# Home Assistant Pentair Pool Pump MQTT Bridge Add-on

A Home Assistant custom add-on that bridges a Pentair IntelliFlo/RS-485 pool pump over MQTT, decodes status responses, and exposes simple MQTT command topics for control.

## Features

- Connects to an MQTT broker
- Sends Pentair RS-485 frames over MQTT transport topics
- Polls pump status automatically with an active startup refresh window, then passive monitoring
- Decodes status responses
- Publishes parsed values back to MQTT as JSON and scalar topics
- Accepts MQTT command topics for pump control
- **Cleaning mode** — pause polling so the physical panel is freely usable during maintenance
- **Schedule enabled** — read-only sensor showing whether the pump's internal schedule is active
- **Speed 1–4 preset visibility** — publishes the RPM configured for each speed button
- **Last poll telemetry** — publishes the last active poll refresh time as local ISO-8601 and Unix epoch
- Designed for Home Assistant as a custom add-on

## Repository structure

```text
repository.yaml
pentair_mqtt_bridge/
  config.yaml
  Dockerfile
  run.sh
  pentair_bridge.py
  DOCS.md
```

## Add this add-on to Home Assistant

1. Open **Home Assistant**
2. Go to **Settings → Add-ons**
3. Open the **Add-on Store**
4. Click the **three-dot menu** in the upper-right
5. Choose **Repositories**
6. Add this repository URL:

```text
https://github.com/DeFlanko/ha-pool-pump-mqtt-bridge-addon
```

7. Click **Add**
8. Find **Pentair MQTT Bridge**
9. Install the add-on
10. Configure the options
11. Start the add-on

## Example add-on configuration

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
poll_interval_minutes: 15
control_release_seconds: 60
min_command_interval_seconds: 1.0
cleaning_mode: false
```

## MQTT topics

### Raw transport topics
- `D4AD20CF144A/up`
- `D4AD20CF144A/down`

These are configurable in the add-on options.

### Parsed status topics
- `pentair/pump/status/json`
- `pentair/pump/status/rpm`
- `pentair/pump/status/watts`
- `pentair/pump/status/run`
- `pentair/pump/status/mode`
- `pentair/pump/status/drive_state`
- `pentair/pump/status/timer`
- `pentair/pump/status/clock`
- `pentair/pump/status/schedule_enabled` — `ON`/`OFF`
- `pentair/pump/status/cleaning_mode` — `ON`/`OFF`
- `pentair/pump/status/last_poll_epoch`
- `pentair/pump/status/last_poll_local`
- `pentair/pump/status/speed/1/rpm` through `pentair/pump/status/speed/4/rpm`

### Command topics
- `pentair/pump/cmd/status`
- `pentair/pump/cmd/off`
- `pentair/pump/cmd/low`
- `pentair/pump/cmd/high`
- `pentair/pump/cmd/rpm`
- `pentair/pump/cmd/set/cleaning_mode`

Example:
- publish `2200` to `pentair/pump/cmd/rpm`
- publish `ON` to `pentair/pump/cmd/set/cleaning_mode` to suspend polling

## Polling behavior

- Control mode is always **on-demand**. After a remote command, the bridge holds control for `control_release_seconds` and then returns to read-only behavior so the local keypad becomes usable again.
- Polling starts in **ACTIVE** mode for the first **5 seconds** after startup or an immediate refresh trigger (for example reconnect or cleaning mode being disabled), then returns to **PASSIVE** mode automatically.
- `poll_interval_minutes` is the only polling cadence setting:
  - `15` by default
  - `0` means continuous polling **while the bridge is in an active polling window**
- During passive mode, the bridge does **not** transmit AUTO STATUS poll frames.

> **Local keypad tip:** Some Pentair pump firmware versions lock the keypad and show **"Display Not Active"** whenever any RS-485 frame is transmitted by an external device. The add-on now minimizes that by using a short active refresh window and passive monitoring the rest of the time. Use **Cleaning Mode** during manual maintenance sessions to keep the bus silent.

## Last poll telemetry

- `pentair/pump/status/last_poll_epoch` — Unix epoch seconds for the most recent active poll refresh
- `pentair/pump/status/last_poll_local` — local timezone ISO-8601 timestamp, for example `2026-08-16T14:22:31-04:00`
- When MQTT discovery is enabled, Home Assistant gets diagnostic sensors for both values, including a timestamp sensor for `last_poll_local`
- Add-on log timestamps now use the detected local timezone, and startup logs include the timezone / UTC offset for quick verification

## Cleaning mode

Enable Cleaning Mode to pause all polling and keep the physical pump panel free for manual use:

- Config option: `cleaning_mode: true` (start suspended)
- MQTT command: publish `ON` to `pentair/pump/cmd/set/cleaning_mode`
- HA entity: **Cleaning Mode** switch (via MQTT discovery)

When disabled, the bridge immediately polls for fresh status.

## Migration notes

- Removed user-facing options: `control_mode`, `status_poll_mode`, `status_poll_interval_seconds`, and legacy `status_poll_interval`
- Existing stored configs that still contain those keys are accepted, but the add-on logs a warning and ignores them
- Use `poll_interval_minutes` instead for polling cadence

See `DOCS.md` in the add-on for full option descriptions.

## Notes

- This add-on expects an MQTT-connected RS-485 transport such as a USR-DR164 configured to exchange raw Pentair frames.
- Broker credentials are provided through add-on configuration, not hardcoded in the source.
- If Home Assistant does not show the repository, confirm the root file is named exactly `repository.yaml`.
- Schedule write (enable/disable via command) is not implemented in the current version pending hardware validation.
