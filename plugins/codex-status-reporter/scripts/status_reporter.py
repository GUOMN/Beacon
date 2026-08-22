from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


def default_task_id(title: str) -> str:
    """优先采用宿主会话 ID；没有时使用工作目录与标题的稳定摘要。"""
    for name in ("STATUS_TASK_ID", "CODEX_THREAD_ID", "CLAUDE_SESSION_ID"):
        value = os.getenv(name, "").strip()
        if value:
            return value
    seed = f"{Path.cwd().resolve()}\0{title}".encode("utf-8", errors="replace")
    return hashlib.sha256(seed).hexdigest()[:24]


def post_event(payload: dict[str, object]) -> None:
    """尽力上报；任何网络或服务异常都吞掉，绝不让原任务失败。"""
    endpoint = os.getenv("STATUS_BRIDGE_URL", "http://127.0.0.1:8765/v1/events")
    token = os.getenv("STATUS_BRIDGE_TOKEN", os.getenv("CODEX_STATUS_TOKEN", ""))
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=0.25):
            pass
    except Exception:
        pass


def spawn_worker(payload: dict[str, object]) -> None:
    """把网络发送交给独立子进程，调用方无需等待结果。"""
    command = [sys.executable, str(Path(__file__).resolve()), "--worker", json.dumps(payload)]
    options: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        options["start_new_session"] = True
    try:
        subprocess.Popen(command, **options)
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Non-blocking task status reporter")
    parser.add_argument("state", nargs="?", choices=("running", "waiting", "success", "warning", "failure"))
    parser.add_argument("--task-id")
    parser.add_argument("--title", default="Agent task")
    parser.add_argument("--progress", type=int, default=0)
    parser.add_argument("--source", default=os.getenv("STATUS_SOURCE", "agent"))
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        try:
            post_event(json.loads(args.worker))
        except Exception:
            pass
        return 0
    payload = {
        "task_id": args.task_id or default_task_id(args.title),
        "title": args.title,
        "state": args.state,
        "progress": max(0, min(100, args.progress)),
        "source": args.source,
        "occurred_at_ms": time.time_ns() // 1_000_000,
    }
    spawn_worker(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
