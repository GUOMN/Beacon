"""JSON bridge from the Tauri shell to the existing Python core."""
from __future__ import annotations

import json
import sys
import threading
import asyncio
import base64
from pathlib import Path
try:
    import psutil
except ImportError:  # Task viewing still works before optional system metrics are installed.
    psutil = None

from codex_status_core.event_store import StatusEventStore
from codex_status_core.event_store import app_data_directory
from codex_status_core.event_store import EventIngestServer
from codex_status_core.codex_session_source import CodexSessionSource
from codex_status_core.models import DashboardSnapshot, StateStyle, TaskSlot, TaskState

DEFAULT_STYLES = {
    "running": {"color": "#5B8FF9", "effect": 3, "frequency": 50, "duty": 50, "automatic": True},
    "waiting": {"color": "#F6BD4B", "effect": 2, "frequency": 60, "duty": 20, "automatic": True},
    "success": {"color": "#5AC49D", "effect": 1, "frequency": 30, "duty": 100, "automatic": False},
    "warning": {"color": "#F38B75", "effect": 2, "frequency": 90, "duty": 25, "automatic": True},
    "failure": {"color": "#D96770", "effect": 2, "frequency": 120, "duty": 20, "automatic": True},
}
DEFAULT_BUSY_WEIGHTS = {"task": 30, "token": 20, "cpu": 20, "memory": 10, "disk": 10, "network": 10}
_persistent_worker = None
_persistent_device_id: str | None = None
_preview_payload: dict[str, object] | None = None
_last_live_signature: str | None = None


def _weighted_busy(values: tuple[float, ...], weights: tuple[float, ...]) -> int:
    safe_weights = [max(0.0, value) for value in weights]
    total = sum(safe_weights)
    return 0 if total <= 0 else round(sum(max(0.0, min(100.0, value)) * weight for value, weight in zip(values, safe_weights)) / total)


def _ensure_persistent_worker(device_id: str | None) -> None:
    global _persistent_worker, _persistent_device_id
    normalized = str(device_id or "").upper() or None
    if normalized == _persistent_device_id and _persistent_worker is not None:
        return
    if _persistent_worker is not None:
        _persistent_worker.stop()
        _persistent_worker = None
    _persistent_device_id = normalized
    if normalized:
        from windows_app.ble_worker import BLEWorker
        _persistent_worker = BLEWorker(lambda _message: None, normalized)
        _persistent_worker.start()


# Tauri exchanges JSON as UTF-8. Windows console defaults can otherwise encode
# Chinese task titles with the active code page and make serde_json reject them.
if hasattr(sys.stdin, "reconfigure"):
    sys.stdin.reconfigure(encoding="utf-8")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def _resource_values(store: StatusEventStore, task_busy: int, current: dict[str, object]) -> tuple[int, dict[str, int]]:
    cpu = round(psutil.cpu_percent(interval=0.05)) if psutil else 0
    memory = round(psutil.virtual_memory().percent) if psutil else 0
    disk = round(psutil.disk_usage(str(Path.home().anchor or "/")).percent) if psutil else 0
    network = 0
    five_hours, _ = store.usage_totals()
    token = min(100, round(five_hours / 10000))
    weights = current["busy_weights"]
    busy = _weighted_busy((task_busy, token, cpu, memory, disk, network), tuple(float(weights[key]) for key in ("task", "token", "cpu", "memory", "disk", "network")))
    available = {"CPU 可用程度": 100-cpu, "内存可用程度": 100-memory, "磁盘可用程度": 100-disk, "账号余量": 100}
    return busy, available


def dashboard() -> dict[str, object]:
    global _last_live_signature
    store = StatusEventStore()
    snapshot = store.snapshot(63)
    five_hours, seven_days = store.usage_totals()
    busy, _ = _resource_values(store, snapshot.busy_percent, settings())
    result = {
        "tasks": store.latest_records(63),
        "metrics": {
            "busy_percent": busy,
            "five_hour_tokens": five_hours,
            "seven_day_tokens": seven_days,
        },
    }
    live_signature = json.dumps({"tasks": result["tasks"], "busy": busy, "settings": settings()}, ensure_ascii=False, sort_keys=True)
    if _persistent_worker is not None and _preview_payload is None and live_signature != _last_live_signature:
        apply_device({})
        _last_live_signature = live_signature
    return result


def scan_devices() -> dict[str, object]:
    # BLE is optional for task viewing; import it only when the user scans.
    from windows_app.ble_worker import scan_status_devices

    done = threading.Event()
    result: list[dict[str, object]] = []

    def receive(devices: list[dict[str, object]]) -> None:
        result.extend(devices)
        done.set()

    scan_status_devices(receive, lambda _message: None)
    if not done.wait(25):
        raise TimeoutError("蓝牙扫描超时")
    # A connected BLE peripheral normally stops advertising, so an ordinary
    # discovery pass cannot see the device that this process already owns.
    # Keep the bound board visible in Device Manager and expose its live state.
    bound_device_id = str(settings().get("bound_device_id") or "").upper()
    if bound_device_id and not any(str(item.get("device_id", "")).upper() == bound_device_id for item in result):
        connected = bool(_persistent_worker is not None and _persistent_worker.is_connected)
        result.insert(0, {
            "name": f"Codex-Light-{bound_device_id}",
            "device_id": bound_device_id,
            "address": "",
            "rssi": None,
            "connected": connected,
        })
    for item in result:
        item.setdefault("connected", bool(
            _persistent_worker is not None
            and _persistent_worker.is_connected
            and str(item.get("device_id", "")).upper() == bound_device_id
        ))
    return {"devices": result}


def _settings_path() -> Path:
    return app_data_directory() / "settings.json"


def settings() -> dict[str, object]:
    try:
        data = json.loads(_settings_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    styles = {key: {**value, **data.get("state_styles", {}).get(key, {})} for key, value in DEFAULT_STYLES.items()}
    weights = {key: max(0, min(100, int(data.get("busy_weights", {}).get(key, value)))) for key, value in DEFAULT_BUSY_WEIGHTS.items()}
    return {"bound_device_id": data.get("bound_device_id"), "brightness": int(data.get("master_brightness", 60)), "sleep_minutes": int(data.get("sleep_timeout_minutes", 10)), "led_count": int(data.get("total_led_count", 6)), "output_channels": int(data.get("output_channels", 1)), "styles": styles, "busy_weights": weights, "system_color_source": str(data.get("system_color_source", "账号余量")), "system_effect": int(data.get("system_effect", 4))}


def save_settings(payload: dict[str, object]) -> dict[str, object]:
    path = _settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    for key in ("bound_device_id", "master_brightness", "sleep_timeout_minutes", "total_led_count", "output_channels", "state_styles", "busy_weights", "system_color_source", "system_effect"):
        if key in payload:
            data[key] = payload[key]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return settings()


def manage_tasks(payload: dict[str, object]) -> dict[str, object]:
    """Persist task list operations used by both desktop shells."""
    store = StatusEventStore()
    operation = str(payload.get("operation") or "")
    task_ids = [str(value) for value in payload.get("task_ids", []) if value]
    if operation == "delete":
        store.delete_tasks(task_ids)
    elif operation == "delete-completed":
        completed = [
            str(record["task_id"])
            for record in store.latest_records(500)
            if record.get("state") == "success"
        ]
        store.delete_tasks(completed)
    elif operation == "pin":
        task_id = str(payload.get("task_id") or "")
        if task_id:
            store.set_pinned(task_id, bool(payload.get("pinned")))
    elif operation == "reorder":
        store.reorder_tasks(task_ids)
        dragged_id = str(payload.get("dragged_id") or "")
        target_slot = payload.get("target_slot")
        if dragged_id and target_slot is not None:
            store.assign_task_to_slot(dragged_id, int(target_slot))
    else:
        raise ValueError("未知的任务管理操作")
    return {"ok": True}


async def _connected(device_id: str):
    from bleak import BleakClient, BleakScanner
    from codex_status_core.protocol import BLEProtocol
    device = await BleakScanner.find_device_by_filter(lambda found, adv: BLEProtocol.device_id_from_name(adv.local_name or found.name) == device_id.upper(), timeout=8)
    if device is None:
        raise RuntimeError("没有发现已绑定灯板")
    return BleakClient(device)


def apply_device(payload: dict[str, object]) -> dict[str, object]:
    global _preview_payload
    if payload.get("preview") is True:
        _preview_payload = dict(payload)
    elif payload.get("preview") is False:
        _preview_payload = None
    current = save_settings(payload)
    device_id = str(current.get("bound_device_id") or "")
    if not device_id:
        raise RuntimeError("请先绑定灯板")
    if _persistent_device_id is not None:
        _ensure_persistent_worker(device_id)
    store = StatusEventStore()
    bridge = store.snapshot(max(1, min(63, int(current["led_count"]) - 1)))
    calculated_busy, availability = _resource_values(store, bridge.busy_percent, current)
    tasks = bridge.tasks
    if payload.get("preview"):
        state_names = {
            "idle": TaskState.IDLE, "running": TaskState.RUNNING,
            "waiting": TaskState.WAITING, "success": TaskState.SUCCESS,
            "warning": TaskState.WARNING, "failure": TaskState.FAILURE,
        }
        requested = payload.get("preview_states")
        if isinstance(requested, list):
            states = [state_names.get(str(value), TaskState.IDLE) for value in requested]
        else:
            states = [TaskState.RUNNING, TaskState.WAITING, TaskState.SUCCESS, TaskState.WARNING, TaskState.FAILURE]
        task_count = max(1, min(63, int(current["led_count"]) - 1))
        states = (states + [TaskState.IDLE] * task_count)[:task_count]
        tasks = [TaskSlot(state.chinese_name, state, 70) for state in states]
    enum_keys = {"running": TaskState.RUNNING, "waiting": TaskState.WAITING, "success": TaskState.SUCCESS, "warning": TaskState.WARNING, "failure": TaskState.FAILURE}
    state_styles: dict[TaskState, StateStyle] = {}
    automatic_states: set[TaskState] = set()
    for key, raw in dict(current["styles"]).items():
        color = str(raw["color"]).lstrip("#")
        frequency = max(6, min(300, int(raw["frequency"])))
        state_styles[enum_keys[key]] = StateStyle(tuple(int(color[i:i + 2], 16) for i in (0, 2, 4)), int(raw["effect"]), round(60000 / frequency), int(raw["duty"]))
        if raw.get("automatic"):
            automatic_states.add(enum_keys[key])
    for task in tasks:
        task.automatic_frequency = task.state in automatic_states
        if not task.automatic_frequency and task.state in state_styles:
            task.animation_period_ms = state_styles[task.state].period_ms
    remaining = int(payload.get("remaining_percent", 75)) if payload.get("preview") else availability.get(str(current["system_color_source"]), 100)
    busy = int(payload.get("busy_percent", calculated_busy)) if payload.get("preview") else calculated_busy
    snapshot = DashboardSnapshot(remaining_percent=max(0, min(100, remaining)), period_used_percent=max(0, min(100, busy)), master_brightness_percent=max(0, min(100, int(current["brightness"]))), sleep_timeout_minutes=max(1, min(1440, int(current["sleep_minutes"]))), output_channels=int(current["output_channels"]), system_effect=max(1, min(4, int(current["system_effect"]))), state_styles=state_styles, tasks=tasks)
    if _persistent_worker is not None:
        _persistent_worker.submit(snapshot)
        return {"ok": True, "settings": current}
    async def send() -> None:
        from codex_status_core.ble_client import DashboardBLEClient
        client = await _connected(device_id)
        async with client:
            core = DashboardBLEClient(lambda _message: None, device_id)
            await core._write_snapshot(client, snapshot)
    asyncio.run(send())
    return {"ok": True, "settings": current}


def identify(payload: dict[str, object]) -> dict[str, object]:
    from windows_app.ble_worker import identify_status_device
    done = threading.Event()
    messages: list[str] = []
    def status(message: str) -> None:
        messages.append(message)
        if message.startswith(("识别命令", "识别失败")):
            done.set()
    identify_status_device(str(payload.get("address") or ""), status)
    if not done.wait(12) or messages[-1].startswith("识别失败"):
        raise RuntimeError(messages[-1] if messages else "识别超时")
    return {"ok": True}


def ota(payload: dict[str, object]) -> dict[str, object]:
    firmware = base64.b64decode(str(payload.get("firmware_base64") or ""), validate=True)
    if not firmware:
        raise ValueError("固件为空")
    if _persistent_worker is None or not _persistent_worker.is_connected:
        raise RuntimeError("已绑定灯板当前未连接，不能开始固件升级")
    _persistent_worker.submit_ota(firmware)
    return {"ok": True, "bytes": len(firmware), **_persistent_worker.ota_status}


def ota_progress() -> dict[str, object]:
    if _persistent_worker is None:
        return {"state": "error", "progress": 0, "message": "蓝牙后台未启动"}
    return _persistent_worker.ota_status


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else "dashboard"
    handlers = {"dashboard": lambda _p: dashboard(), "scan-devices": lambda _p: scan_devices(), "settings": lambda _p: settings(), "save-settings": save_settings, "manage-tasks": manage_tasks, "apply-device": apply_device, "identify": identify, "ota": ota, "ota-progress": lambda _p: ota_progress()}
    if command == "serve":
        store = StatusEventStore()
        store.fail_interrupted_tasks()
        event_server = EventIngestServer(store)
        event_server.start()
        codex_source = CodexSessionSource(store, lambda _message: None)
        codex_source.start()
        _ensure_persistent_worker(settings().get("bound_device_id"))
        try:
            for line in sys.stdin:
                try:
                    request = json.loads(line)
                    selected = str(request.get("command", ""))
                    payload = request.get("payload", {})
                    result = handlers[selected](payload)
                    if selected == "save-settings":
                        _ensure_persistent_worker(result.get("bound_device_id"))
                    response = {"ok": result}
                except Exception as exc:
                    response = {"error": str(exc)}
                print(json.dumps(response, ensure_ascii=False), flush=True)
        finally:
            codex_source.stop()
            event_server.stop()
            if _persistent_worker is not None:
                _persistent_worker.stop()
        return 0
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        print(json.dumps(handlers[command](payload), ensure_ascii=False))
        return 0
    except KeyError:
        print(json.dumps({"error": f"unknown command: {command}"}), file=sys.stderr)
        return 2
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
