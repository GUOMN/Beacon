"""Small cross-platform MQTT 3.1.1 publisher for Beacon task events."""
from __future__ import annotations

import json
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from .event_store import StatusEventStore


def _remaining_length(value: int) -> bytes:
    encoded = bytearray()
    while True:
        digit = value % 128
        value //= 128
        if value:
            digit |= 0x80
        encoded.append(digit)
        if not value:
            return bytes(encoded)


def _utf8(value: str) -> bytes:
    raw = value.encode("utf-8")
    return len(raw).to_bytes(2, "big") + raw


def _packet(kind: int, body: bytes) -> bytes:
    return bytes((kind,)) + _remaining_length(len(body)) + body


def validate_mqtt_settings(raw: dict[str, Any]) -> dict[str, Any]:
    enabled = bool(raw.get("enabled", False))
    target = str(raw.get("target") or "").strip()
    topic = str(raw.get("topic") if "topic" in raw else raw.get("topic_prefix") or "").strip()
    username = str(raw.get("username") or "").strip()
    password = str(raw.get("password") or "")
    qos = int(raw.get("qos", 1))
    if (enabled and not topic) or "+" in topic or "#" in topic:
        raise ValueError("MQTT 发布 Topic 无效")
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
            "username": username, "password": password}


@dataclass(frozen=True, slots=True)
class MqttSettings:
    enabled: bool
    target: str
    topic: str
    username: str
    password: str

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "MqttSettings":
        value = validate_mqtt_settings(raw)
        return cls(value["enabled"], value["target"], value["topic"], value["username"], value["password"])


class MqttPublisher:
    """Publish the durable outbox in a worker so ingestion never waits on a broker."""

    def __init__(self, store: StatusEventStore, settings_provider) -> None:
        self._store = store
        self._settings_provider = settings_provider
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._status = "已停用"

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="beacon-mqtt", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    def status(self) -> str:
        with self._lock:
            return self._status

    def _set_status(self, value: str) -> None:
        with self._lock:
            self._status = value

    def _run(self) -> None:
        while not self._stop.wait(0.25):
            message = None
            try:
                config = MqttSettings.from_raw(self._settings_provider())
                if not config.enabled:
                    self._set_status("已停用")
                    continue
                message = self._store.next_mqtt_message()
                if message is None:
                    self._set_status("已连接，等待任务变更")
                    continue
                self._publish(config, str(message["payload"]))
                self._store.mark_mqtt_published(int(message["id"]))
                self._set_status("已连接")
            except Exception as exc:
                self._set_status(f"连接失败：{str(exc)[:120]}")
                if message is not None:
                    self._store.mark_mqtt_failed(int(message["id"]), str(exc))
                self._stop.wait(2)

    def test_connection(self) -> None:
        config = MqttSettings.from_raw(self._settings_provider())
        if not config.enabled:
            raise ValueError("请先启用 MQTT 对外同步")
        self._connect(config).close()

    @staticmethod
    def _read_packet(connection: socket.socket) -> tuple[int, bytes]:
        first = connection.recv(1)
        if not first:
            raise ConnectionError("MQTT 服务器提前关闭连接")
        multiplier, remaining = 1, 0
        while True:
            digit = connection.recv(1)
            if not digit:
                raise ConnectionError("MQTT 数据不完整")
            remaining += (digit[0] & 127) * multiplier
            if not digit[0] & 128:
                break
            multiplier *= 128
            if multiplier > 128 ** 3:
                raise ValueError("MQTT 剩余长度无效")
        body = bytearray()
        while len(body) < remaining:
            chunk = connection.recv(remaining - len(body))
            if not chunk:
                raise ConnectionError("MQTT 数据不完整")
            body.extend(chunk)
        return first[0], bytes(body)

    def _connect(self, config: MqttSettings) -> socket.socket:
        parsed = urlparse(config.target)
        port = parsed.port or (8883 if parsed.scheme == "mqtts" else 1883)
        connection = socket.create_connection((str(parsed.hostname), port), timeout=5)
        connection.settimeout(5)
        if parsed.scheme == "mqtts":
            connection = ssl.create_default_context().wrap_socket(connection, server_hostname=parsed.hostname)
        client_id = "Beacon"
        flags = 0x02
        if config.username:
            flags |= 0x80
        if config.password:
            flags |= 0x40
        connect = _utf8("MQTT") + bytes((4, flags)) + (30).to_bytes(2, "big") + _utf8(client_id)
        if config.username:
            connect += _utf8(config.username)
        if config.password:
            connect += _utf8(config.password)
        connection.sendall(_packet(0x10, connect))
        kind, body = self._read_packet(connection)
        if kind != 0x20 or len(body) != 2 or body[1] != 0:
            connection.close()
            raise ConnectionError(f"MQTT CONNECT 被拒绝（返回码 {body[1] if len(body) > 1 else '未知'}）")
        return connection

    def _publish(self, config: MqttSettings, payload: str) -> None:
        event = json.loads(payload)
        topic = config.topic
        connection = self._connect(config)
        try:
            packet_id = 1
            body = _utf8(topic) + packet_id.to_bytes(2, "big") + payload.encode("utf-8")
            connection.sendall(_packet(0x32, body))
            kind, ack = self._read_packet(connection)
            if kind != 0x40 or ack != packet_id.to_bytes(2, "big"):
                raise ConnectionError("MQTT 未确认消息")
            connection.sendall(bytes((0xE0, 0x00)))
        finally:
            connection.close()
