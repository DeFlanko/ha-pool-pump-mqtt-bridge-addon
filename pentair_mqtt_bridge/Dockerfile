FROM ghcr.io/home-assistant/amd64-base:3.19

WORKDIR /app

RUN apk add --no-cache bash python3 py3-pip \
    && pip3 install --break-system-packages --no-cache-dir paho-mqtt

COPY run.sh /run.sh
COPY pentair_bridge.py /app/pentair_bridge.py

RUN chmod +x /run.sh

CMD ["/bin/bash", "/run.sh"]
