# Changelog

All notable changes to the Pentair MQTT Bridge add-on are documented here.

## [0.5.0]

### Configuration simplification
- Removed user-facing `control_mode`, `status_poll_mode`, `status_poll_interval_seconds`, and legacy `status_poll_interval` settings.
- Added `poll_interval_minutes` as the only polling cadence option. Default is `15`; `0` means continuous polling while an active refresh window is running.
- Existing stored configs that still contain the removed options are accepted, but the bridge now logs that those settings are deprecated and ignored.

### Polling behavior
- Control mode is now always **on-demand** internally.
- Status polling now starts in **ACTIVE** mode and automatically switches to **PASSIVE** mode after 5 seconds.
- The bridge does not transmit `AUTO STATUS` poll frames while passive.
- Reconnects and cleaning-mode disable events trigger an immediate active refresh window.

### Observability
- Log timestamps are now emitted as local timezone-aware ISO-8601 values.
- Startup logging now reports the detected local timezone / UTC offset for verification.
- Added last poll telemetry topics: `<parsed_base>/last_poll_epoch` and `<parsed_base>/last_poll_local`.
- MQTT discovery now registers diagnostic sensors for the last poll timestamp and epoch values.

## [0.4.0]

### Pump Off fix
- Reworked `build_off_request()` to use action `0x06` with data `03 21 00 00` (stop / program 0), matching the Pentair packet spec stop sequence. The previous payload had no effect on the pump.

### 15-minute polling default
- Default `status_poll_interval_seconds` changed from **30 s** to **900 s** (15 minutes) to reduce RS-485 bus traffic and avoid locking the pump display during normal use.
- Cleaning mode still suspends polling entirely; disabling cleaning mode still triggers an immediate refresh.

### Speed 1–4 controls (replaces Pump Low / Pump High)
- MQTT discovery now registers **Speed 1**, **Speed 2**, **Speed 3**, and **Speed 4** button entities instead of the previous Pump Low / Pump High buttons.
- Each speed button has a dedicated command topic: `pentair/pump/cmd/speed/1` through `pentair/pump/cmd/speed/4`.
- RPM for each slot is configured via `speed1_rpm`, `speed2_rpm`, `speed3_rpm`, `speed4_rpm` options (defaults: 1100, 1650, 2200, 3000).
- The legacy `pentair/pump/cmd/low` and `pentair/pump/cmd/high` topics remain as backward-compatible aliases (low → Speed 1 RPM, high → Speed 4 RPM).

### Improved status decoder
- `Pump Mode` topic now publishes a human-readable label (e.g., `Manual`, `Feature 1`, `External`) instead of a raw byte value. The raw byte is still available in the JSON payload.
- `Pump Drive State` topic now publishes a label such as `Speed 1` through `Speed 4` or `Stopped` instead of a raw byte. Raw byte remains in JSON.
- `Pump Schedule Enabled` correctly reads bit 2 (`0x04`) of the run byte.
- `run_active` (bool) and label fields added to the JSON status payload.

### Other
- Device `sw_version` bumped to `0.4.0`.

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
