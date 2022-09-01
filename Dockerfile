ARG BASE_IMAGE=python:3.8-alpine3.12
FROM ${BASE_IMAGE}

ARG GIT_BRANCH
ARG GIT_COMMIT_ID
ARG GIT_BUILD_TIME
ARG GITHUB_RUN_NUMBER
ARG GITHUB_RUN_ID
ARG PROJECT_URL

LABEL git.branch=${GIT_BRANCH}
LABEL git.commit.id=${GIT_COMMIT_ID}
LABEL git.build.time=${GIT_BUILD_TIME}
LABEL git.run.number=${GITHUB_RUN_NUMBER}
LABEL git.run.id=${GITHUB_RUN_ID}
LABEL org.opencontainers.image.authors="support@sixsq.com"
LABEL org.opencontainers.image.created=${GIT_BUILD_TIME}
LABEL org.opencontainers.image.url=${PROJECT_URL}
LABEL org.opencontainers.image.vendor="SixSq SA"
LABEL org.opencontainers.image.title="NuvlaEdge Peripheral Manager Modbus"
LABEL org.opencontainers.image.description="Finds and identifies Modbus peripherals around the NuvlaEdge"

RUN apk update && apk --no-cache add nmap nmap-scripts

COPY code/ LICENSE /opt/nuvlaedge/

WORKDIR /opt/nuvlaedge/

RUN pip install -r requirements.txt

RUN wget https://svn.nmap.org/nmap/scripts/modbus-discover.nse && \
    wget https://raw.githubusercontent.com/mbs38/spicierModbus2mqtt/master/modbus2mqtt/addToHomeAssistant.py && \
    wget https://raw.githubusercontent.com/mbs38/spicierModbus2mqtt/master/modbus2mqtt/modbus2mqtt.py

RUN rm -rf /var/cache/apk/*

ONBUILD RUN ./license.sh

ENTRYPOINT ["./modbus.py"]
