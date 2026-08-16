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
speed1_rpm: 1650
speed2_rpm: 2000
speed3_rpm: 2500
speed4_rpm: 3450
poll_interval_minutes: 15
control_release_seconds: 60
min_command_interval_seconds: 1.0
cleaning_mode: false
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

#### `speed1_rpm`
RPM used when pressing the **Speed 1** button (or sending to `cmd_base/speed/1`).

Default: `1650`

#### `speed2_rpm`
RPM used when pressing the **Speed 2** button (or sending to `cmd_base/speed/2`).

Default: `2000`

#### `speed3_rpm`
RPM used when pressing the **Speed 3** button (or sending to `cmd_base/speed/3`).

Default: `2500`

#### `speed4_rpm`
RPM used when pressing the **Speed 4** button (or sending to `cmd_base/speed/4`).

Default: `3450`

#### `low_rpm` / `high_rpm`
Legacy RPM values kept for backward compatibility with the `cmd_base/low` and `cmd_base/high` topics. New installs should use `speed1_rpm`–`speed4_rpm` instead.

#### `poll_interval_minutes`
How often the add-on schedules an active poll refresh window, in minutes.

Default: `15`

- `15` = check the pump every 15 minutes
- `0` = continuous polling while an active polling window is running

Each active poll refresh window lasts 5 seconds and then the bridge automatically returns to passive mode.

> **Panel lock tip:** Polling too frequently causes the pump display to show **"Display Not Active"** and prevents manual keypad operation. The default 15-minute interval is a good balance. Use **cleaning mode** during manual maintenance sessions to suspend polling entirely.

---

## Control behavior

Control mode is no longer configurable. The bridge always uses **on-demand** control behavior.

After a command is sent, the bridge holds control for `control_release_seconds`, then stops reasserting and returns to read-only behavior. This keeps local Pentair keypad control available once the hold window expires.

### `control_release_seconds`

How many seconds after a control command is sent before the bridge releases control and returns to read-only polling.

Default: `60`

### `min_command_interval_seconds`

Minimum time in seconds between consecutive control commands. Commands arriving faster than this rate are dropped with a log message.

Default: `1.0`

---

## Status polling behavior

Polling mode is no longer configurable. The bridge now behaves as follows:

- Starts in **ACTIVE** mode for the first **5 seconds** after startup
- Returns to **PASSIVE** mode automatically after that window closes
- Re-enters a short active refresh window on reconnect and when cleaning mode is disabled
- Uses `poll_interval_minutes` to determine how often a new active refresh window is scheduled
- Does **not** transmit AUTO STATUS poll frames while passive

> **Keypad usability note:** Some Pentair pump firmware versions treat any RS-485 frame sent by an external controller — including the AUTO STATUS poll — as evidence of an active automation system. This causes the local keypad to display **"Display Not Active"** and prevents manual keypad operation while the integration is running.
>
> The add-on now defaults to passive behavior outside of short refresh windows, so the keypad remains usable most of the time. Use **cleaning mode** (see below) during maintenance periods if you want the bridge to stay fully silent.
>
> **Trade-off:** In passive mode, telemetry updates depend on the pump generating its own bus traffic. If the pump is idle and produces no uplink frames, telemetry will not update until the pump sends a frame on its own or a remote command is sent.

---

## Cleaning mode

Cleaning mode suspends all active polling so the physical pump panel remains freely usable during manual maintenance (e.g., vacuuming, backwash, filter cleaning).

### `cleaning_mode` (config option)

Set to `true` to start the add-on with polling suspended.

Default: `false`

### Runtime control via MQTT

You can toggle cleaning mode at any time without restarting the add-on by publishing to:

```
pentair/pump/cmd/set/cleaning_mode
```

Accepted payloads: `ON`, `OFF`, `1`, `0`, `TRUE`, `FALSE`, `YES`, `NO` (case-insensitive).

**Enable cleaning mode** (suspend polling):
```
Topic:   pentair/pump/cmd/set/cleaning_mode
Payload: ON
```

**Disable cleaning mode** (resume polling):
```
Topic:   pentair/pump/cmd/set/cleaning_mode
Payload: OFF
```

When cleaning mode is disabled, the bridge immediately sends a status poll so Home Assistant reflects the current pump state without waiting for the next scheduled interval.

The current cleaning mode state is published (retained) to:

```
pentair/pump/status/cleaning_mode
```

In Home Assistant, the MQTT discovery switch entity **Cleaning Mode** lets you toggle this from the UI or an automation.

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
- `pentair/pump/status/schedule_enabled` — `ON` or `OFF`; derived from the run-byte schedule flag
- `pentair/pump/status/cleaning_mode` — `ON` or `OFF`; current polling state
- `pentair/pump/status/last_poll_epoch` — Unix epoch seconds for the latest active poll refresh
- `pentair/pump/status/last_poll_local` — local timezone ISO-8601 timestamp for the latest active poll refresh

#### Speed preset topics

When the pump is running on a numbered speed preset (Speed 1–4 buttons), the active RPM is published to:

- `pentair/pump/status/speed/1/rpm`
- `pentair/pump/status/speed/2/rpm`
- `pentair/pump/status/speed/3/rpm`
- `pentair/pump/status/speed/4/rpm`

These topics are updated only when the pump reports operating on the corresponding speed slot. They retain their last published value so HA always has a reference for each configured speed.

If you change `parsed_base`, all topic paths above will change accordingly.

#### Last poll telemetry sensors

When MQTT discovery is enabled, Home Assistant gets:

- a diagnostic timestamp sensor for `last_poll_local`
- a diagnostic sensor for `last_poll_epoch`

These update whenever the bridge sends an active poll refresh.

Add-on log timestamps also use the detected local timezone, and startup logs include the timezone / UTC offset for verification.

### Command topics

The add-on listens on:

- `pentair/pump/cmd/status`
- `pentair/pump/cmd/off`
- `pentair/pump/cmd/speed/1`
- `pentair/pump/cmd/speed/2`
- `pentair/pump/cmd/speed/3`
- `pentair/pump/cmd/speed/4`
- `pentair/pump/cmd/rpm`
- `pentair/pump/cmd/set/cleaning_mode`
- `pentair/pump/cmd/low` *(backward-compatible alias → Speed 1 RPM)*
- `pentair/pump/cmd/high` *(backward-compatible alias → Speed 4 RPM)*

If you change `cmd_base`, these topic paths will change accordingly.

#### Example commands

Request status:

- Topic: `pentair/pump/cmd/status`
- Payload: `1`

Turn pump off:

- Topic: `pentair/pump/cmd/off`
- Payload: `1`

Set Speed 1:

- Topic: `pentair/pump/cmd/speed/1`
- Payload: `1`

Set Speed 2:

- Topic: `pentair/pump/cmd/speed/2`
- Payload: `1`

Set Speed 3:

- Topic: `pentair/pump/cmd/speed/3`
- Payload: `1`

Set Speed 4:

- Topic: `pentair/pump/cmd/speed/4`
- Payload: `1`

Set a specific RPM:

- Topic: `pentair/pump/cmd/rpm`
- Payload: `2200`

Enable cleaning mode:

- Topic: `pentair/pump/cmd/set/cleaning_mode`
- Payload: `ON`

Disable cleaning mode:

- Topic: `pentair/pump/cmd/set/cleaning_mode`
- Payload: `OFF`

---

## Schedule enabled state

The pump's schedule-running flag is exposed as a read-only binary sensor:

- **MQTT topic:** `pentair/pump/status/schedule_enabled`
- **Values:** `ON` (schedule active) / `OFF` (schedule not active)
- **HA entity:** `binary_sensor.pump_schedule_enabled` (via MQTT discovery)

This value is derived from bit 2 of the run byte in the pump status response. It reflects whether the pump's internal schedule is currently driving operation.

> **Note:** Writing to the schedule (enable/disable via RS-485 command) requires hardware validation of the specific write register for your pump model and is not implemented in the current version. This is a safe, read-only addition.

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

### Deprecated polling options still appear in my stored config

Older stored configurations may still contain:

- `control_mode`
- `status_poll_mode`
- `status_poll_interval_seconds`
- `status_poll_interval`

The add-on accepts those keys for backward compatibility, logs that they are deprecated, and ignores them. Use `poll_interval_minutes` going forward.

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

- Broker credentials are supplied through add-on options and are not hardcoded in the add-on source.

---

## Support

If the add-on connects to MQTT but the pump does not respond correctly, start by verifying:

1. RS-485 polarity
2. raw MQTT topic names
3. pump address and controller address
4. the bridge device is passing raw bytes unchanged
