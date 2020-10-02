FROM python:3-alpine

ARG GIT_BRANCH
ARG GIT_COMMIT_ID
ARG GIT_DIRTY
ARG GIT_BUILD_TIME
ARG TRAVIS_BUILD_NUMBER
ARG TRAVIS_BUILD_WEB_URL

LABEL git.branch=${GIT_BRANCH}
LABEL git.commit.id=${GIT_COMMIT_ID}
LABEL git.dirty=${GIT_DIRTY}
LABEL git.build.time=${GIT_BUILD_TIME}
LABEL travis.build.number=${TRAVIS_BUILD_NUMBER}
LABEL travis.build.web.url=${TRAVIS_BUILD_WEB_URL}

RUN apk update && apk --no-cache add nmap=7.80-r2 nmap-scripts=7.80-r2

COPY code/ LICENSE /opt/nuvlabox/

WORKDIR /opt/nuvlabox/

RUN pip install -r requirements.txt

RUN wget https://svn.nmap.org/nmap/scripts/modbus-discover.nse && \
    wget https://raw.githubusercontent.com/mbs38/spicierModbus2mqtt/master/addToHomeAssistant.py && \
    wget https://raw.githubusercontent.com/mbs38/spicierModbus2mqtt/master/modbus2mqtt.py

RUN rm -rf /var/cache/apk/*

ONBUILD RUN ./license.sh

ENTRYPOINT ["./modbus.py"]