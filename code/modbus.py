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
import xmltodict
import logging
import sys
import requests
import time
from threading import Event

KUBERNETES_SERVICE_HOST = os.getenv('KUBERNETES_SERVICE_HOST')
namespace = os.getenv('MY_NAMESPACE', 'nuvlabox')
agent_dns_name = 'agent' if not KUBERNETES_SERVICE_HOST else f'agent.{namespace}'
api_endpoint = f"http://{agent_dns_name}/api/peripheral"


def init_logger():
    """ Initializes logging """

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(levelname)s - %(funcName)s - %(message)s')
    handler.setFormatter(formatter)
    root.addHandler(handler)


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


def wait_for_bootstrap():
    """ Simply waits for the NuvlaBox to finish bootstrapping, by pinging the Agent API

    :returns
    """

    logging.info("Checking if NuvlaBox has been initialized...")

    healthcheck_endpoint = f"http://{agent_dns_name}/api/healthcheck"

    while True:
        try:
            r = requests.get(healthcheck_endpoint)
        except requests.exceptions.ConnectionError as e:
            logging.warning(f'Unable to establish connection with NuvlaBox Agent: {e}. Will keep trying...')
            time.sleep(5)
            continue

        if r.ok:
            break

    return


def manage_modbus_peripherals(peripherals):
    """ Takes care of posting or deleting the respective
    NB peripheral resources from Nuvla

    :param peripherals_path: base path for the NB shared directory
    :param peripherals: list of dict, one for each discovered modbus slave
    """

    # local file naming convention:
    #    modbus.{port}.{interface}.{identifier}

    modbus_identifier_pattern = "modbus.*"
    # Ask the NB agent for all modbus peripherals matching this pattern
    get_modbus_peripherals = requests.get(api_endpoint + '?identifier_pattern=' + modbus_identifier_pattern)
    if not get_modbus_peripherals.ok or not isinstance(get_modbus_peripherals.json(), list):
        return

    local_modbus_peripherals = get_modbus_peripherals.json()
    for per in peripherals:
        port = per.get("port", "nullport")
        interface = per.get("interface", "nullinterface")
        identifier = "modbus.{}.{}.{}".format(port, interface, per.get("identifier"))
        # Redefine the identifier
        per['identifier'] = identifier

        if identifier in local_modbus_peripherals:
            # device is discovered, and already reported, so nothing to do here
            local_modbus_peripherals.remove(identifier)
        else:
            # filename is not saved locally, which means it is newly discovered
            # thus report to Nuvla
            try:
                nuvla_peripheral_id = requests.post(api_endpoint, json=per).json()["resource-id"]

                logging.info("Created new NuvlaBox Modbus peripheral {} with ID {}".format(identifier,
                                                                                           nuvla_peripheral_id))
            except:
                logging.exception("Cannot create Modbus peripheral {} in Nuvla...moving on".format(identifier))

    for old_modbus_file in local_modbus_peripherals:
        # the leftover files are devices that have not been detected anymore,
        # and thus should be deleted
        r = requests.delete(api_endpoint + "/" + old_modbus_file)


if __name__ == "__main__":

    init_logger()

    wait_for_bootstrap()

    gateway_ip = get_default_gateway_ip()

    # Runs the modbus discovery functions periodically
    e = Event()

    while True:
        xml_file = scan_open_ports(gateway_ip)
        with open(xml_file) as ox:
            namp_xml_output = ox.read()

        all_modbus_devices = parse_modbus_peripherals(namp_xml_output)

        manage_modbus_peripherals(all_modbus_devices)

        e.wait(timeout=90)


