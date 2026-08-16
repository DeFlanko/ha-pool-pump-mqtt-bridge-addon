import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta

import paho.mqtt.client as mqtt



def initialize_local_timezone():
    tz = os.environ.get("TZ")
    if tz:
        os.environ["TZ"] = tz
    if hasattr(time, "tzset"):
        time.tzset()


initialize_local_timezone()


class LocalTimezoneFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        return datetime.fromtimestamp(record.created).astimezone().isoformat(
            timespec="seconds"
        )


_handler = logging.StreamHandler()
_handler.setFormatter(
    LocalTimezoneFormatter("%(asctime)s %(levelname)s [pentair_bridge] %(message)s")
)
logging.basicConfig(level=logging.INFO, handlers=[_handler], force=True)
logger = logging.getLogger("pentair_bridge")


def load_options():
    with open("/data/options.json", "r", encoding="utf-8") as f:
        return json.load(f)


OPTIONS = load_options()

BROKER = OPTIONS.get("broker", "")
PORT = int(OPTIONS.get("port", 1883))
USERNAME = OPTIONS.get("username", "")
PASSWORD = OPTIONS.get("password", "")

TOPIC_UP = OPTIONS.get("topic_up", "D4AD20CF144A/up")
TOPIC_DOWN = OPTIONS.get("topic_down", "D4AD20CF144A/down")

PARSED_BASE = OPTIONS.get("parsed_base", "pentair/pump/status")
CMD_BASE = OPTIONS.get("cmd_base", "pentair/pump/cmd")
DISCOVERY_BASE = OPTIONS.get("discovery_base", "homeassistant")
DISCOVERY_PREFIX = OPTIONS.get("discovery_prefix", "pentair_pump")
ENABLE_DISCOVERY = bool(OPTIONS.get("enable_discovery", True))

CTRL_ADDR = int(OPTIONS.get("ctrl_addr", 33))
PUMP_ADDR = int(OPTIONS.get("pump_addr", 96))

SPEED1_RPM = int(OPTIONS.get("speed1_rpm", OPTIONS.get("low_rpm", 1650)))
SPEED2_RPM = int(OPTIONS.get("speed2_rpm", 2000))
SPEED3_RPM = int(OPTIONS.get("speed3_rpm", 2500))
SPEED4_RPM = int(OPTIONS.get("speed4_rpm", OPTIONS.get("high_rpm", 3450)))
DEFAULT_TARGET_RPM = 1650

CONTROL_MODE = "on_demand"

CONTROL_RELEASE_SECONDS = int(OPTIONS.get("control_release_seconds", 60))
MIN_COMMAND_INTERVAL_SECONDS = float(OPTIONS.get("min_command_interval_seconds", 1.0))
DEDUPE_WINDOW_SECONDS = 2.0

ACTIVE_POLL_TIMEOUT_SECONDS = 5.0
CONTINUOUS_POLL_INTERVAL_SECONDS = 1.0


def _warn_deprecated_option(option_name: str, message: str):
    if option_name in OPTIONS:
        logger.warning("Option %r is deprecated and ignored: %s", option_name, message)


def _load_poll_interval_minutes() -> float:
    raw_value = OPTIONS.get("poll_interval_minutes", 15)
    try:
        poll_interval_minutes = float(raw_value)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid poll_interval_minutes %r; falling back to default 15", raw_value
        )
        return 15.0

    if poll_interval_minutes < 0:
        logger.warning(
            "Negative poll_interval_minutes %r is not supported; falling back to 15",
            raw_value,
        )
        return 15.0

    return poll_interval_minutes


_warn_deprecated_option(
    "control_mode",
    "control mode is now fixed to 'on_demand'",
)
_warn_deprecated_option(
    "status_poll_mode",
    "polling now starts active for 5 seconds and then returns to passive automatically",
)
_warn_deprecated_option(
    "status_poll_interval_seconds",
    "use 'poll_interval_minutes' instead",
)
_warn_deprecated_option(
    "status_poll_interval",
    "use 'poll_interval_minutes' instead",
)

POLL_INTERVAL_MINUTES = _load_poll_interval_minutes()
POLL_INTERVAL_SECONDS = POLL_INTERVAL_MINUTES * 60.0

# --- Cleaning mode ---
# When enabled, active polling is suspended so humans can operate the physical
# panel without the RS-485 bus being driven by the bridge.
_cleaning_mode_lock = threading.Lock()
_cleaning_mode: bool = bool(OPTIONS.get("cleaning_mode", False))

DEVICE_NAME = "Pentair Pool Pump"
DEVICE_ID = "pentair_pool_pump_bridge"

listener_publish_client = None
command_client = None
stop_event = threading.Event()
discovery_published = False

# --- control state ---
_control_lock = threading.Lock()
_last_cmd_hash: str | None = None
_last_cmd_time: float = 0.0
_hold_until: float = 0.0  # epoch second when hold window expires

# --- immediate-poll event: set to trigger a poll without waiting for the interval ---
_poll_now_event = threading.Event()
_poll_state_lock = threading.Lock()
_active_poll_until: float = 0.0
_next_poll_due: float = 0.0
_last_poll_epoch: int | None = None
_last_poll_local: str | None = None

# --- Energy accumulator ---
# Cumulative kWh computed by integrating watt samples over time.
# Persisted across restarts via a small JSON file in /data.
_ENERGY_PERSIST_PATH = "/data/energy_kwh.json"
_energy_lock = threading.Lock()
_energy_kwh: float = 0.0
_energy_last_sample_time: float | None = None  # monotonic clock


def _load_energy_kwh() -> float:
    """Load persisted cumulative kWh from /data, returning 0.0 on any error."""
    try:
        with open(_ENERGY_PERSIST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        value = float(data.get("energy_kwh", 0.0))
        if value < 0:
            return 0.0
        return value
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return 0.0


def _save_energy_kwh(value: float) -> None:
    """Persist cumulative kWh to /data (best-effort; never raises)."""
    try:
        with open(_ENERGY_PERSIST_PATH, "w", encoding="utf-8") as f:
            json.dump({"energy_kwh": value}, f)
    except OSError:
        pass


def _accumulate_energy(watts: float | None) -> float:
    """Integrate a new watt sample into the cumulative kWh counter.

    Uses wall-clock monotonic time to compute the time delta since the last
    sample.  Negative deltas, zero deltas, and invalid watt values are silently
    ignored so the accumulator stays monotonic (required for
    state_class: total_increasing).

    Returns the updated cumulative kWh value.
    """
    global _energy_kwh, _energy_last_sample_time

    now = time.monotonic()

    with _energy_lock:
        if watts is None or watts < 0:
            # Invalid sample – update timestamp so next delta starts fresh
            _energy_last_sample_time = now
            return _energy_kwh

        if _energy_last_sample_time is None:
            # First sample; initialise timestamp without accumulating
            _energy_last_sample_time = now
            return _energy_kwh

        delta_seconds = now - _energy_last_sample_time
        if delta_seconds <= 0:
            # Duplicate or out-of-order call; skip
            return _energy_kwh

        delta_hours = delta_seconds / 3600.0
        delta_kwh = (watts * delta_hours) / 1000.0

        if delta_kwh < 0:
            # Guard: should not happen given watts >= 0 and delta > 0
            _energy_last_sample_time = now
            return _energy_kwh

        _energy_kwh += delta_kwh
        _energy_last_sample_time = now

        _save_energy_kwh(_energy_kwh)
        return _energy_kwh


def hex_dump(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def pentair_checksum(body: bytes) -> bytes:
    total = sum(body) & 0xFFFF
    return bytes([(total >> 8) & 0xFF, total & 0xFF])


def pentair_frame(dest: int, src: int, action: int, data: bytes = b"") -> bytes:
    body = bytes([0xA5, 0x00, dest, src, action, len(data)]) + data
    return bytes([0xFF, 0x00, 0xFF]) + body + pentair_checksum(body)


def build_status_request() -> bytes:
    return pentair_frame(PUMP_ADDR, CTRL_ADDR, 0x07, b"")


def build_set_rpm_request(rpm: int) -> bytes:
    rpm = max(450, min(3450, rpm))
    data = bytes([0x02, 0xC4, (rpm >> 8) & 0xFF, rpm & 0xFF])
    return pentair_frame(PUMP_ADDR, CTRL_ADDR, 0x01, data)


def build_off_request() -> bytes:
    # Per the Pentair packet spec, program-control command 0x06 with data
    # 03 21 00 00 stops the pump (program 0 / stop).
    data = bytes([0x03, 0x21, 0x00, 0x00])
    return pentair_frame(PUMP_ADDR, CTRL_ADDR, 0x06, data)


def parse_pentair_packet(payload: bytes):
    if len(payload) < 11:
        return None
    if payload[0:4] != bytes([0xFF, 0x00, 0xFF, 0xA5]):
        return None

    version = payload[4]
    dest = payload[5]
    src = payload[6]
    action = payload[7]
    data_len = payload[8]

    expected_len = 3 + 1 + 1 + 1 + 1 + 1 + 1 + data_len + 2
    if len(payload) < expected_len:
        return None

    data = payload[9:9 + data_len]
    recv_ck = payload[9 + data_len:11 + data_len]
    body = payload[3:9 + data_len]
    calc_ck = pentair_checksum(body)

    return {
        "version": version,
        "dest": dest,
        "src": src,
        "action": action,
        "data_len": data_len,
        "data": data,
        "checksum_ok": recv_ck == calc_ck,
        "raw": payload,
    }


def decode_status_data(data: bytes):
    """Decode a Pentair action-0x07 status response payload.

    Byte layout (indices are 0-based within the data field):
      0      run / status flags
      1      mode
      2      drive state (PMP byte; 1-4 = speed preset slot)
      3-4    watts (big-endian)
      5-6    rpm (big-endian)
      7      timer hours
      8      timer minutes
      9      clock hours
      10     clock minutes

    Bits in byte 0 (run byte):
      bit 0  (0x01)  – pump is running
      bit 2  (0x04)  – schedule is currently enabled/active

    Mode byte (best-effort mapping from known IntelliFlo values):
      0x00 = Manual
      0x06 = Feature 1 / Speed Preset
      0x09 = External (Automation)

    Drive state byte — active speed preset slot (1-4) or 0 when not running
    on a preset.
    """
    # Named labels for the mode byte (best-effort; model-dependent)
    MODE_LABELS = {
        0x00: "Manual",
        0x06: "Feature 1",
        0x09: "External",
    }

    out = {
        "run": None,
        "run_active": None,
        "mode": None,
        "mode_label": None,
        "drive_state": None,
        "drive_state_label": None,
        "watts": None,
        "rpm": None,
        "timer_hours": None,
        "timer_minutes": None,
        "clock_hours": None,
        "clock_minutes": None,
        # schedule_enabled: derived from bit 2 of run byte (read-only from status)
        "schedule_enabled": None,
        "raw_data_hex": hex_dump(data),
    }

    if len(data) >= 11:
        run_byte = data[0]
        mode_byte = data[1]
        drive_byte = data[2]

        out["run"] = run_byte
        out["run_active"] = bool(run_byte & 0x01)
        out["mode"] = mode_byte
        out["mode_label"] = MODE_LABELS.get(mode_byte, f"0x{mode_byte:02X}")
        out["drive_state"] = drive_byte
        out["drive_state_label"] = (
            f"Speed {drive_byte}" if 1 <= drive_byte <= 4 else (
                "Stopped" if drive_byte == 0 else f"0x{drive_byte:02X}"
            )
        )
        out["watts"] = (data[3] << 8) | data[4]
        out["rpm"] = (data[5] << 8) | data[6]
        out["timer_hours"] = data[7]
        out["timer_minutes"] = data[8]
        out["clock_hours"] = data[9]
        out["clock_minutes"] = data[10]
        # Bit 2 of the run byte indicates schedule-running state
        out["schedule_enabled"] = bool(run_byte & 0x04)

    return out


def device_block():
    return {
        "identifiers": [DEVICE_ID],
        "name": DEVICE_NAME,
        "manufacturer": "Pentair",
        "model": "MQTT RS-485 Bridge",
        "sw_version": "0.6.0",
    }


def publish_discovery(client):
    entities = {
        "sensor_rpm": {
            "component": "sensor",
            "object_id": "rpm",
            "name": "Pump RPM",
            "state_topic": f"{PARSED_BASE}/rpm",
            "unit_of_measurement": "RPM",
            "icon": "mdi:rotate-right",
        },
        "sensor_watts": {
            "component": "sensor",
            "object_id": "watts",
            "name": "Pump Power",
            "state_topic": f"{PARSED_BASE}/watts",
            "unit_of_measurement": "W",
            "device_class": "power",
            "state_class": "measurement",
            "icon": "mdi:flash",
        },
        "sensor_energy_kwh": {
            "component": "sensor",
            "object_id": "energy_kwh",
            "name": "Pump Energy",
            "state_topic": f"{PARSED_BASE}/energy_kwh",
            "unit_of_measurement": "kWh",
            "device_class": "energy",
            "state_class": "total_increasing",
            "icon": "mdi:lightning-bolt",
        },
        "sensor_run": {
            "component": "sensor",
            "object_id": "run",
            "name": "Pump Run",
            "state_topic": f"{PARSED_BASE}/run",
            "icon": "mdi:play-circle",
        },
        "sensor_mode": {
            "component": "sensor",
            "object_id": "mode",
            "name": "Pump Mode",
            "state_topic": f"{PARSED_BASE}/mode",
            "icon": "mdi:cog",
        },
        "sensor_drive_state": {
            "component": "sensor",
            "object_id": "drive_state",
            "name": "Pump Drive State",
            "state_topic": f"{PARSED_BASE}/drive_state",
            "icon": "mdi:engine",
        },
        "sensor_last_poll_local": {
            "component": "sensor",
            "object_id": "last_poll_local",
            "name": "Pump Last Poll",
            "state_topic": f"{PARSED_BASE}/last_poll_local",
            "device_class": "timestamp",
            "entity_category": "diagnostic",
            "icon": "mdi:clock-check-outline",
        },
        "sensor_last_poll_epoch": {
            "component": "sensor",
            "object_id": "last_poll_epoch",
            "name": "Pump Last Poll Epoch",
            "state_topic": f"{PARSED_BASE}/last_poll_epoch",
            "entity_category": "diagnostic",
            "icon": "mdi:counter",
        },
        # Schedule enabled – read-only binary sensor derived from run-byte bit 2
        "binary_sensor_schedule_enabled": {
            "component": "binary_sensor",
            "object_id": "schedule_enabled",
            "name": "Pump Schedule Enabled",
            "state_topic": f"{PARSED_BASE}/schedule_enabled",
            "payload_on": "ON",
            "payload_off": "OFF",
            "icon": "mdi:calendar-check",
        },
        # Cleaning mode – runtime-controllable switch
        "switch_cleaning_mode": {
            "component": "switch",
            "object_id": "cleaning_mode",
            "name": "Cleaning Mode",
            "state_topic": f"{PARSED_BASE}/cleaning_mode",
            "command_topic": f"{CMD_BASE}/set/cleaning_mode",
            "payload_on": "ON",
            "payload_off": "OFF",
            "icon": "mdi:broom",
        },
        # Speed preset sensors (read from last decoded status when available)
        "sensor_speed1_rpm": {
            "component": "sensor",
            "object_id": "speed1_rpm",
            "name": "Speed 1 RPM",
            "state_topic": f"{PARSED_BASE}/speed/1/rpm",
            "unit_of_measurement": "RPM",
            "icon": "mdi:fan-speed-1",
        },
        "sensor_speed2_rpm": {
            "component": "sensor",
            "object_id": "speed2_rpm",
            "name": "Speed 2 RPM",
            "state_topic": f"{PARSED_BASE}/speed/2/rpm",
            "unit_of_measurement": "RPM",
            "icon": "mdi:fan-speed-2",
        },
        "sensor_speed3_rpm": {
            "component": "sensor",
            "object_id": "speed3_rpm",
            "name": "Speed 3 RPM",
            "state_topic": f"{PARSED_BASE}/speed/3/rpm",
            "unit_of_measurement": "RPM",
            "icon": "mdi:fan-speed-3",
        },
        "sensor_speed4_rpm": {
            "component": "sensor",
            "object_id": "speed4_rpm",
            "name": "Speed 4 RPM",
            "state_topic": f"{PARSED_BASE}/speed/4/rpm",
            "unit_of_measurement": "RPM",
            "icon": "mdi:fan",
        },
        "button_status": {
            "component": "button",
            "object_id": "status",
            "name": "Poll Pump Status",
            "command_topic": f"{CMD_BASE}/status",
            "payload_press": "1",
            "icon": "mdi:refresh",
        },
        "button_off": {
            "component": "button",
            "object_id": "off",
            "name": "Pump Off",
            "command_topic": f"{CMD_BASE}/off",
            "payload_press": "1",
            "icon": "mdi:stop-circle",
        },
        "button_speed1": {
            "component": "button",
            "object_id": "speed1",
            "name": "Speed 1",
            "command_topic": f"{CMD_BASE}/speed/1",
            "payload_press": "1",
            "icon": "mdi:fan-speed-1",
        },
        "button_speed2": {
            "component": "button",
            "object_id": "speed2",
            "name": "Speed 2",
            "command_topic": f"{CMD_BASE}/speed/2",
            "payload_press": "1",
            "icon": "mdi:fan-speed-2",
        },
        "button_speed3": {
            "component": "button",
            "object_id": "speed3",
            "name": "Speed 3",
            "command_topic": f"{CMD_BASE}/speed/3",
            "payload_press": "1",
            "icon": "mdi:fan-speed-3",
        },
        "button_speed4": {
            "component": "button",
            "object_id": "speed4",
            "name": "Speed 4",
            "command_topic": f"{CMD_BASE}/speed/4",
            "payload_press": "1",
            "icon": "mdi:fan",
        },
        "number_rpm_target": {
            "component": "number",
            "object_id": "target_rpm",
            "name": "Pump Target RPM",
            "command_topic": f"{CMD_BASE}/rpm",
            "state_topic": f"{PARSED_BASE}/rpm",
            "initial": DEFAULT_TARGET_RPM,
            "min": 450,
            "max": 3450,
            "step": 10,
            "mode": "box",
            "unit_of_measurement": "RPM",
            "icon": "mdi:speedometer",
        },
    }

    for unique_suffix, entity in entities.items():
        component = entity.pop("component")
        object_id = entity.pop("object_id")
        topic = f"{DISCOVERY_BASE}/{component}/{DISCOVERY_PREFIX}/{object_id}/config"

        payload = {
            **entity,
            "unique_id": f"{DEVICE_ID}_{unique_suffix}",
            "device": device_block(),
        }

        client.publish(topic, json.dumps(payload), retain=True)
        logger.info("Published discovery topic %s", topic)


def publish_cleaning_mode_state(client):
    """Publish the current cleaning mode state to MQTT."""
    with _cleaning_mode_lock:
        state = _cleaning_mode
    client.publish(
        f"{PARSED_BASE}/cleaning_mode",
        "ON" if state else "OFF",
        retain=True,
    )
    logger.info("Cleaning mode state published: %s", "ON" if state else "OFF")


def local_now() -> datetime:
    return datetime.now().astimezone()


def format_utc_offset(offset: timedelta | None) -> str:
    if offset is None:
        return "UTC+00:00"

    total_seconds = int(offset.total_seconds())
    sign = "+" if total_seconds >= 0 else "-"
    total_seconds = abs(total_seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes = remainder // 60
    return f"UTC{sign}{hours:02d}:{minutes:02d}"


def publish_last_poll_state(client, when: datetime | None = None):
    global _last_poll_epoch, _last_poll_local

    if when is None:
        when = local_now()

    with _poll_state_lock:
        _last_poll_epoch = int(when.timestamp())
        _last_poll_local = when.isoformat(timespec="seconds")
        last_poll_epoch = _last_poll_epoch
        last_poll_local = _last_poll_local

    client.publish(f"{PARSED_BASE}/last_poll_epoch", str(last_poll_epoch), retain=True)
    client.publish(f"{PARSED_BASE}/last_poll_local", last_poll_local, retain=True)
    logger.info(
        "Poll refresh telemetry published epoch=%s local=%s",
        last_poll_epoch,
        last_poll_local,
    )


def publish_parsed_status(client, packet, status):
    with _poll_state_lock:
        last_poll_epoch = _last_poll_epoch
        last_poll_local = _last_poll_local

    # Accumulate cumulative energy before building the JSON payload so it is
    # included in the /json topic together with all other status fields.
    energy_kwh = _accumulate_energy(
        float(status["watts"]) if status["watts"] is not None else None
    )

    payload = {
        "timestamp": int(time.time()),
        "timestamp_local": local_now().isoformat(timespec="seconds"),
        "source": f"0x{packet['src']:02X}",
        "destination": f"0x{packet['dest']:02X}",
        "action": f"0x{packet['action']:02X}",
        "checksum_ok": packet["checksum_ok"],
        "run": status["run"],
        "run_active": status["run_active"],
        "mode": status["mode"],
        "mode_label": status["mode_label"],
        "drive_state": status["drive_state"],
        "drive_state_label": status["drive_state_label"],
        "watts": status["watts"],
        "energy_kwh": round(energy_kwh, 6),
        "rpm": status["rpm"],
        "schedule_enabled": status["schedule_enabled"],
        "timer": {
            "hours": status["timer_hours"],
            "minutes": status["timer_minutes"],
        },
        "clock": {
            "hours": status["clock_hours"],
            "minutes": status["clock_minutes"],
        },
        "last_poll_epoch": last_poll_epoch,
        "last_poll_local": last_poll_local,
        "raw_data_hex": status["raw_data_hex"],
        "raw_packet_hex": hex_dump(packet["raw"]),
    }

    client.publish(f"{PARSED_BASE}/json", json.dumps(payload), retain=True)
    client.publish(f"{PARSED_BASE}/rpm", str(status["rpm"]), retain=True)
    client.publish(f"{PARSED_BASE}/watts", str(status["watts"]), retain=True)
    client.publish(f"{PARSED_BASE}/energy_kwh", f"{energy_kwh:.6f}", retain=True)
    client.publish(f"{PARSED_BASE}/run", str(status["run"]), retain=True)
    client.publish(f"{PARSED_BASE}/mode", status["mode_label"] or str(status["mode"]), retain=True)
    client.publish(f"{PARSED_BASE}/drive_state", status["drive_state_label"] or str(status["drive_state"]), retain=True)
    client.publish(
        f"{PARSED_BASE}/timer",
        json.dumps({"hours": status["timer_hours"], "minutes": status["timer_minutes"]}),
        retain=True,
    )
    client.publish(
        f"{PARSED_BASE}/clock",
        json.dumps({"hours": status["clock_hours"], "minutes": status["clock_minutes"]}),
        retain=True,
    )

    # Schedule enabled (derived from run-byte bit 2; None when data unavailable)
    if status["schedule_enabled"] is not None:
        client.publish(
            f"{PARSED_BASE}/schedule_enabled",
            "ON" if status["schedule_enabled"] else "OFF",
            retain=True,
        )

    # Speed preset RPM values: for IntelliFlo VS the current running RPM
    # correlates to the active speed preset when mode indicates a speed button
    # is in use.  We publish the live RPM under the active speed slot when the
    # mode byte identifies a specific preset (mode 0x06 = speed-preset mode).
    # Slots that are not currently active are published as unknown ("").
    _maybe_publish_speed_presets(client, status)


def _maybe_publish_speed_presets(client, status):
    """Publish Speed 1–4 RPM topics based on the active drive state.

    Pentair IntelliFlo VS uses the drive_state byte to indicate which speed
    preset button is currently active:
      drive_state 1 → Speed 1
      drive_state 2 → Speed 2
      drive_state 3 → Speed 3
      drive_state 4 → Speed 4

    When the pump is actively running on a numbered preset we publish the
    live RPM to that preset's topic so the HA card shows what each speed
    button is configured to.  Other slots are left at their last retained
    value (we do not overwrite them with empty/zero).
    """
    rpm = status.get("rpm")
    drive_state = status.get("drive_state")

    if rpm is None or drive_state is None:
        return

    if drive_state in (1, 2, 3, 4):
        client.publish(
            f"{PARSED_BASE}/speed/{drive_state}/rpm",
            str(rpm),
            retain=True,
        )
        logger.info("Speed %d RPM updated to %d", drive_state, rpm)


def publish_frame(client, frame: bytes, label: str):
    info = client.publish(TOPIC_DOWN, frame)
    logger.info("TX %s %s mid=%s", label, hex_dump(frame), info.mid)


def publish_status_request(client, label: str):
    publish_frame(client, build_status_request(), label)
    publish_last_poll_state(client)


def _cmd_fingerprint(frame: bytes) -> str:
    return hashlib.sha1(frame).hexdigest()


def _in_hold_window() -> bool:
    return time.monotonic() < _hold_until


def publish_control_frame(client, frame: bytes, label: str) -> bool:
    """Send a control frame, applying dedupe and rate-limiting.

    Returns True if the frame was sent, False if it was skipped.
    """
    global _last_cmd_hash, _last_cmd_time, _hold_until

    now = time.monotonic()
    fp = _cmd_fingerprint(frame)

    with _control_lock:
        # Dedupe: skip if same command within 2 s
        if fp == _last_cmd_hash and (now - _last_cmd_time) < DEDUPE_WINDOW_SECONDS:
            logger.info("CTRL duplicate skipped label=%s", label)
            return False

        # Rate limit
        elapsed = now - _last_cmd_time
        if elapsed < MIN_COMMAND_INTERVAL_SECONDS:
            logger.info(
                "CTRL rate-limited label=%s elapsed=%.2fs min=%.2fs",
                label, elapsed, MIN_COMMAND_INTERVAL_SECONDS,
            )
            return False

        # Send
        publish_frame(client, frame, label)
        logger.info("CTRL command sent label=%s mode=%s", label, CONTROL_MODE)
        _last_cmd_hash = fp
        _last_cmd_time = now

        if CONTROL_MODE == "on_demand":
            _hold_until = now + CONTROL_RELEASE_SECONDS
            logger.info(
                "CTRL hold window started; releases in %ds", CONTROL_RELEASE_SECONDS
            )

    return True


def handle_command(client, topic, payload_text):
    global _cleaning_mode

    topic = topic.strip()
    payload_text = payload_text.strip()

    if topic == f"{CMD_BASE}/status":
        publish_status_request(client, "CMD STATUS")
        return

    if topic == f"{CMD_BASE}/off":
        publish_control_frame(client, build_off_request(), "CMD OFF")
        return

    if topic == f"{CMD_BASE}/speed/1":
        publish_control_frame(client, build_set_rpm_request(SPEED1_RPM), "CMD SPEED1")
        return

    if topic == f"{CMD_BASE}/speed/2":
        publish_control_frame(client, build_set_rpm_request(SPEED2_RPM), "CMD SPEED2")
        return

    if topic == f"{CMD_BASE}/speed/3":
        publish_control_frame(client, build_set_rpm_request(SPEED3_RPM), "CMD SPEED3")
        return

    if topic == f"{CMD_BASE}/speed/4":
        publish_control_frame(client, build_set_rpm_request(SPEED4_RPM), "CMD SPEED4")
        return

    # Backward-compatible aliases (low → speed1, high → speed4)
    if topic == f"{CMD_BASE}/low":
        publish_control_frame(client, build_set_rpm_request(SPEED1_RPM), "CMD LOW (alias speed1)")
        return

    if topic == f"{CMD_BASE}/high":
        publish_control_frame(client, build_set_rpm_request(SPEED4_RPM), "CMD HIGH (alias speed4)")
        return

    if topic == f"{CMD_BASE}/rpm":
        try:
            rpm = int(payload_text)
            publish_control_frame(client, build_set_rpm_request(rpm), f"CMD RPM {rpm}")
        except ValueError:
            logger.warning("Invalid RPM payload: %s", payload_text)
        return

    if topic == f"{CMD_BASE}/set/cleaning_mode":
        normalized = payload_text.upper()
        if normalized in ("ON", "1", "TRUE", "YES"):
            with _cleaning_mode_lock:
                _cleaning_mode = True
            logger.info("Cleaning mode ENABLED — polling suspended")
            publish_cleaning_mode_state(client)
        elif normalized in ("OFF", "0", "FALSE", "NO"):
            with _cleaning_mode_lock:
                _cleaning_mode = False
            logger.info(
                "Cleaning mode DISABLED — resuming polling; triggering immediate refresh"
            )
            publish_cleaning_mode_state(client)
            # Signal the poll loop to poll immediately
            _poll_now_event.set()
        else:
            logger.warning(
                "Invalid cleaning_mode payload %r; expected ON/OFF", payload_text
            )
        return

    logger.warning("Unhandled command topic: %s", topic)


def on_connect(client, userdata, flags, reason_code, properties=None):
    logger.info("MQTT connected rc=%s", reason_code)
    client.subscribe(TOPIC_UP)
    client.subscribe(f"{CMD_BASE}/#")
    logger.info("Subscribed to %s", TOPIC_UP)
    logger.info("Subscribed to %s/#", CMD_BASE)

    global discovery_published
    if ENABLE_DISCOVERY and not discovery_published:
        publish_discovery(client)
        discovery_published = True

    # Publish cleaning mode state on every (re)connect so HA is in sync
    publish_cleaning_mode_state(client)

    # Trigger an immediate active polling window on connect/reconnect
    _poll_now_event.set()


def on_message(client, userdata, msg):
    global listener_publish_client

    if msg.topic.startswith(f"{CMD_BASE}/"):
        payload_text = msg.payload.decode("utf-8", errors="ignore")
        logger.info("Command RX topic=%s payload=%r", msg.topic, payload_text)
        handle_command(client, msg.topic, payload_text)
        return

    payload = msg.payload
    logger.info("RX topic=%s len=%s", msg.topic, len(payload))
    logger.info("RX HEX %s", hex_dump(payload))

    pkt = parse_pentair_packet(payload)
    if pkt is None:
        logger.warning("Not a recognized Pentair frame")
        return

    logger.info(
        "Decoded packet src=0x%02X dest=0x%02X action=0x%02X len=%s checksum_ok=%s",
        pkt["src"],
        pkt["dest"],
        pkt["action"],
        pkt["data_len"],
        pkt["checksum_ok"],
    )

    if pkt["action"] == 0x07:
        status = decode_status_data(pkt["data"])
        logger.info(
            "Status run=%s mode=%s drive_state=%s watts=%s rpm=%s timer=%02d:%02d clock=%02d:%02d",
            status["run"],
            status["mode"],
            status["drive_state"],
            status["watts"],
            status["rpm"],
            status["timer_hours"] if status["timer_hours"] is not None else 0,
            status["timer_minutes"] if status["timer_minutes"] is not None else 0,
            status["clock_hours"] if status["clock_hours"] is not None else 0,
            status["clock_minutes"] if status["clock_minutes"] is not None else 0,
        )

        if listener_publish_client is not None:
            publish_parsed_status(listener_publish_client, pkt, status)
            logger.info("Published parsed status to %s/#", PARSED_BASE)


def on_publish(client, userdata, mid, reason_code=None, properties=None):
    logger.info("MQTT publish acknowledged mid=%s", mid)


def mqtt_listener():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if USERNAME:
        client.username_pw_set(USERNAME, PASSWORD)
    client.on_connect = on_connect
    client.on_message = on_message
    client.on_publish = on_publish
    client.connect(BROKER, PORT, 60)
    client.loop_forever()


_hold_logged_expired = False


def activate_polling_window(reason: str, *, immediate: bool):
    global _active_poll_until, _next_poll_due

    now = time.monotonic()
    with _poll_state_lock:
        _active_poll_until = max(_active_poll_until, now + ACTIVE_POLL_TIMEOUT_SECONDS)
        if immediate or _next_poll_due <= 0:
            _next_poll_due = now
    logger.info(
        "Polling ACTIVE for %.0fs (%s)",
        ACTIVE_POLL_TIMEOUT_SECONDS,
        reason,
    )


def autopoll_loop():
    global _hold_logged_expired, _active_poll_until, _next_poll_due
    logger.info(
        "Auto-poll loop started (cadence=%s, active_window=%.0fs, cleaning_mode=%s)",
        (
            "continuous"
            if POLL_INTERVAL_SECONDS == 0
            else f"{POLL_INTERVAL_SECONDS:.0f}s"
        ),
        ACTIVE_POLL_TIMEOUT_SECONDS,
        _cleaning_mode,
    )
    activate_polling_window("startup", immediate=True)
    poll_state = None
    pending_immediate = True
    while not stop_event.is_set():
        if pending_immediate:
            refresh_requested = False
            pending_immediate = False
        else:
            refresh_requested = _poll_now_event.wait(timeout=1.0)

        if refresh_requested:
            _poll_now_event.clear()

        if stop_event.is_set():
            break

        if command_client is None:
            continue

        if refresh_requested:
            activate_polling_window("immediate refresh request", immediate=True)

        # Respect cleaning mode – skip the poll but keep the loop alive
        with _cleaning_mode_lock:
            cleaning = _cleaning_mode
        if cleaning:
            logger.info(
                "AUTO POLL skipped — cleaning mode is enabled; polling suspended"
            )
            continue

        now = time.monotonic()

        if CONTROL_MODE == "on_demand":
            with _control_lock:
                hold_until = _hold_until
                logged = _hold_logged_expired
            if hold_until > 0 and now >= hold_until and not logged:
                logger.info(
                    "CTRL hold window expired; resuming read-only polling"
                )
                with _control_lock:
                    _hold_logged_expired = True
            elif now < hold_until:
                with _control_lock:
                    _hold_logged_expired = False
        with _poll_state_lock:
            if POLL_INTERVAL_SECONDS > 0 and _next_poll_due > 0 and now >= _next_poll_due:
                _active_poll_until = max(
                    _active_poll_until,
                    now + ACTIVE_POLL_TIMEOUT_SECONDS,
                )

            polling_active = now < _active_poll_until
            poll_due = _next_poll_due <= 0 or now >= _next_poll_due

        current_state = "ACTIVE" if polling_active else "PASSIVE"
        if current_state != poll_state:
            logger.info("Polling mode switched to %s", current_state)
            poll_state = current_state

        if not polling_active or not poll_due:
            continue

        logger.info("AUTO POLL sending status request")
        publish_status_request(command_client, "AUTO STATUS")

        with _poll_state_lock:
            if POLL_INTERVAL_SECONDS == 0:
                _next_poll_due = time.monotonic() + CONTINUOUS_POLL_INTERVAL_SECONDS
            else:
                _next_poll_due = time.monotonic() + POLL_INTERVAL_SECONDS


def main():
    global listener_publish_client
    global command_client
    global _energy_kwh

    if not BROKER:
        raise RuntimeError("broker is required in add-on configuration")

    loaded_kwh = _load_energy_kwh()
    with _energy_lock:
        _energy_kwh = loaded_kwh
    logger.info("Loaded persisted cumulative energy: %.6f kWh", loaded_kwh)
    logger.info("Starting Pentair bridge with broker=%s port=%s", BROKER, PORT)
    tz_now = local_now()
    logger.info(
        "Detected local timezone: %s (%s); current local time=%s",
        tz_now.tzname() or "local",
        format_utc_offset(tz_now.utcoffset()),
        tz_now.isoformat(timespec="seconds"),
    )
    logger.info(
        "Control mode: %s (release=%ds, min_interval=%.2fs)",
        CONTROL_MODE, CONTROL_RELEASE_SECONDS, MIN_COMMAND_INTERVAL_SECONDS,
    )
    logger.info(
        "Polling behavior: startup ACTIVE for %.0fs, then PASSIVE; cadence=%s",
        ACTIVE_POLL_TIMEOUT_SECONDS,
        (
            "continuous while active"
            if POLL_INTERVAL_SECONDS == 0
            else f"{POLL_INTERVAL_MINUTES:.0f} minute(s)"
        ),
    )
    logger.info("Cleaning mode on startup: %s", "ENABLED" if _cleaning_mode else "DISABLED")

    listener_thread = threading.Thread(target=mqtt_listener, daemon=True)
    listener_thread.start()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if USERNAME:
        client.username_pw_set(USERNAME, PASSWORD)
    client.on_publish = on_publish
    client.connect(BROKER, PORT, 60)
    client.loop_start()

    listener_publish_client = client
    command_client = client

    poll_thread = threading.Thread(target=autopoll_loop, daemon=True)
    poll_thread.start()

    while not stop_event.is_set():
        time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        stop_event.set()
