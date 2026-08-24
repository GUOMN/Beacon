import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_status_core.event_store import StatusEventStore
from codex_status_core.codex_session_source import CodexSessionSource
from codex_status_core.hook_manager import HookProvider, install, status, uninstall
from tauri_bridge import data_sources, manage_tasks, set_data_source


class EventPipelineTests(unittest.TestCase):
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
                self.assertIn("hooks = true", config_path.read_text(encoding="utf-8"))
                self.assertIn("existing-notifier.exe", config_path.read_text(encoding="utf-8"))
                uninstall(provider)
            restored = json.loads(hooks_path.read_text(encoding="utf-8"))
            self.assertEqual(restored["hooks"]["Stop"][0]["hooks"][0]["command"], "existing")
            self.assertNotIn("PermissionRequest", restored["hooks"])

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
            with patch("tauri_bridge.hook_providers", return_value=(provider,)):
                enabled = set_data_source({"key": "codex", "enabled": True})
                self.assertTrue(enabled["sources"][0]["enabled"])
                self.assertTrue(enabled["sources"][0]["manageable"])
                disabled = set_data_source({"key": "codex", "enabled": False})
                self.assertFalse(disabled["sources"][0]["enabled"])
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
