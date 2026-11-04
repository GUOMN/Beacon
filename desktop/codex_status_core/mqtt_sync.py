"""Shared MQTT session for Beacon task events and screen pings."""
from __future__ import annotations

import json
import re
import ssl
import threading
import time
import uuid
from collections import deque
from typing import Any
from urllib.parse import urlparse

from .event_store import StatusEventStore


def validate_mqtt_settings(raw: dict[str, Any]) -> dict[str, Any]:
    enabled = bool(raw.get("enabled", False))
    target = str(raw.get("target") or "").strip()
    topic = str(raw.get("topic") if "topic" in raw else raw.get("topic_prefix") or "").strip()
    ping_topic = str(raw.get("ping_topic") or "").strip()
    username = str(raw.get("username") or "").strip()
    password = str(raw.get("password") or "")
    qos = int(raw.get("qos", 1))
    if (enabled and not topic) or any(
        any(char in name for char in "+#\0") or len(name.encode("utf-8")) > 65535
        for name in (topic, ping_topic)
    ):
        raise ValueError("MQTT Topic 无效")
    if ping_topic and ping_topic == topic:
        raise ValueError("状态 Topic 和 Ping Topic 必须不同")
    if qos != 1:
        raise ValueError("当前仅支持 MQTT QoS 1")
    if password and not username:
        raise ValueError("配置 MQTT 密码时必须同时填写用户名")
    if any(len(value.encode("utf-8")) > 65_535 for value in (username, password)):
        raise ValueError("MQTT 用户名或密码过长")
    if enabled:
        parsed = urlparse(target)
        if parsed.scheme not in {"mqtt", "mqtts"} or not parsed.hostname or parsed.path not in ("", "/"):
            raise ValueError("目标地址必须为 mqtt://主机:端口 或 mqtts://主机:端口")
        try:
            port = parsed.port or (8883 if parsed.scheme == "mqtts" else 1883)
        except ValueError as exc:
            raise ValueError("MQTT 端口无效") from exc
        if not 1 <= port <= 65535:
            raise ValueError("MQTT 端口无效")
    return {"enabled": enabled, "target": target, "topic": topic, "qos": qos,
            "username": username, "password": password, "ping_topic": ping_topic}


PING_KINDS = ("attention", "idea", "question", "wait", "help", "review", "done", "thanks")
FEISHU_EMOJI_KIND = re.compile(r"feishu:[A-Za-z0-9_]{1,64}")
EMOTE_IMAGE = re.compile(r"data:image/(?:webp|png);base64,[A-Za-z0-9+/]+={0,2}")
MAX_EMOTE_URL = 16_420
PING_TTL = 5


def marker_data(value):
    kind = value.get("kind")
    if isinstance(kind, str) and (kind in PING_KINDS or FEISHU_EMOJI_KIND.fullmatch(kind)):
        return {"kind": kind}
    if kind == "emote":
        image, fx = value.get("image"), value.get("fx", 0)
        if isinstance(image, str) and len(image) <= MAX_EMOTE_URL and EMOTE_IMAGE.fullmatch(image):
            if type(fx) is int and 0 <= fx < 6:
                return {"kind": kind, "image": image, "fx": fx}
    return None


def ping_coordinates(value):
    return all(type(value.get(axis)) in (int, float) and 0 <= value[axis] <= 1 for axis in ("x", "y"))


class MqttPublisher:
    """One MQTT session for the durable task outbox and transient desktop pings."""

    def __init__(self, store: StatusEventStore, settings_provider) -> None:
        self._store = store
        self._settings_provider = settings_provider
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._status = "已停用"
        self._id = uuid.uuid4().hex
        self._topic = ""
        self._connected_config = None
        self._attempted_config = None
        self._outgoing = deque(maxlen=16)
        self._incoming = deque(maxlen=32)
        self._seen = {}

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="beacon-mqtt", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=6)
            self._thread = None

    def status(self):
        with self._lock:
            return self._status

    def send_ping(self, value):
        marker = marker_data(value)
        if marker is None or marker["kind"] != "emote" or not ping_coordinates(value):
            raise ValueError("Ping 数据无效")
        now = time.time()
        created = value.get("time", now)
        if type(created) not in (int, float) or not now - 1 <= created <= now:
            return
        config = self._settings_provider()
        with self._lock:
            if config != self._connected_config or not self._topic:
                return
            self._outgoing.append({
                "v": 1, "id": uuid.uuid4().hex, "sender": self._id, "time": created,
                **marker, "x": value["x"], "y": value["y"],
            })

    def take_pings(self):
        config = self._settings_provider()
        with self._lock:
            now = time.time()
            result = [event for event in self._incoming if config == self._connected_config and now - event["time"] <= PING_TTL]
            self._incoming.clear()
            return result

    def _receive(self, _client, _userdata, message):
        if message.retain or len(message.payload) > MAX_EMOTE_URL + 512:
            return
        try:
            event = json.loads(message.payload)
        except (ValueError, RecursionError):
            return
        now = time.time()
        if (
            not isinstance(event, dict) or event.get("v") != 1
            or not isinstance(event.get("sender"), str) or len(event["sender"]) != 32
            or event["sender"] == self._id
            or not isinstance(event.get("id"), str) or len(event["id"]) != 32
            or type(event.get("time")) not in (int, float)
            or not now - PING_TTL <= event["time"] <= now + PING_TTL
            or not ping_coordinates(event)
        ):
            return
        marker = marker_data(event)
        if marker is None:
            return
        with self._lock:
            if message.topic != self._topic or event["id"] in self._seen:
                return
            self._seen = {key: expiry for key, expiry in self._seen.items() if expiry > now}
            if len(self._seen) >= 512:
                return
            self._seen[event["id"]] = event["time"] + PING_TTL
            self._incoming.append({**marker, "x": event["x"], "y": event["y"], "time": event["time"]})

    def test_connection(self):
        config = self._settings_provider()
        if not config["enabled"]:
            raise ValueError("请先启用 MQTT 对外同步")
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            with self._lock:
                status = self._status
                if config == self._connected_config:
                    if status == "已连接":
                        return
                    raise ConnectionError(status)
                if status.startswith("连接失败") and config == self._attempted_config:
                    raise ConnectionError(status)
            if self._stop.wait(0.1):
                break
        raise ConnectionError(self.status())

    def _run(self):
        while not self._stop.is_set():
            try:
                config = self._settings_provider()
                if not config["enabled"]:
                    with self._lock:
                        self._status = "已停用"
                    self._stop.wait(0.25)
                    continue
                with self._lock:
                    self._attempted_config = config
                    self._status = "连接中"
                self._session(config)
            except Exception as exc:
                with self._lock:
                    self._status = f"连接失败：{str(exc)[:120]}"
                self._stop.wait(2)

    def _session(self, config):
        import paho.mqtt.client as mqtt

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="beacon-" + self._id[:16],
                             clean_session=True, reconnect_on_failure=False)
        address = urlparse(config["target"])
        if address.scheme == "mqtts":
            client.tls_set_context(ssl.create_default_context())
        if config["username"]:
            client.username_pw_set(config["username"], config["password"] or None)
        client.on_message = self._receive
        subscribed = None
        subscription_id = None
        subscription_error = None

        def on_subscribe(_client, _userdata, mid, reasons, _properties):
            nonlocal subscription_id, subscription_error
            if mid == subscription_id:
                subscription_id = None
                if not reasons or reasons[0].is_failure:
                    subscription_error = "MQTT 服务器拒绝订阅 Ping Topic"
                else:
                    with self._lock:
                        self._topic = subscribed

        client.on_subscribe = on_subscribe
        pending = None
        pending_pings = deque()
        try:
            client.connect(address.hostname, address.port or (8883 if address.scheme == "mqtts" else 1883), 30)
            deadline = time.monotonic() + 5
            while not self._stop.is_set():
                latest = self._settings_provider()
                if any(latest[key] != config[key] for key in ("enabled", "target", "username", "password")):
                    return
                result = client.loop(timeout=0.1)
                if result != mqtt.MQTT_ERR_SUCCESS:
                    raise ConnectionError(mqtt.error_string(result))
                if not client.is_connected():
                    if time.monotonic() > deadline:
                        raise ConnectionError("MQTT 握手超时")
                    continue
                if subscription_id is not None and time.monotonic() > deadline:
                    subscription_id = None
                    subscription_error = "Ping Topic 订阅超时"
                if subscription_id is None and subscribed != latest["ping_topic"]:
                    with self._lock:
                        self._topic = ""
                        self._connected_config = None
                        self._status = "正在订阅 Ping Topic"
                        self._incoming.clear()
                        self._outgoing.clear()
                    if subscribed:
                        client.unsubscribe(subscribed)
                    subscribed = latest["ping_topic"]
                    subscription_error = None
                    if subscribed:
                        result, subscription_id = client.subscribe(subscribed, qos=1)
                        if result != mqtt.MQTT_ERR_SUCCESS:
                            raise ConnectionError(mqtt.error_string(result))
                        deadline = time.monotonic() + 5
                if subscription_id is None:
                    with self._lock:
                        self._status = f"已连接，{subscription_error}" if subscription_error else "已连接"
                        self._connected_config = latest
                        outgoing = list(self._outgoing)
                        self._outgoing.clear()
                    while pending_pings and pending_pings[0][0].is_published():
                        pending_pings.popleft()
                    if pending_pings and time.monotonic() >= pending_pings[0][1]:
                        raise ConnectionError("MQTT 未确认 Ping 消息")
                    for event in outgoing[:16 - len(pending_pings)]:
                        if time.time() - event["time"] <= 1:
                            info = client.publish(subscribed, json.dumps(event), qos=1)
                            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                                raise ConnectionError(mqtt.error_string(info.rc))
                            pending_pings.append((info, time.monotonic() + 5))
                if pending:
                    message, info, expiry = pending
                    if info.is_published():
                        self._store.mark_mqtt_published(int(message["id"]))
                        pending = None
                    elif time.monotonic() >= expiry:
                        raise ConnectionError("MQTT 未确认状态消息")
                if pending is None and (message := self._store.next_mqtt_message()):
                    info = client.publish(latest["topic"], message["payload"], qos=1)
                    if info.rc != mqtt.MQTT_ERR_SUCCESS:
                        raise ConnectionError(mqtt.error_string(info.rc))
                    pending = (message, info, time.monotonic() + 5)
        except Exception as exc:
            if pending:
                self._store.mark_mqtt_failed(int(pending[0]["id"]), str(exc))
            raise
        finally:
            with self._lock:
                self._topic = ""
                self._connected_config = None
                self._incoming.clear()
                self._outgoing.clear()
            client.disconnect()
