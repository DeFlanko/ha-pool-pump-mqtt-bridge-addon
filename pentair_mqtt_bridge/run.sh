#!/bin/bash
set -e

echo "[INFO] Starting Pentair MQTT Bridge add-on"

exec python3 /app/pentair_bridge.py
