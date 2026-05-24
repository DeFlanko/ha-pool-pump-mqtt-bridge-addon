FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir paho-mqtt

COPY run.sh /run.sh
COPY pentair_bridge.py /app/pentair_bridge.py

RUN chmod a+x /run.sh

CMD [ "/run.sh" ]