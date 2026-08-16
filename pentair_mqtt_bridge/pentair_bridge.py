import hashlib
import json
import logging
import threading
import time

import paho.mqtt.client as mqtt


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [pentair_bridge] %(message)s",
)
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

LOW_RPM = int(OPTIONS.get("low_rpm", 1650))
HIGH_RPM = int(OPTIONS.get("high_rpm", 3000))
DEFAULT_TARGET_RPM = 1650

_raw_control_mode = OPTIONS.get("control_mode", "on_demand")
if _raw_control_mode not in ("on_demand", "continuous"):
    logger.warning(
        "Invalid control_mode %r; falling back to 'on_demand'", _raw_control_mode
    )
    _raw_control_mode = "on_demand"
CONTROL_MODE = _raw_control_mode

CONTROL_RELEASE_SECONDS = int(OPTIONS.get("control_release_seconds", 60))
MIN_COMMAND_INTERVAL_SECONDS = float(OPTIONS.get("min_command_interval_seconds", 1.0))
DEDUPE_WINDOW_SECONDS = 2.0

_raw_poll_mode = OPTIONS.get("status_poll_mode", "active").lower()
if _raw_poll_mode not in ("active", "passive"):
    logger.warning(
        "Invalid status_poll_mode %r; falling back to 'active'", _raw_poll_mode
    )
    _raw_poll_mode = "active"
STATUS_POLL_MODE = _raw_poll_mode

# status_poll_interval_seconds overrides the older status_poll_interval if provided
STATUS_POLL_INTERVAL = float(
    OPTIONS.get(
        "status_poll_interval_seconds",
        OPTIONS.get("status_poll_interval", 30),
    )
)

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
    data = bytes([0x00, 0x00, 0x00])
    return pentair_frame(PUMP_ADDR, CTRL_ADDR, 0x01, data)


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
      2      drive state
      3-4    watts (big-endian)
      5-6    rpm (big-endian)
      7      timer hours
      8      timer minutes
      9      clock hours
      10     clock minutes

    Bits in byte 0 (run byte):
      bit 2  (0x04)  – schedule is currently enabled/active
    """
    out = {
        "run": None,
        "mode": None,
        "drive_state": None,
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
        out["run"] = data[0]
        out["mode"] = data[1]
        out["drive_state"] = data[2]
        out["watts"] = (data[3] << 8) | data[4]
        out["rpm"] = (data[5] << 8) | data[6]
        out["timer_hours"] = data[7]
        out["timer_minutes"] = data[8]
        out["clock_hours"] = data[9]
        out["clock_minutes"] = data[10]
        # Bit 2 of the run byte indicates schedule-running state
        out["schedule_enabled"] = bool(data[0] & 0x04)

    return out


def device_block():
    return {
        "identifiers": [DEVICE_ID],
        "name": DEVICE_NAME,
        "manufacturer": "Pentair",
        "model": "MQTT RS-485 Bridge",
        "sw_version": "0.3.0",
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
        "button_low": {
            "component": "button",
            "object_id": "low",
            "name": "Pump Low",
            "command_topic": f"{CMD_BASE}/low",
            "payload_press": "1",
            "icon": "mdi:fan-speed-1",
        },
        "button_high": {
            "component": "button",
            "object_id": "high",
            "name": "Pump High",
            "command_topic": f"{CMD_BASE}/high",
            "payload_press": "1",
            "icon": "mdi:fan-speed-3",
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


def publish_parsed_status(client, packet, status):
    payload = {
        "timestamp": int(time.time()),
        "source": f"0x{packet['src']:02X}",
        "destination": f"0x{packet['dest']:02X}",
        "action": f"0x{packet['action']:02X}",
        "checksum_ok": packet["checksum_ok"],
        "run": status["run"],
        "mode": status["mode"],
        "drive_state": status["drive_state"],
        "watts": status["watts"],
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
        "raw_data_hex": status["raw_data_hex"],
        "raw_packet_hex": hex_dump(packet["raw"]),
    }

    client.publish(f"{PARSED_BASE}/json", json.dumps(payload), retain=True)
    client.publish(f"{PARSED_BASE}/rpm", str(status["rpm"]), retain=True)
    client.publish(f"{PARSED_BASE}/watts", str(status["watts"]), retain=True)
    client.publish(f"{PARSED_BASE}/run", str(status["run"]), retain=True)
    client.publish(f"{PARSED_BASE}/mode", str(status["mode"]), retain=True)
    client.publish(f"{PARSED_BASE}/drive_state", str(status["drive_state"]), retain=True)
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
        publish_frame(client, build_status_request(), "CMD STATUS")
        return

    if topic == f"{CMD_BASE}/off":
        publish_control_frame(client, build_off_request(), "CMD OFF")
        return

    if topic == f"{CMD_BASE}/low":
        publish_control_frame(client, build_set_rpm_request(LOW_RPM), "CMD LOW")
        return

    if topic == f"{CMD_BASE}/high":
        publish_control_frame(client, build_set_rpm_request(HIGH_RPM), "CMD HIGH")
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

    # Trigger an immediate status poll on connect/reconnect
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


def autopoll_loop():
    global _hold_logged_expired
    logger.info(
        "Auto-poll loop started (interval=%.0fs, cleaning_mode=%s)",
        STATUS_POLL_INTERVAL,
        _cleaning_mode,
    )
    while not stop_event.is_set():
        # Wait for the poll interval OR an immediate-poll signal (whichever comes first).
        # _poll_now_event.wait() does not respond to stop_event, so we cap it at 1 s
        # slices and re-check the stop flag, ensuring clean shutdown without
        # waiting a full STATUS_POLL_INTERVAL.
        deadline = time.monotonic() + STATUS_POLL_INTERVAL
        while not stop_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            triggered = _poll_now_event.wait(timeout=min(remaining, 1.0))
            if triggered:
                break

        _poll_now_event.clear()

        if stop_event.is_set():
            break

        if command_client is None:
            continue

        # Respect cleaning mode – skip the poll but keep the loop alive
        with _cleaning_mode_lock:
            cleaning = _cleaning_mode
        if cleaning:
            logger.info(
                "AUTO POLL skipped — cleaning mode is enabled; polling suspended"
            )
            continue

        if CONTROL_MODE == "on_demand":
            now = time.monotonic()
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

        logger.info("AUTO POLL sending status request")
        publish_frame(command_client, build_status_request(), "AUTO STATUS")


def main():
    global listener_publish_client
    global command_client

    if not BROKER:
        raise RuntimeError("broker is required in add-on configuration")

    logger.info("Starting Pentair bridge with broker=%s port=%s", BROKER, PORT)
    logger.info(
        "Control mode: %s (release=%ds, min_interval=%.2fs)",
        CONTROL_MODE, CONTROL_RELEASE_SECONDS, MIN_COMMAND_INTERVAL_SECONDS,
    )
    logger.info(
        "Status poll mode: %s (interval=%.0fs)",
        STATUS_POLL_MODE, STATUS_POLL_INTERVAL,
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

    if STATUS_POLL_MODE == "passive":
        logger.info(
            "Status poll mode: passive — active AUTO STATUS polling disabled; "
            "telemetry updates from incoming uplink frames only."
        )
    else:
        poll_thread = threading.Thread(target=autopoll_loop, daemon=True)
        poll_thread.start()

    while not stop_event.is_set():
        time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        stop_event.set()
