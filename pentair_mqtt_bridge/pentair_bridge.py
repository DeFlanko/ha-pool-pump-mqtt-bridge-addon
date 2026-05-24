import json
import os
import threading
import time
import paho.mqtt.client as mqtt


BROKER = os.getenv("BROKER", "")
PORT = int(os.getenv("PORT", "1883"))
USERNAME = os.getenv("USERNAME", "")
PASSWORD = os.getenv("PASSWORD", "")

TOPIC_UP = os.getenv("TOPIC_UP", "D4AD20CF144A/up")
TOPIC_DOWN = os.getenv("TOPIC_DOWN", "D4AD20CF144A/down")

PARSED_BASE = os.getenv("PARSED_BASE", "pentair/pump/status")
CMD_BASE = os.getenv("CMD_BASE", "pentair/pump/cmd")
DISCOVERY_BASE = os.getenv("DISCOVERY_BASE", "homeassistant")
DISCOVERY_PREFIX = os.getenv("DISCOVERY_PREFIX", "pentair_pump")
ENABLE_DISCOVERY = os.getenv("ENABLE_DISCOVERY", "true").lower() == "true"

CTRL_ADDR = int(os.getenv("CTRL_ADDR", "33"))
PUMP_ADDR = int(os.getenv("PUMP_ADDR", "96"))

LOW_RPM = int(os.getenv("LOW_RPM", "1650"))
HIGH_RPM = int(os.getenv("HIGH_RPM", "3000"))
STATUS_POLL_INTERVAL = int(os.getenv("STATUS_POLL_INTERVAL", "15"))

DEVICE_NAME = "Pentair Pool Pump"
DEVICE_ID = "pentair_pool_pump_bridge"

listener_publish_client = None
command_client = None
stop_event = threading.Event()
discovery_published = False


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

    return out


def device_block():
    return {
        "identifiers": [DEVICE_ID],
        "name": DEVICE_NAME,
        "manufacturer": "Pentair",
        "model": "MQTT RS-485 Bridge",
        "sw_version": "0.1.0",
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
        print(f"[DISCOVERY] Published {topic}")


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


def publish_frame(client, frame: bytes, label: str):
    info = client.publish(TOPIC_DOWN, frame)
    print(f"[TX {label}] {hex_dump(frame)} mid={info.mid}")


def handle_command(client, topic, payload_text):
    topic = topic.strip()
    payload_text = payload_text.strip()

    if topic == f"{CMD_BASE}/status":
        publish_frame(client, build_status_request(), "CMD STATUS")
        return

    if topic == f"{CMD_BASE}/off":
        publish_frame(client, build_off_request(), "CMD OFF")
        return

    if topic == f"{CMD_BASE}/low":
        publish_frame(client, build_set_rpm_request(LOW_RPM), "CMD LOW")
        return

    if topic == f"{CMD_BASE}/high":
        publish_frame(client, build_set_rpm_request(HIGH_RPM), "CMD HIGH")
        return

    if topic == f"{CMD_BASE}/rpm":
        try:
            rpm = int(payload_text)
            publish_frame(client, build_set_rpm_request(rpm), f"CMD RPM {rpm}")
        except ValueError:
            print(f"[CMD] Invalid RPM payload: {payload_text}")
        return

    print(f"[CMD] Unhandled topic: {topic}")


def on_connect(client, userdata, flags, reason_code, properties=None):
    print(f"[MQTT] Connected with rc={reason_code}")
    client.subscribe(TOPIC_UP)
    client.subscribe(f"{CMD_BASE}/#")
    print(f"[MQTT] Subscribed to {TOPIC_UP}")
    print(f"[MQTT] Subscribed to {CMD_BASE}/#")

    global discovery_published
    if ENABLE_DISCOVERY and not discovery_published:
        publish_discovery(client)
        discovery_published = True


def on_message(client, userdata, msg):
    global listener_publish_client

    if msg.topic.startswith(f"{CMD_BASE}/"):
        payload_text = msg.payload.decode("utf-8", errors="ignore")
        print(f"[CMD RX] topic={msg.topic} payload={payload_text!r}")
        handle_command(client, msg.topic, payload_text)
        return

    payload = msg.payload
    print(f"[RX] topic={msg.topic} len={len(payload)}")
    print(f"[RX HEX] {hex_dump(payload)}")

    pkt = parse_pentair_packet(payload)
    if pkt is None:
        print("[DECODE] Not a recognized Pentair frame")
        return

    print(
        f"[DECODE] src=0x{pkt['src']:02X} dest=0x{pkt['dest']:02X} "
        f"action=0x{pkt['action']:02X} len={pkt['data_len']} checksum_ok={pkt['checksum_ok']}"
    )

    if pkt["action"] == 0x07:
        status = decode_status_data(pkt["data"])
        print(
            "[STATUS] "
            f"run={status['run']} mode={status['mode']} drive_state={status['drive_state']} "
            f"watts={status['watts']} rpm={status['rpm']} "
            f"timer={status['timer_hours']:02d}:{status['timer_minutes']:02d} "
            f"clock={status['clock_hours']:02d}:{status['clock_minutes']:02d}"
        )

        if listener_publish_client is not None:
            publish_parsed_status(listener_publish_client, pkt, status)
            print(f"[MQTT] Published parsed status to {PARSED_BASE}/#")


def on_publish(client, userdata, mid, reason_code=None, properties=None):
    print(f"[MQTT] Publish acknowledged mid={mid}")


def mqtt_listener():
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if USERNAME:
        client.username_pw_set(USERNAME, PASSWORD)
    client.on_connect = on_connect
    client.on_message = on_message
    client.on_publish = on_publish
    client.connect(BROKER, PORT, 60)
    client.loop_forever()


def autopoll_loop():
    while not stop_event.is_set():
        if command_client is not None:
            publish_frame(command_client, build_status_request(), "AUTO STATUS")
        stop_event.wait(STATUS_POLL_INTERVAL)


def main():
    global listener_publish_client
    global command_client

    if not BROKER:
        raise RuntimeError("BROKER is required")

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
