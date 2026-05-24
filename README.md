# Home Assistant Pentair Pool Pump MQTT Bridge Add-on

A Home Assistant custom add-on that bridges a Pentair IntelliFlo/RS-485 pool pump over MQTT, decodes status responses, and exposes simple MQTT command topics for control.

## Features

- Connects to an MQTT broker
- Sends Pentair RS-485 frames over MQTT transport topics
- Polls pump status automatically
- Decodes status responses
- Publishes parsed values back to MQTT as JSON and scalar topics
- Accepts MQTT command topics for pump control
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
status_poll_interval: 15
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

### Command topics
- `pentair/pump/cmd/status`
- `pentair/pump/cmd/off`
- `pentair/pump/cmd/low`
- `pentair/pump/cmd/high`
- `pentair/pump/cmd/rpm`

Example:
- publish `2200` to `pentair/pump/cmd/rpm`

## Notes

- This add-on expects an MQTT-connected RS-485 transport such as a USR-DR164 configured to exchange raw Pentair frames.
- Broker credentials are provided through add-on configuration, not hardcoded in the source.
- If Home Assistant does not show the repository, confirm the root file is named exactly `repository.yaml`.

## Future improvements

- Home Assistant MQTT Discovery
- richer pump state decoding
- diagnostics and health topics
- optional manual command services
