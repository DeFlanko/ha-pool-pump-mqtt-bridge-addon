#!/usr/bin/with-contenv bash
set -e

source /usr/lib/bashio/bashio.sh

bashio::log.info "Starting Pentair MQTT Bridge"

export BROKER="$(bashio::config 'broker')"
export PORT="$(bashio::config 'port')"
export USERNAME="$(bashio::config 'username')"
export PASSWORD="$(bashio::config 'password')"
export TOPIC_UP="$(bashio::config 'topic_up')"
export TOPIC_DOWN="$(bashio::config 'topic_down')"
export PARSED_BASE="$(bashio::config 'parsed_base')"
export CMD_BASE="$(bashio::config 'cmd_base')"
export DISCOVERY_BASE="$(bashio::config 'discovery_base')"
export DISCOVERY_PREFIX="$(bashio::config 'discovery_prefix')"
export CTRL_ADDR="$(bashio::config 'ctrl_addr')"
export PUMP_ADDR="$(bashio::config 'pump_addr')"
export LOW_RPM="$(bashio::config 'low_rpm')"
export HIGH_RPM="$(bashio::config 'high_rpm')"
export STATUS_POLL_INTERVAL="$(bashio::config 'status_poll_interval')"
export ENABLE_DISCOVERY="$(bashio::config 'enable_discovery')"

exec python3 /app/pentair_bridge.py
