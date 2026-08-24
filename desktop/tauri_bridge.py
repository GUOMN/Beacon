"""JSON bridge from the Tauri shell to the existing Python core."""
from __future__ import annotations

import json
import sys
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
from codex_status_core.hook_manager import install as install_hook
from codex_status_core.hook_manager import providers as hook_providers
from codex_status_core.hook_manager import status as hook_status
from codex_status_core.hook_manager import uninstall as uninstall_hook
from codex_status_core.models import DashboardSnapshot, StateStyle, TaskSlot, TaskState

DEFAULT_STYLES = {
    "running": {"color": "#5B8FF9", "effect": 3, "frequency": 50, "duty": 50, "automatic": True},
    "waiting": {"color": "#F6BD4B", "effect": 2, "frequency": 60, "duty": 20, "automatic": True},
    "success": {"color": "#5AC49D", "effect": 1, "frequency": 30, "duty": 100, "automatic": False},
    "warning": {"color": "#F38B75", "effect": 2, "frequency": 90, "duty": 25, "automatic": True},
    "failure": {"color": "#D96770", "effect": 2, "frequency": 120, "duty": 20, "automatic": True},
}
DEFAULT_BUSY_WEIGHTS = {"task": 30, "token": 20, "cpu": 20, "memory": 10, "disk": 10, "network": 10}
_preview_payload: dict[str, object] | None = None


def _weighted_busy(values: tuple[float, ...], weights: tuple[float, ...]) -> int:
    safe_weights = [max(0.0, value) for value in weights]
    total = sum(safe_weights)
    return 0 if total <= 0 else round(sum(max(0.0, min(100.0, value)) * weight for value, weight in zip(values, safe_weights)) / total)


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
    return result


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
            if record.get("state") == "success" and not bool(record.get("pinned"))
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


def data_sources(_payload: dict[str, object] | None = None) -> dict[str, object]:
    """Return real hook state instead of the desktop UI's former placeholders."""
    result: list[dict[str, object]] = []
    for provider in hook_providers():
        if provider.key == "codex":
            result.append({
                "key": provider.key,
                "name": provider.name,
                "status": "正在运行",
                "enabled": True,
                "manageable": False,
                "note": "内置实时会话采集，随 Beacon 自动启动",
            })
            continue
        current = hook_status(provider)
        result.append({
            "key": provider.key,
            "name": provider.name,
            "status": current,
            "enabled": current == "已启用",
            "manageable": provider.supported,
            "note": provider.note or "通过官方 Hook 将任务状态写入本机 Beacon",
        })
    return {"sources": result}


def set_data_source(payload: dict[str, object]) -> dict[str, object]:
    key = str(payload.get("key") or "")
    selected = next((provider for provider in hook_providers() if provider.key == key), None)
    if selected is None:
        raise ValueError("未知的数据源")
    if selected.key == "codex":
        raise ValueError("Codex 是 Beacon 内置数据源，始终随客户端运行")
    if bool(payload.get("enabled")):
        install_hook(selected)
    else:
        uninstall_hook(selected)
    return data_sources()


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
    if payload.get("_native_transport"):
        from codex_status_core.protocol import BLEProtocol
        sequence = 0
        packets: list[bytes] = []
        def add(packet_factory) -> None:
            nonlocal sequence
            sequence = (sequence + 1) & 0xFF
            packets.append(packet_factory(sequence))
        add(lambda seq: BLEProtocol.encode_led_count(seq, len(snapshot.tasks) + 1))
        add(lambda seq: BLEProtocol.encode_sleep_timeout(seq, snapshot.sleep_timeout_minutes))
        add(lambda seq: BLEProtocol.encode_channel_count(seq, snapshot.output_channels))
        add(lambda seq: BLEProtocol.encode_system_effect(seq, snapshot.system_effect))
        for state, style in snapshot.state_styles.items():
            add(lambda seq, state=state, style=style: BLEProtocol.encode_state_style(
                seq, int(state), style.color, style.effect, style.period_ms, style.blink_duty_percent
            ))
        add(lambda seq: BLEProtocol.encode_panel_header(seq, snapshot))
        for task_index, task in enumerate(snapshot.tasks):
            add(lambda seq, task_index=task_index, task=task: BLEProtocol.encode_task_state(
                seq, task_index, task, include_timing_mode=True
            ))
        return {
            "ok": True,
            "settings": current,
            "device_id": device_id,
            "packets": [base64.b64encode(packet).decode("ascii") for packet in packets],
        }
    raise RuntimeError("设备传输必须由 Tauri 原生蓝牙服务执行")


def main() -> int:
    if len(sys.argv) >= 4 and sys.argv[1] == "--status-bridge-hook":
        from codex_status_core.hook_adapter import report_hook
        return report_hook(sys.argv[2], sys.argv[3])
    if len(sys.argv) >= 3 and sys.argv[1] == "--status-bridge-codex-notify":
        from codex_status_core.hook_adapter import report_codex_notification
        return report_codex_notification(sys.argv[2])
    command = sys.argv[1] if len(sys.argv) > 1 else "dashboard"
    handlers = {
        "dashboard": lambda _p: dashboard(),
        "settings": lambda _p: settings(),
        "save-settings": save_settings,
        "manage-tasks": manage_tasks,
        "data-sources": data_sources,
        "set-data-source": set_data_source,
        "apply-device": apply_device,
    }
    if command == "serve":
        store = StatusEventStore()
        store.fail_interrupted_tasks()
        event_server = EventIngestServer(store)
        event_server.start()
        codex_source = CodexSessionSource(store, lambda _message: None)
        codex_source.start()
        try:
            for line in sys.stdin:
                try:
                    request = json.loads(line)
                    selected = str(request.get("command", ""))
                    payload = request.get("payload", {})
                    result = handlers[selected](payload)
                    response = {"ok": result}
                except Exception as exc:
                    response = {"error": str(exc)}
                print(json.dumps(response, ensure_ascii=False), flush=True)
        finally:
            codex_source.stop()
            event_server.stop()
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
