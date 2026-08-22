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


if __name__ == "__main__":
    unittest.main()
