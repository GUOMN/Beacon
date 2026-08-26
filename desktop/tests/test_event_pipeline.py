import json
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from codex_status_core.event_store import BridgeSnapshot, StatusEventStore
from codex_status_core.codex_session_source import CodexSessionSource
from codex_status_core.custom_source import (
    CustomSourceRegistry,
    CustomSourceSupervisor,
    adapter_envelope,
    normalize_config,
    validate_config,
)
from codex_status_core.hook_manager import HookProvider, install, status, uninstall
from codex_status_core.hook_adapter import _hook_payload_failed
from codex_status_core.models import TaskSlot
from tauri_bridge import (
    apply_device, data_sources, delete_custom_source, manage_tasks, save_custom_source,
    set_data_source, settings, save_settings,
)


class EventPipelineTests(unittest.TestCase):
    def test_metric_cards_use_existing_settings_and_accept_local_options_only(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            path = Path(folder) / "settings.json"
            with patch("tauri_bridge._settings_path", return_value=path):
                result = save_settings({"metric_cards": ["five_hour_tokens", "today_task_count"]})
                self.assertEqual(result["metric_cards"], ["five_hour_tokens", "today_task_count"])
                self.assertEqual(settings()["metric_cards"], ["five_hour_tokens", "today_task_count"])
                with self.assertRaisesRegex(ValueError, "最多"):
                    save_settings({"metric_cards": ["five_hour_tokens", "seven_day_tokens", "system_busy", "today_task_count"]})

    def test_connect_target_does_not_change_saved_binding_before_validation(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            settings_path = Path(folder) / "settings.json"
            settings_path.write_text(json.dumps({"bound_device_id": "OLD001"}), encoding="utf-8")
            store = Mock()
            store.snapshot.return_value = BridgeSnapshot(
                tasks=[TaskSlot("空闲") for _ in range(5)], busy_percent=0
            )
            store.usage_totals.return_value = (0, 0)
            with patch("tauri_bridge._settings_path", return_value=settings_path), patch(
                "tauri_bridge.StatusEventStore", return_value=store
            ):
                result = apply_device({"target_device_id": "NEW002", "_native_transport": True})
            self.assertEqual(result["device_id"], "NEW002")
            self.assertEqual(json.loads(settings_path.read_text(encoding="utf-8"))["bound_device_id"], "OLD001")

    def test_custom_source_registry_stays_in_local_config_file(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            path = Path(folder) / "custom-sources.json"
            registry = CustomSourceRegistry(path)
            source = registry.save({
                "name": "本地工具",
                "transport": "unix",
                "socket_path": "/private/local/tool.socket",
                "adapter_executable": "",
                "enabled": False,
            })
            self.assertEqual(registry.get(source["id"])["socket_path"], "/private/local/tool.socket")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["version"], 1)

    def test_custom_tcp_source_rejects_non_loopback_host(self) -> None:
        source = normalize_config({"transport": "tcp", "host": "192.168.1.8", "enabled": False})
        with self.assertRaisesRegex(ValueError, "回环地址"):
            validate_config(source, require_complete=False)

    def test_custom_adapter_contract_contains_only_generic_endpoint_metadata(self) -> None:
        source = normalize_config({
            "id": "local-source",
            "transport": "tcp",
            "host": "127.0.0.1",
            "port": 9000,
            "adapter_config_path": "/private/local/adapter.json",
        })
        envelope = adapter_envelope(source)
        self.assertEqual(envelope["schema"], "beacon.custom-source/2")
        self.assertNotIn("endpoint", envelope)
        self.assertNotIn("protocol", envelope)

    def test_custom_adapter_events_are_namespaced_and_normalized(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            store = StatusEventStore(Path(folder) / "events.sqlite")
            registry = CustomSourceRegistry(Path(folder) / "custom-sources.json")
            supervisor = CustomSourceSupervisor(store, registry)
            source = normalize_config({"id": "private", "name": "本地工具"})
            supervisor._consume(source, json.dumps({
                "task_id": "task-1", "title": "转换后任务", "state": "waiting", "progress": 25,
                "agent": "worker-a",
            }))
            record = store.latest_records()[0]
            self.assertEqual(record["task_id"], "custom:private:task-1")
            self.assertEqual(record["source"], "本地工具-worker-a")
            self.assertEqual(record["state"], "waiting")

    def test_custom_source_supervisor_runs_adapter_without_a_shell(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            root = Path(folder)
            adapter = root / "adapter.py"
            adapter.write_text(
                "import json, sys\n"
                "config = json.loads(sys.stdin.readline())\n"
                "assert config['schema'] == 'beacon.custom-source/2'\n"
                "for line in sys.stdin:\n"
                "    message = json.loads(line)\n"
                "    if message.get('type') == 'hello':\n"
                "        print(json.dumps({'schema':'beacon.adapter-control/1','action':'send','data':'subscribe\\n'}), flush=True)\n"
                "    elif message.get('type') == 'update':\n"
                "        print(json.dumps({'task_id':'one','state':'running'}), flush=True)\n",
                encoding="utf-8",
            )
            server_socket, beacon_socket = socket.socketpair()
            received: list[bytes] = []

            def serve() -> None:
                with server_socket:
                    server_socket.sendall(b'{"type":"hello"}\n')
                    received.append(server_socket.recv(1024))
                    server_socket.sendall(b'{"type":"update"}\n')

            server = threading.Thread(target=serve, daemon=True)
            server.start()
            store = StatusEventStore(root / "events.sqlite")
            registry = CustomSourceRegistry(root / "custom-sources.json")
            registry.save({
                "id": "adapter-test",
                "name": "测试适配器",
                "enabled": True,
                "transport": "tcp",
                "host": "127.0.0.1",
                "port": 9000,
                "adapter_executable": sys.executable,
                "adapter_args": [str(adapter)],
            })
            supervisor = CustomSourceSupervisor(store, registry)
            with patch.object(supervisor, "_connect", return_value=beacon_socket):
                supervisor.start()
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and not store.latest_records():
                    time.sleep(0.02)
                supervisor.stop()
            server.join(timeout=1)
            self.assertEqual(received, [b"subscribe\n"])
            self.assertEqual(store.latest_records()[0]["task_id"], "custom:adapter-test:one")

    def test_custom_source_bridge_can_save_toggle_and_delete(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            registry = CustomSourceRegistry(Path(folder) / "custom-sources.json")
            with patch("tauri_bridge._custom_registry", registry), patch("tauri_bridge._custom_supervisor", None):
                saved = save_custom_source({"config": {"name": "本地工具", "enabled": False}})
                custom = next(item for item in saved["sources"] if item.get("kind") == "custom")
                self.assertFalse(custom["enabled"])
                source_id = custom["config"]["id"]
                delete_custom_source({"id": source_id})
                self.assertEqual(registry.list(), [])

    def test_sqlite_snapshot_uses_latest_task_state(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            store = StatusEventStore(Path(folder) / "events.sqlite")
            store.record({"task_id": "one", "title": "任务一", "state": "running", "occurred_at_ms": 1})
            store.record({"task_id": "one", "title": "任务一", "state": "success", "occurred_at_ms": 2})
            snapshot = store.snapshot(2)
            self.assertEqual(snapshot.tasks[0].state.value, 3)
            self.assertEqual(len(snapshot.tasks), 2)

    def test_event_key_prevents_replayed_codex_events(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            store = StatusEventStore(Path(folder) / "events.sqlite")
            event = {"task_id": "same", "title": "同一任务", "state": "running", "event_key": "session:42"}
            store.record(event)
            store.record(event)
            with store._connect() as database:
                self.assertEqual(database.execute("SELECT COUNT(*) FROM task_events").fetchone()[0], 1)

    def test_database_connections_close_after_each_operation(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            store = StatusEventStore(Path(folder) / "events.sqlite")
            with store._connect() as database:
                self.assertEqual(database.execute("SELECT 1").fetchone()[0], 1)
            with self.assertRaises(sqlite3.ProgrammingError):
                database.execute("SELECT 1")

    def test_startup_does_not_fail_active_codex_live_task(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            store = StatusEventStore(Path(folder) / "events.sqlite")
            store.record({"task_id": "codex-live", "title": "Codex", "state": "running", "source": "codex-live"})
            store.record({"task_id": "hook", "title": "Hook", "state": "waiting", "source": "claude"})
            store.fail_interrupted_tasks()
            records = {record["task_id"]: record["state"] for record in store.latest_records()}
            self.assertEqual(records["codex-live"], "running")
            self.assertEqual(records["hook"], "failure")

    def test_old_summary_column_is_removed_during_migration(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            path = Path(folder) / "old.sqlite"
            with sqlite3.connect(path) as database:
                database.execute(
                    "CREATE TABLE task_events (id INTEGER PRIMARY KEY, task_id TEXT, title TEXT, state TEXT, "
                    "progress INTEGER, source TEXT, summary TEXT, occurred_at_ms INTEGER)"
                )
                database.execute(
                    "CREATE TABLE task_layout (task_id TEXT PRIMARY KEY, display_order INTEGER, hidden INTEGER)"
                )
            store = StatusEventStore(path)
            with store._connect() as database:
                columns = {row[1] for row in database.execute("PRAGMA table_info(task_events)")}
            self.assertNotIn("summary", columns)

    def test_hook_install_preserves_existing_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            path = Path(folder) / "settings.json"
            path.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "existing"}]}]}}), encoding="utf-8")
            provider = HookProvider("claude", "Claude Code", path, (("Stop", "success"),))
            install(provider)
            install(provider)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(data["hooks"]["Stop"]), 2)
            self.assertEqual(status(provider), "已启用")
            uninstall(provider)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["hooks"]["Stop"][0]["hooks"][0]["command"], "existing")

    def test_codex_hooks_preserve_existing_hooks_and_restore_legacy_notify(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            config_path = Path(folder) / "config.toml"
            hooks_path = Path(folder) / "hooks.json"
            original = ["existing-notifier.exe", "turn-ended"]
            config_path.write_text(
                "notify = [\"beacon\", \"--status-bridge-codex-notify\"]\n[features]\njs_repl = false\n",
                encoding="utf-8",
            )
            hooks_path.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "existing"}]}]}}), encoding="utf-8")
            provider = HookProvider("codex", "Codex", hooks_path, (("PermissionRequest", "waiting"), ("Stop", "success")))
            with patch("codex_status_core.hook_manager._codex_backup_path", return_value=Path(folder) / "backup.json"):
                (Path(folder) / "backup.json").write_text(json.dumps({"notify": original}), encoding="utf-8")
                install(provider)
                self.assertEqual(status(provider), "已启用")
                installed = json.loads(hooks_path.read_text(encoding="utf-8"))
                self.assertEqual(len(installed["hooks"]["Stop"]), 2)
                self.assertIn("PermissionRequest", installed["hooks"])
                permission_hook = installed["hooks"]["PermissionRequest"][0]["hooks"][0]
                self.assertEqual(permission_hook["timeout"], 86_400)
                self.assertIn("hooks = true", config_path.read_text(encoding="utf-8"))
                self.assertIn("existing-notifier.exe", config_path.read_text(encoding="utf-8"))
                uninstall(provider)
            restored = json.loads(hooks_path.read_text(encoding="utf-8"))
            self.assertEqual(restored["hooks"]["Stop"][0]["hooks"][0]["command"], "existing")
            self.assertNotIn("PermissionRequest", restored["hooks"])

    def test_codex_permission_hook_is_repaired_with_long_timeout(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            path = Path(folder) / "hooks.json"
            provider = HookProvider("codex", "Codex", path, (("PermissionRequest", "waiting"),))
            install(provider)
            install(provider)
            installed = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(installed["hooks"]["PermissionRequest"][0]["hooks"][0]["timeout"], 86_400)

    def test_hook_failure_detection_marks_rejected_post_tool_results(self) -> None:
        self.assertTrue(_hook_payload_failed({"success": False}))
        self.assertTrue(_hook_payload_failed({"result": "Permission denied by user"}))
        self.assertFalse(_hook_payload_failed({"success": True, "result": "ok"}))

    def test_data_source_management_reports_and_changes_real_hook_state(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            provider = HookProvider(
                "claude", "Claude Code", Path(folder) / "settings.json", (("Stop", "success"),)
            )
            with patch("tauri_bridge.hook_providers", return_value=(provider,)):
                self.assertFalse(data_sources()["sources"][0]["enabled"])
                enabled = set_data_source({"key": "claude", "enabled": True})
                self.assertTrue(enabled["sources"][0]["enabled"])
                disabled = set_data_source({"key": "claude", "enabled": False})
                self.assertFalse(disabled["sources"][0]["enabled"])

    def test_codex_hook_subscription_can_be_enabled_and_disabled(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            provider = HookProvider(
                "codex", "Codex", Path(folder) / "hooks.json", (("PermissionRequest", "waiting"),)
            )
            store = StatusEventStore(Path(folder) / "events.sqlite")
            source = Mock()
            with (
                patch("tauri_bridge.hook_providers", return_value=(provider,)),
                patch("tauri_bridge._runtime_store", store),
                patch("tauri_bridge._codex_source", None),
                patch("tauri_bridge.CodexSessionSource", return_value=source),
            ):
                enabled = set_data_source({"key": "codex", "enabled": True})
                self.assertTrue(enabled["sources"][0]["enabled"])
                self.assertTrue(enabled["sources"][0]["manageable"])
                source.start.assert_called_once_with()
                disabled = set_data_source({"key": "codex", "enabled": False})
                self.assertFalse(disabled["sources"][0]["enabled"])
                source.stop.assert_called_once_with()
            contents = json.loads((Path(folder) / "hooks.json").read_text(encoding="utf-8"))
            self.assertEqual(contents["hooks"], {})

    def test_active_sort_pin_and_delete_drive_lamp_snapshot(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            store = StatusEventStore(Path(folder) / "events.sqlite")
            for task_id in ("one", "two", "three"):
                store.record({"task_id": task_id, "title": task_id, "state": "running"})
            store.reorder_tasks(["three", "one", "two"])
            store.set_pinned("three", True)
            self.assertEqual([item["task_id"] for item in store.latest_records()], ["three", "one", "two"])
            store.update_task_usage("one", 100, 20, 120, 1000)
            store.delete_tasks(["one"])
            self.assertEqual([item.title for item in store.snapshot(2).tasks], ["three", "two"])
            self.assertNotIn("one", [item["task_id"] for item in store.latest_records()])
            with store._connect() as database:
                self.assertEqual(database.execute("SELECT COUNT(*) FROM task_usage WHERE task_id='one'").fetchone()[0], 0)

    def test_delete_completed_keeps_pinned_tasks(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            store = StatusEventStore(Path(folder) / "events.sqlite")
            store.record({"task_id": "pinned-success", "title": "固定完成", "state": "success"})
            store.record({"task_id": "plain-success", "title": "普通完成", "state": "success"})
            store.record({"task_id": "running", "title": "进行中", "state": "running"})
            store.set_pinned("pinned-success", True)
            with patch("tauri_bridge.StatusEventStore", return_value=store):
                manage_tasks({"operation": "delete-completed"})
            self.assertEqual(
                {record["task_id"] for record in store.latest_records()},
                {"pinned-success", "running"},
            )

    def test_codex_live_events_map_to_task_states(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            store = StatusEventStore(Path(folder) / "events.sqlite")
            source = CodexSessionSource(store, lambda _message: None)
            source._titles["thread"] = "灯板任务"
            source._consume("thread", {"type": "event_msg", "payload": {"type": "user_message", "message": "实现 蓝牙 状态灯"}})
            source._consume("thread", {"type": "event_msg", "payload": {"type": "task_started"}})
            record = store.latest_records()[0]
            self.assertEqual(record["state"], "running")
            source._consume("thread", {"type": "event_msg", "payload": {"type": "task_complete"}})
            self.assertEqual(store.latest_records()[0]["state"], "success")

    def test_codex_live_events_mark_approval_and_tool_failures(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            store = StatusEventStore(Path(folder) / "events.sqlite")
            source = CodexSessionSource(store, lambda _message: None)
            source._titles["thread"] = "灯板任务"
            source._consume("thread", {"type": "event_msg", "payload": {"type": "task_started"}})
            source._consume("thread", {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "call_id": "approval-call",
                    "input": '{"sandbox_permissions":"require_escalated"}',
                },
            })
            self.assertEqual(store.latest_records()[0]["state"], "waiting")
            source._consume("thread", {
                "type": "response_item",
                "payload": {"type": "custom_tool_call_output", "call_id": "approval-call", "output": [{"type": "input_text", "text": "Approval denied"}]},
            })
            self.assertEqual(store.latest_records()[0]["state"], "failure")

            source._consume("thread", {
                "type": "response_item",
                "payload": {"type": "custom_tool_call", "call_id": "failed-call", "input": "{}"},
            })
            source._consume("thread", {
                "type": "response_item",
                "payload": {"type": "custom_tool_call_output", "call_id": "failed-call", "output": {"exit_code": 1}},
            })
            self.assertEqual(store.latest_records()[0]["state"], "failure")


if __name__ == "__main__":
    unittest.main()
