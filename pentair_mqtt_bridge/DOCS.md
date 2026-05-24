# Pentair MQTT Bridge

This add-on bridges a Pentair IntelliFlo pump over MQTT using a USR-DR164 or similar serial-to-MQTT transport.

## Features

- Sends Pentair RS-485 frames over MQTT
- Polls pump status automatically
- Decodes status responses
- Publishes parsed values as MQTT topics
- Accepts MQTT command topics for pump control

## MQTT topics

### Raw transport
- Up topic: configurable, default `D4AD20CF144A/up`
- Down topic: configurable, default `D4AD20CF144A/down`

### Parsed status
- `pentair/pump/status/json`
- `pentair/pump/status/rpm`
- `pentair/pump/status/watts`
- `pentair/pump/status/run`
- `pentair/pump/status/mode`
- `pentair/pump/status/drive_state`
- `pentair/pump/status/timer`
- `pentair/pump/status/clock`

### Commands
- `pentair/pump/cmd/status`
- `pentair/pump/cmd/off`
- `pentair/pump/cmd/low`
- `pentair/pump/cmd/high`
- `pentair/pump/cmd/rpm`

For `pentair/pump/cmd/rpm`, publish an integer payload such as `2200`.

## Configuration

- `broker`: MQTT broker hostname or IP
- `port`: MQTT broker port
- `username`: MQTT username
- `password`: MQTT password
- `topic_up`: raw receive topic
- `topic_down`: raw send topic
- `parsed_base`: base topic for parsed status
- `cmd_base`: base topic for command topics
- `ctrl_addr`: Pentair controller address
- `pump_addr`: Pentair pump address
- `low_rpm`: RPM used for low command
- `high_rpm`: RPM used for high command
- `status_poll_interval`: auto-poll interval in seconds

## Install

1. Add this repository to Home Assistant Add-on Store as a custom repository.
2. Install the `Pentair MQTT Bridge` add-on.
3. Configure broker settings and topics.
4. Start the add-on.
5. Verify MQTT topics are being published.

## Notes

- This add-on does not include Home Assistant MQTT discovery yet.
- Ensure RS-485 polarity is correct.
- Ensure your transport device is subscribed/publishing to the configured raw topics.