# Changelog

All notable changes to the Pentair MQTT Bridge add-on are documented here.

## [0.3.0]

### Polling enhancements
- Default `status_poll_interval_seconds` raised to **30 s** (was 15 s) to reduce bus contention on most installations.
- The bridge now performs an **immediate status poll** on startup and on every MQTT reconnect, so Home Assistant telemetry is populated the moment the add-on connects — no more stale/zero values after a scheduled pump start.
- When cleaning mode is disabled via MQTT, an immediate status refresh is triggered so HA reflects the current pump state right away.

### Cleaning mode
- New configuration option **`cleaning_mode`** (boolean, default `false`) to start the add-on with polling suspended.
- Runtime MQTT control topic **`<cmd_base>/set/cleaning_mode`** accepts `ON`/`OFF` payloads (also `1`/`0`, `TRUE`/`FALSE`, `YES`/`NO`).
- Current cleaning mode state is published (retained) to **`<parsed_base>/cleaning_mode`** (`ON`/`OFF`).
- When cleaning mode is enabled, the RS-485 bus goes silent so the physical panel remains freely usable during manual maintenance; resuming disables the hold and triggers an immediate refresh.
- Home Assistant MQTT discovery now registers a **switch** entity for cleaning mode.

### Schedule enabled state
- **`<parsed_base>/schedule_enabled`** topic now published (`ON`/`OFF`, retained) derived from bit 2 of the run-byte in each pump status response.
- Included in the `<parsed_base>/json` payload as `schedule_enabled`.
- Home Assistant MQTT discovery registers a **binary_sensor** entity for schedule enabled.
- Write support (enable/disable schedule via MQTT command) is deferred pending hardware validation of the write register.

### Speed 1–4 preset visibility
- When the pump reports operating on a numbered speed preset (drive_state 1–4), the live RPM is published (retained) to **`<parsed_base>/speed/{1..4}/rpm`**.
- Allows HA to show what each Speed button is configured to without requiring direct protocol introspection of the preset registers.
- Home Assistant MQTT discovery registers **sensor** entities for Speed 1–4 RPM.
- Existing topic structure is unchanged; new topics are additive.

### Other improvements
- Device `sw_version` in MQTT discovery payloads updated to `0.3.0`.
- Startup log now reports cleaning mode initial state.

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
