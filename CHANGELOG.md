# Changelog

All notable changes to the Pentair MQTT Bridge add-on are documented here.

## [0.2.2]

- Default target RPM for new/default installs is now `1650` instead of `2000`.

## [0.2.1]

- **On-demand control mode improvements**: the bridge now releases the RS-485
  bus after a configurable timeout (`CONTROL_RELEASE_SECONDS`), restoring
  keypad usability when no automation commands are active.
- **Passive status polling mode**: set `STATUS_POLL_MODE=passive` to disable
  outgoing `TX AUTO STATUS` frames entirely. Telemetry is derived from uplink
  RX frames only, keeping the pump keypad active at all times.
- **Logging & configuration updates**: improved startup log output showing
  active control mode and poll mode; new `STATUS_POLL_INTERVAL_SECONDS` option.

## [0.2.0]

- Initial release of the Pentair MQTT Bridge Home Assistant add-on.
- MQTT discovery, RPM/watt/status publishing, and basic control commands.
