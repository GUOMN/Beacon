import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_status_core.event_store import StatusEventStore
from codex_status_core.hook_manager import HookProvider, install, status, uninstall


class EventPipelineTests(unittest.TestCase):
    def test_sqlite_snapshot_uses_latest_task_state(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            store = StatusEventStore(Path(folder) / "events.sqlite")
            store.record({"task_id": "one", "title": "任务一", "state": "running", "occurred_at_ms": 1})
            store.record({"task_id": "one", "title": "任务一", "state": "success", "occurred_at_ms": 2})
            snapshot = store.snapshot(2)
            self.assertEqual(snapshot.tasks[0].state.value, 3)
            self.assertEqual(len(snapshot.tasks), 2)

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

    def test_codex_notify_chains_and_restores_existing_callback(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            path = Path(folder) / "config.toml"
            original = ["existing-notifier.exe", "turn-ended"]
            path.write_text("notify = " + json.dumps(original) + "\n[features]\njs_repl = false\n", encoding="utf-8")
            provider = HookProvider("codex", "Codex", path, (("notify", "success"),))
            with patch("codex_status_core.hook_manager._codex_backup_path", return_value=Path(folder) / "backup.json"):
                install(provider)
                self.assertEqual(status(provider), "已启用")
                self.assertIn("--status-bridge-codex-notify", path.read_text(encoding="utf-8"))
                uninstall(provider)
            restored = path.read_text(encoding="utf-8")
            self.assertIn("existing-notifier.exe", restored)
            self.assertIn("[features]", restored)

    def test_task_order_and_hidden_state_drive_lamp_snapshot(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            store = StatusEventStore(Path(folder) / "events.sqlite")
            for task_id in ("one", "two", "three"):
                store.record({"task_id": task_id, "title": task_id, "state": "running"})
            store.reorder_tasks(["three", "one", "two"])
            self.assertEqual([item["task_id"] for item in store.latest_records()], ["three", "one", "two"])
            store.delete_tasks(["one"])
            self.assertEqual([item.title for item in store.snapshot(2).tasks], ["three", "two"])
            self.assertNotIn("one", [item["task_id"] for item in store.latest_records()])


if __name__ == "__main__":
    unittest.main()
