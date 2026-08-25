"""Local-only custom data source configuration and adapter supervision."""
from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import os
import re
import socket
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any

from .event_store import StatusEventStore, app_data_directory

SCHEMA = "beacon.custom-source/2"
CONTROL_SCHEMA = "beacon.adapter-control/1"
ALLOWED_STATES = {"running", "waiting", "success", "warning", "failure"}
MAX_EVENT_BYTES = 256 * 1024


class CustomSourceRegistry:
    """Persist custom source metadata in the user's app-data directory only."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or app_data_directory() / "custom-sources.json"
        self._lock = threading.RLock()

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError):
                return []
            sources = payload.get("sources", []) if isinstance(payload, dict) else []
            return [dict(item) for item in sources if isinstance(item, dict)]

    def get(self, source_id: str) -> dict[str, Any] | None:
        return next((item for item in self.list() if item.get("id") == source_id), None)

    def save(self, raw: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            source = normalize_config(raw)
            validate_config(source, require_complete=bool(source["enabled"]))
            sources = self.list()
            sources = [item for item in sources if item.get("id") != source["id"]]
            sources.append(source)
            self._write(sources)
            return source

    def set_enabled(self, source_id: str, enabled: bool) -> dict[str, Any]:
        source = self.get(source_id)
        if source is None:
            raise ValueError("找不到自定义数据源")
        source["enabled"] = enabled
        return self.save(source)

    def delete(self, source_id: str) -> None:
        with self._lock:
            sources = self.list()
            if not any(item.get("id") == source_id for item in sources):
                raise ValueError("找不到自定义数据源")
            self._write([item for item in sources if item.get("id") != source_id])

    def _write(self, sources: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"version": 1, "sources": sources}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, self.path)
        if os.name != "nt":
            self.path.chmod(0o600)


def normalize_config(raw: dict[str, Any]) -> dict[str, Any]:
    args = raw.get("adapter_args", [])
    if isinstance(args, str):
        args = [line.strip() for line in args.splitlines() if line.strip()]
    if not isinstance(args, list):
        raise ValueError("适配器参数必须是字符串列表")
    return {
        "id": str(raw.get("id") or uuid.uuid4().hex).strip()[:64],
        "name": str(raw.get("name") or "自定义本地订阅").strip()[:80],
        "enabled": bool(raw.get("enabled", False)),
        "transport": str(raw.get("transport") or "unix").strip().lower(),
        "socket_path": str(raw.get("socket_path") or "").strip(),
        "host": str(raw.get("host") or "127.0.0.1").strip(),
        "port": int(raw.get("port") or 0),
        "adapter_executable": str(raw.get("adapter_executable") or "").strip(),
        "adapter_args": [str(value) for value in args][:32],
        "adapter_config_path": str(raw.get("adapter_config_path") or "").strip(),
    }


def validate_config(source: dict[str, Any], require_complete: bool = True) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", str(source.get("id") or "")):
        raise ValueError("数据源标识无效")
    if not source.get("name"):
        raise ValueError("数据源名称不能为空")
    transport = source.get("transport")
    if transport not in {"unix", "tcp"}:
        raise ValueError("本地端点只支持 Unix Socket 或 TCP")
    if transport == "tcp":
        host = str(source.get("host") or "")
        try:
            loopback = host.lower() == "localhost" or ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise ValueError("TCP 自定义数据源只允许本机回环地址")
        port = int(source.get("port") or 0)
        if require_complete and not 1 <= port <= 65535:
            raise ValueError("请填写 1～65535 的本地端口")
    elif require_complete:
        socket_path = Path(str(source.get("socket_path") or "")).expanduser()
        if not str(source.get("socket_path") or ""):
            raise ValueError("请填写 Unix Socket 文件路径")
        if not socket_path.is_absolute():
            raise ValueError("Unix Socket 必须使用绝对路径")
    executable = str(source.get("adapter_executable") or "")
    if require_complete:
        if not executable:
            raise ValueError("请填写适配器可执行文件")
        path = Path(executable).expanduser()
        if not path.is_absolute() or not path.is_file():
            raise ValueError("适配器必须是本机存在的绝对路径")
        if os.name != "nt" and not os.access(path, os.X_OK):
            raise ValueError("适配器文件没有执行权限")
    adapter_config = str(source.get("adapter_config_path") or "")
    if adapter_config and require_complete:
        config_path = Path(adapter_config).expanduser()
        if not config_path.is_absolute() or not config_path.is_file():
            raise ValueError("适配器私有配置必须是本机存在的绝对路径")
    if any("\x00" in str(value) for value in source.get("adapter_args", [])):
        raise ValueError("适配器参数包含无效字符")


def adapter_envelope(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "source_id": source["id"],
        "adapter_config_path": (
            str(Path(source["adapter_config_path"]).expanduser())
            if source.get("adapter_config_path") else None
        ),
    }


class CustomSourceSupervisor:
    """Connect local endpoints and run protocol-only adapter processes."""

    def __init__(self, store: StatusEventStore, registry: CustomSourceRegistry) -> None:
        self.store = store
        self.registry = registry
        self._lock = threading.RLock()
        self._workers: dict[str, tuple[threading.Event, threading.Thread]] = {}
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._connections: dict[str, socket.socket] = {}
        self._statuses: dict[str, str] = {}

    def start(self) -> None:
        self.reload()

    def stop(self) -> None:
        self._stop_workers()

    def reload(self) -> None:
        self._stop_workers()
        for source in self.registry.list():
            source_id = str(source.get("id") or "")
            if not source_id:
                continue
            if not source.get("enabled"):
                self._set_status(source_id, "已停用")
                continue
            stop = threading.Event()
            thread = threading.Thread(
                target=self._run,
                args=(source_id, stop),
                name=f"beacon-custom-{source_id[:12]}",
                daemon=True,
            )
            with self._lock:
                self._workers[source_id] = (stop, thread)
                self._statuses[source_id] = "正在启动"
            thread.start()

    def status(self, source_id: str) -> str:
        with self._lock:
            return self._statuses.get(source_id, "已停用")

    def _stop_workers(self) -> None:
        with self._lock:
            workers = list(self._workers.values())
            processes = list(self._processes.values())
            connections = list(self._connections.values())
            self._workers = {}
            self._processes = {}
            self._connections = {}
        for stop, _thread in workers:
            stop.set()
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for connection in connections:
            try:
                connection.close()
            except OSError:
                pass
        for _stop, thread in workers:
            thread.join(timeout=2)
        for process in processes:
            if process.poll() is None:
                process.kill()

    def _run(self, source_id: str, stop: threading.Event) -> None:
        while not stop.is_set():
            source = self.registry.get(source_id)
            if source is None or not source.get("enabled"):
                self._set_status(source_id, "已停用")
                return
            process: subprocess.Popen[bytes] | None = None
            connection: socket.socket | None = None
            output_thread: threading.Thread | None = None
            try:
                validate_config(source)
                connection = self._connect(source)
                command = [str(Path(source["adapter_executable"]).expanduser()), *source["adapter_args"]]
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    bufsize=0,
                    shell=False,
                )
                with self._lock:
                    self._processes[source_id] = process
                    self._connections[source_id] = connection
                assert process.stdin is not None
                process.stdin.write(
                    json.dumps(adapter_envelope(source), ensure_ascii=False).encode("utf-8") + b"\n"
                )
                process.stdin.flush()
                self._set_status(source_id, "正在运行")
                assert process.stdout is not None
                output_thread = threading.Thread(
                    target=self._read_adapter_output,
                    args=(source, process, connection, stop),
                    name=f"beacon-adapter-output-{source_id[:12]}",
                    daemon=True,
                )
                output_thread.start()

                connection.settimeout(0.5)
                while not stop.is_set() and process.poll() is None:
                    try:
                        chunk = connection.recv(65536)
                    except socket.timeout:
                        continue
                    if not chunk:
                        break
                    process.stdin.write(chunk)
                    process.stdin.flush()

                try:
                    process.stdin.close()
                except OSError:
                    pass
                try:
                    return_code = process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        return_code = process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        return_code = process.wait(timeout=1)
                if not stop.is_set():
                    self._set_status(source_id, "等待重连" if return_code == 0 else f"适配器退出（{return_code}）")
            except Exception as exc:
                if not stop.is_set():
                    self._set_status(source_id, f"启动失败：{str(exc)[:100]}")
            finally:
                if process is not None and process.stdin is not None and not process.stdin.closed:
                    try:
                        process.stdin.close()
                    except OSError:
                        pass
                if connection is not None:
                    try:
                        connection.close()
                    except OSError:
                        pass
                if process is not None and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        process.kill()
                if output_thread is not None:
                    output_thread.join(timeout=1)
                if process is not None and process.stdout is not None:
                    process.stdout.close()
                with self._lock:
                    self._processes.pop(source_id, None)
                    self._connections.pop(source_id, None)
            stop.wait(3)

    @staticmethod
    def _connect(source: dict[str, Any]) -> socket.socket:
        if source["transport"] == "unix":
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.connect(str(Path(source["socket_path"]).expanduser()))
            return connection
        return socket.create_connection((source["host"], int(source["port"])), timeout=3)

    def _read_adapter_output(
        self,
        source: dict[str, Any],
        process: subprocess.Popen[bytes],
        connection: socket.socket,
        stop: threading.Event,
    ) -> None:
        assert process.stdout is not None
        try:
            for raw_line in process.stdout:
                if stop.is_set():
                    break
                if len(raw_line) > MAX_EVENT_BYTES:
                    self._set_status(source["id"], "忽略了过大的适配器输出")
                    continue
                line = raw_line.decode("utf-8", errors="replace")
                if self._forward_control(connection, line):
                    continue
                self._consume(source, line)
        except (OSError, ValueError) as exc:
            if not stop.is_set():
                self._set_status(source["id"], f"适配器输出错误：{str(exc)[:100]}")
            try:
                connection.close()
            except OSError:
                pass

    @staticmethod
    def _forward_control(connection: socket.socket, line: str) -> bool:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            return False
        if not isinstance(value, dict) or value.get("schema") != CONTROL_SCHEMA:
            return False
        if value.get("action") != "send":
            raise ValueError("适配器控制动作无效")
        if "data_base64" in value:
            try:
                payload = base64.b64decode(str(value["data_base64"]), validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ValueError("适配器控制数据不是有效 Base64") from exc
        else:
            payload = str(value.get("data") or "").encode("utf-8")
        if len(payload) > MAX_EVENT_BYTES:
            raise ValueError("适配器控制数据过大")
        connection.sendall(payload)
        return True

    def _consume(self, source: dict[str, Any], line: str) -> None:
        if len(line.encode("utf-8", errors="ignore")) > MAX_EVENT_BYTES:
            self._set_status(source["id"], "忽略了过大的适配器事件")
            return
        try:
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise ValueError("事件不是 JSON 对象")
            state = str(raw.get("state") or "").lower()
            if state not in ALLOWED_STATES:
                raise ValueError("任务状态无效")
            external_id = str(raw.get("task_id") or "").strip()
            if not external_id:
                raise ValueError("缺少 task_id")
            event: dict[str, Any] = {
                "task_id": f"custom:{source['id']}:{external_id}"[:160],
                "title": str(raw.get("title") or "自定义任务")[:240],
                "state": state,
                "progress": raw.get("progress", 0),
                "source": self._event_source(source, raw),
            }
            for key in ("occurred_at_ms", "input_tokens", "output_tokens"):
                if key in raw:
                    event[key] = raw[key]
            if raw.get("event_key"):
                event["event_key"] = f"custom:{source['id']}:{raw['event_key']}"
            self.store.record(event)
            self._set_status(source["id"], "正在运行")
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._set_status(source["id"], f"事件格式错误：{str(exc)[:100]}")

    @staticmethod
    def _event_source(source: dict[str, Any], raw: dict[str, Any]) -> str:
        """Keep custom-source attribution while allowing an adapter to name its agent."""
        source_name = str(source.get("name") or "自定义任务").strip()[:80]
        agent = re.sub(r"[\x00-\x1f\x7f]+", " ", str(raw.get("agent") or "")).strip()
        return f"{source_name}-{agent}"[:80] if agent else source_name

    def _set_status(self, source_id: str, status: str) -> None:
        with self._lock:
            self._statuses[source_id] = status
