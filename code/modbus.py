#!/usr/bin/env python3

# -*- coding: utf-8 -*-

"""NuvlaBox Peripheral Manager Modbus

This service takes care of the discovery and overall
management of Modbus peripherals that are attached to
the NuvlaBox.

It provides:
 - automatic discovery and reporting of Modbus peripherals
 - data gateway capabilities for Modbus (see docs)
 - modbus2mqtt on demand
"""

import socket
import struct
import os
import glob
import xmltodict
import logging
import sys
import json
import requests
import nuvla.api
import time
from threading import Event


nuvla_endpoint_raw = os.environ["NUVLA_ENDPOINT"] if "NUVLA_ENDPOINT" in os.environ else "nuvla.io"
while nuvla_endpoint_raw[-1] == "/":
    nuvla_endpoint_raw = nuvla_endpoint_raw[:-1]

nuvla_endpoint = nuvla_endpoint_raw.replace("https://", "")

nuvla_endpoint_insecure_raw = os.environ["NUVLA_ENDPOINT_INSECURE"] if "NUVLA_ENDPOINT_INSECURE" in os.environ else False
if isinstance(nuvla_endpoint_insecure_raw, str):
    if nuvla_endpoint_insecure_raw.lower() == "false":
        nuvla_endpoint_insecure_raw = False
    else:
        nuvla_endpoint_insecure_raw = True
else:
    nuvla_endpoint_insecure_raw = bool(nuvla_endpoint_insecure_raw)

nuvla_endpoint_insecure = nuvla_endpoint_insecure_raw


def init_logger():
    """ Initializes logging """

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(levelname)s - %(funcName)s - %(message)s')
    handler.setFormatter(formatter)
    root.addHandler(handler)


def authenticate(api_key, secret_key):
    """ Creates a user session

    :param api_key: API key
    :param secret_key: API secret

    :returns authenticated API object instance
    """

    api_instance = nuvla.api.Api(endpoint='https://{}'.format(nuvla_endpoint),
                       insecure=nuvla_endpoint_insecure, reauthenticate=True)
    logging.info('Authenticate with "{}"'.format(api_key))
    r = api_instance.login_apikey(api_key, secret_key)
    if r.status_code == 403:
        logging.error("Cannot login into Nuvla with API key {}".format(api_key))
        sys.exit(1)

    return api_instance


def get_default_gateway_ip():
    """ Get the default gateway IP

    :returns IP of the default gateway
    """

    logging.info("Retrieving gateway IP...")

    with open("/proc/net/route") as route:
        for line in route:
            fields = line.strip().split()
            if fields[1] != '00000000' or not int(fields[3], 16) & 2:
                continue

            return socket.inet_ntoa(struct.pack("<L", int(fields[2], 16)))


def scan_open_ports(host, modbus_nse="modbus-discover.nse", xml_file="/tmp/nmap_scan.xml"):
    """ Uses nmap to scan all the open ports in the NuvlaBox.
        Writes the output into an XML file.

    :param host: IP of the host to be scanned
    :param modbus_nse: nmap NSE script for modbus service discovery
    :param xml_file: XML filename where to write the nmap output

    :returns XML filename where to write the nmap output
    """

    logging.info("Scanning open ports...")

    command = "nmap --script {} --script-args='modbus-discover.aggressive=true' -p- {} -T4 -oX {} &>/dev/null"\
        .format(modbus_nse,
                host,
                xml_file)

    os.system(command)

    return xml_file


def parse_modbus_peripherals(namp_xml_output):
    """ Uses the output from the nmap port scan to find modbus
    services.
    Plain output example:

        PORT    STATE SERVICE
        502/tcp open  modbus
        | modbus-discover:
        |   sid 0x64:
        |     Slave ID data: \xFA\xFFPM710PowerMeter
        |     Device identification: Schneider Electric PM710 v03.110
        |   sid 0x96:
        |_    error: GATEWAY TARGET DEVICE FAILED TO RESPONSE

    :returns List of modbus devices"""

    namp_odict = xmltodict.parse(namp_xml_output, process_namespaces=True)

    modbus = []
    try:
        all_ports = namp_odict['nmaprun']['host']['ports']['port']
    except KeyError:
        logging.warning("Cannot find any open ports in this NuvlaBox")
        return modbus
    except:
        logging.exception("Unknown error while processing ports scan")
        return modbus

    for port in all_ports:
        if 'service' not in port or port['service']['@name'] != "modbus":
            continue

        modbus_device_base = {
            "interface": port['@protocol'].upper() if "@protocol" in port else None,
            "port": int(port["@portid"]) if "@portid" in port else None,
            "available": True if port['state']['@state'] == "open" else False
        }

        output = port['script']['table']
        if not isinstance(output, list):
            output = [output]

        for address in output:
            slave_id = int(address['@key'].split()[1], 16)
            elements_list = address['elem']
            classes = None
            device_identification = None
            for elem in elements_list:
                if elem['@key'] == "Slave ID data":
                    classes = [str(elem.get('#text'))]
                elif elem['@key'] == 'Device identification':
                    device_identification = elem.get('#text')
                else:
                    logging.warning("Modbus device with slave ID {} cannot be categorized: {}").format(slave_id,
                                                                                                       elem)
            modbus_device_merge = { **modbus_device_base,
                                    "classes": classes,
                                    "identifier": str(slave_id),
                                    "vendor": device_identification,
                                    "name": "Modbus {}/{} {} - {}".format(modbus_device_base['port'], port.get('@protocol'),
                                                                          ' '.join(classes),
                                                                          slave_id)
                                    }

            modbus_device_final = {k: v for k, v in modbus_device_merge.items() if v is not None}

            # add final modbus device to list of devices
            modbus.append(modbus_device_final)

            logging.info("modbus device found {}".format(modbus_device_final))

    return modbus


def wait_for_bootstrap(shared):
    """ Simply waits for the NuvlaBox to finish bootstrapping

    :param shared: base path for the shared NB directory
    :returns path for the activation file where Nuvla credentials are
    :returns path for the folder where all the peripheral files are saved
    """

    peripherals_dir = "{}/.peripherals".format(shared)
    activated = "{}/.activated".format(shared)
    context = "{}/.context".format(shared)

    logging.info("Checking if NuvlaBox has been initialized...")
    while not os.path.isdir(peripherals_dir):
        continue

    while not os.path.isfile(activated):
        continue

    while not os.path.isfile(context):
        continue

    return activated, peripherals_dir, context


def manage_modbus_peripherals(peripherals_path, peripherals, api, nb_context):
    """ Takes care of posting or deleting the respective
    NB peripheral resources from Nuvla

    :param peripherals_path: base path for the NB shared directory
    :param peripherals: list of dict, one for each discovered modbus slave
    :param api: authenticated api obj instance for posting and deleting Nuvla resources
    """

    # local file naming convention:
    #    modbus.{port}.{interface}.{identifier}

    nb_id = nb_context["id"]
    nb_version = nb_context["version"]

    modbus_files_basepath = "{}/modbus.".format(peripherals_path)
    local_modbus_files = glob.glob(modbus_files_basepath+'*')

    for per in peripherals:
        port = per.get("port", "nullport")
        interface = per.get("interface", "nullinterface")
        identifier = per.get("identifier")

        filename_termination = "{}.{}.{}".format(port, interface, identifier)
        full_filename = "{}{}".format(modbus_files_basepath, filename_termination)
        if full_filename in local_modbus_files:
            # device is discovered, and already reported, so nothing to do here
            local_modbus_files.remove(full_filename)
        else:
            # filename is not saved locally, which means it is newly discovered
            # thus report to Nuvla
            payload = {**per, "parent": nb_id, "version": nb_version}
            try:
                nuvla_peripheral_post_response = api._cimi_post("nuvlabox-peripheral", json=payload)
                nuvla_peripheral_id = nuvla_peripheral_post_response["resource-id"]
                with open(full_filename, 'w') as mf:
                    mf.write(json.dumps({**payload, "id": nuvla_peripheral_id}))

                logging.info("Created new NuvlaBox Modbus peripheral {} with ID {}".format(filename_termination,
                                                                                           nuvla_peripheral_id))
            except:
                logging.exception("Cannot create Modbus peripheral {} in Nuvla...moving on".format(filename_termination))

    for old_modbus_file in local_modbus_files:
        # the leftover files are devices that have not been detected anymore,
        # and thus should be deleted
        try:
            with open(old_modbus_file) as omf:
                nuvla_peripheral_id_rm = json.loads(omf.read())["id"]
        except FileNotFoundError:
            logging.warning("{} not found locally. Obsolete Nuvla peripheral resource might not be cleaned properly"
                            .format(old_modbus_file))
            continue
        except:
            logging.exception("Unknown error while deleting obsolete peripherals...moving on")
            continue

        try:
            api._cimi_delete(nuvla_peripheral_id_rm)
        except requests.exceptions.ConnectionError as conn_err:
            logging.error("Can not reach out to Nuvla. Error: {}".format(conn_err))
            return
        except nuvla.api.api.NuvlaError as e:
            if e.response.status_code == 404:
                logging.info("{} does not exist in Nuvla anymore. Moving on...".format(nuvla_peripheral_id_rm))
            else:
                logging.exception("Unknown Nuvla exception - cannot delete {}".format(nuvla_peripheral_id_rm))
                continue
        except:
            logging.exception("Cannot delete {} from Nuvla. Attempting to edit it...".format(nuvla_peripheral_id_rm))
            try:
                api._cimi_put(nuvla_peripheral_id_rm, json={"available": False})
                continue
            except:
                logging.exception("Cannot modify {} in Nuvla. Will try again later".format(nuvla_peripheral_id_rm))
                continue

        try:
            os.remove(old_modbus_file)
        except FileNotFoundError:
            logging.warning("{} has already been removed. Deletion is complete anyway".format(old_modbus_file))
        except:
            logging.exception("Cannot delete local file {}. Will try again".format(old_modbus_file))


if __name__ == "__main__":

    init_logger()

    shared = "/srv/nuvlabox/shared"
    activation_file, peripherals_dir, context_file = wait_for_bootstrap(shared)

    af = open(activation_file)
    c = open(context_file)
    try:
        api_credential = json.loads(af.read())
        nuvlabox_context = json.loads(c.read())
    except json.decoder.JSONDecodeError as e:
        logging.warning("Possible race condition while reading %s and %s. Re-trying" % (activation_file, context_file))
        time.sleep(1)
        af.seek(0)
        api_credential = json.loads(af.read())
        c.seek(0)
        nuvlabox_context = json.loads(c.read())
    except:
        logging.exception("Cannot read JSON from %s and %s. Exiting..." % (activation_file, context_file))
        sys.exit(1)

    af.close()
    c.close()

    try:
        apikey = api_credential["api-key"]
        apisecret = api_credential["secret-key"]
    except KeyError:
        logging.exception("Cannot load Nuvla credentials")
        sys.exit(1)

    socket.setdefaulttimeout(20)

    api = authenticate(apikey, apisecret)

    gateway_ip = get_default_gateway_ip()

    # Runs the modbus discovery functions periodically
    e = Event()

    while True:
        xml_file = scan_open_ports(gateway_ip)
        with open(xml_file) as ox:
            namp_xml_output = ox.read()

        all_modbus_devices = parse_modbus_peripherals(namp_xml_output)

        manage_modbus_peripherals(peripherals_dir, all_modbus_devices, api, nuvlabox_context)

        e.wait(timeout=90)


