# Pentair MQTT Bridge

The **Pentair MQTT Bridge** add-on connects a Pentair IntelliFlo-style pump to Home Assistant using MQTT and an MQTT-connected RS-485 transport device such as a USR-DR164.

This add-on:

- sends Pentair RS-485 frames over MQTT
- polls pump status automatically
- decodes pump status responses
- publishes parsed values back to MQTT
- listens for MQTT command topics to control the pump

---

## Requirements

Before using this add-on, make sure you have:

1. A working MQTT broker
2. A Pentair pump connected over RS-485
3. An MQTT-connected serial/RS-485 bridge
4. Verified raw MQTT transport topics for the bridge

Typical raw topics look like:

- `D4AD20CF144A/up`
- `D4AD20CF144A/down`

---

## Add-on configuration

Example configuration:

```yaml
broker: 192.168.1.10
port: 1883
username: mqtt_user
password: your_password
topic_up: D4AD20CF144A/up
topic_down: D4AD20CF144A/down
parsed_base: pentair/pump/status
cmd_base: pentair/pump/cmd
ctrl_addr: 33
pump_addr: 96
low_rpm: 1650
high_rpm: 3000
status_poll_interval_seconds: 15
status_poll_mode: active
control_mode: on_demand
control_release_seconds: 60
min_command_interval_seconds: 1.0
```

### Configuration options

#### `broker`
Hostname or IP address of your MQTT broker.

#### `port`
MQTT broker port. Usually `1883`.

#### `username`
MQTT username, if required.

#### `password`
MQTT password, if required.

#### `topic_up`
MQTT topic where the RS-485 transport publishes raw Pentair responses.

#### `topic_down`
MQTT topic where this add-on publishes raw Pentair commands.

#### `parsed_base`
Base topic used for decoded pump status topics.

Default:

```text
pentair/pump/status
```

#### `cmd_base`
Base topic used for MQTT control commands.

Default:

```text
pentair/pump/cmd
```

#### `ctrl_addr`
Pentair controller address in decimal.

Default:

```text
33
```

#### `pump_addr`
Pentair pump address in decimal.

Default:

```text
96
```

#### `low_rpm`
RPM used when sending the `low` command.

#### `high_rpm`
RPM used when sending the `high` command.

#### `status_poll_interval_seconds`
How often the add-on polls the pump for status when `status_poll_mode` is `active`, in seconds.

Default: `15`

---

## Control mode options

### `control_mode`

Controls how the bridge behaves after sending a command to the pump.

| Value | Behavior |
|---|---|
| `on_demand` | **(default, recommended)** After a command is sent, the bridge holds control for `control_release_seconds`, then stops reasserting and returns to read-only polling. This allows the local Pentair keypad to resume control after the hold window expires. |
| `continuous` | The bridge continuously reasserts control (original behavior). |

If an invalid value is supplied, the bridge falls back to `on_demand` and logs a warning.

### `control_release_seconds`

How many seconds after a control command is sent before the bridge releases control and returns to read-only polling.

Default: `60`

Only applies when `control_mode` is `on_demand`.

### `min_command_interval_seconds`

Minimum time in seconds between consecutive control commands. Commands arriving faster than this rate are dropped with a log message.

Default: `1.0`

---

## Status polling options

### `status_poll_mode`

Controls whether the bridge actively sends AUTO STATUS requests to the pump.

| Value | Behavior |
|---|---|
| `active` | **(default)** Sends a Pentair AUTO STATUS frame to the pump every `status_poll_interval_seconds`. This is the original behavior. |
| `passive` | **Never** sends AUTO STATUS frames. Telemetry is published only when the pump sends uplink RS-485 frames that arrive on the `topic_up` MQTT topic. |

> **Keypad usability note:** Some Pentair pump firmware versions treat any RS-485 frame sent by an external controller — including the AUTO STATUS poll — as evidence of an active automation system. This causes the local keypad to display **"Display Not Active"** and prevents manual keypad operation while the integration is running.
>
> If you experience this, set `status_poll_mode: passive`. In passive mode, the bridge never transmits status requests. The local keypad remains usable at all times, and telemetry is still published to Home Assistant whenever the pump sends its own periodic uplink status frames.
>
> **Trade-off:** In passive mode, telemetry updates depend on the pump generating its own bus traffic. If the pump is idle and produces no uplink frames, telemetry will not update until the pump sends a frame on its own or a remote command is sent.

If an invalid value is supplied, the bridge falls back to `active` and logs a warning.

### `status_poll_interval_seconds`

How often (in seconds) the bridge sends an AUTO STATUS poll to the pump when `status_poll_mode` is `active`.

Default: `15`

Has no effect when `status_poll_mode` is `passive`.

---

## MQTT topics

### Parsed status topics

The add-on publishes decoded pump data to these topics:

- `pentair/pump/status/json`
- `pentair/pump/status/rpm`
- `pentair/pump/status/watts`
- `pentair/pump/status/run`
- `pentair/pump/status/mode`
- `pentair/pump/status/drive_state`
- `pentair/pump/status/timer`
- `pentair/pump/status/clock`

If you change `parsed_base`, these topic paths will change accordingly.

### Command topics

The add-on listens on:

- `pentair/pump/cmd/status`
- `pentair/pump/cmd/off`
- `pentair/pump/cmd/low`
- `pentair/pump/cmd/high`
- `pentair/pump/cmd/rpm`

If you change `cmd_base`, these topic paths will change accordingly.

#### Example commands

Request status:

- Topic: `pentair/pump/cmd/status`
- Payload: `1`

Turn pump off:

- Topic: `pentair/pump/cmd/off`
- Payload: `1`

Set low speed:

- Topic: `pentair/pump/cmd/low`
- Payload: `1`

Set high speed:

- Topic: `pentair/pump/cmd/high`
- Payload: `1`

Set a specific RPM:

- Topic: `pentair/pump/cmd/rpm`
- Payload: `2200`

---

## Installation

1. Add this repository to Home Assistant as a custom add-on repository
2. Install the **Pentair MQTT Bridge** add-on
3. Enter your MQTT and topic configuration
4. Start the add-on
5. Watch the add-on logs to confirm successful MQTT connection
6. Verify decoded topics are being published

---

## First-time setup checklist

- Confirm the MQTT broker is reachable
- Confirm the username/password are correct
- Confirm `topic_up` and `topic_down` match your RS-485 bridge
- Confirm RS-485 polarity is correct
- Confirm the pump responds to status polling
- Confirm parsed status topics appear in MQTT Explorer or Home Assistant

---

## Troubleshooting

### Add-on starts but no status data appears
Check:

- MQTT broker address and port
- MQTT credentials
- raw topic names
- RS-485 wiring polarity
- whether your transport device is actually receiving pump traffic

### Status works but commands do not
Check:

- `cmd_base` topic path
- RPM values are in a valid range
- your RS-485 transport is forwarding command frames correctly
- pump write/control behavior is supported in your environment

### Home Assistant cannot add the repository
Make sure the repository root contains a file named exactly:

```text
repository.yaml
```

If it is misspelled, Home Assistant will not recognize the repo properly.

---

## Notes

- This add-on currently publishes MQTT topics but does not yet create Home Assistant entities automatically through MQTT discovery.
- MQTT discovery can be added in a future version.
- Broker credentials are supplied through add-on options and are not hardcoded in the add-on source.

---

## Support

If the add-on connects to MQTT but the pump does not respond correctly, start by verifying:

1. RS-485 polarity
2. raw MQTT topic names
3. pump address and controller address
4. the bridge device is passing raw bytes unchanged
