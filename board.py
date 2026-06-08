"""Shared task-board (交換區/任務看板) store — experimental v1.

A tiny file-backed store both main.py (RPC + UI) and bridge_telegram.py
(harness marker inject) read/write. Kept as its own module to avoid a
circular import between main and the bridge, and mirrors the atomic-write
+ lock style of main.py's config persistence.

State lives at ~/.local/state/shellframe/board.json:
    {"version": 1, "tasks": [ {task}, ... ]}

A task:
    id          short hex id
    title       str
    assignee    agent tab label, or "unassigned"
    status      todo | assigned | in_progress | done
    difficulty  easy | medium | hard
    created_at  epoch seconds
    updated_at  epoch seconds
    notes       free text
"""
import json
import os
import threading
import time
import uuid
from pathlib import Path

STATE_DIR = Path.home() / ".local" / "state" / "shellframe"
BOARD_FILE = STATE_DIR / "board.json"

_VALID_STATUS = ("todo", "assigned", "in_progress", "done")
_VALID_DIFFICULTY = ("easy", "medium", "hard")

_lock = threading.RLock()


def _now() -> int:
    return int(time.time())


def load_board() -> dict:
    """Read board state. Never raises — a missing/corrupt file yields empty."""
    with _lock:
        try:
            with open(BOARD_FILE, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("board root not an object")
            data.setdefault("version", 1)
            tasks = data.get("tasks")
            if not isinstance(tasks, list):
                data["tasks"] = []
            return data
        except (OSError, ValueError, json.JSONDecodeError):
            return {"version": 1, "tasks": []}


def save_board(data: dict) -> None:
    """Atomic write (tmp + os.replace), mirroring main.save_config."""
    with _lock:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = str(BOARD_FILE) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, str(BOARD_FILE))


def list_tasks() -> list:
    """Tasks ordered for display: open work first (todo/assigned/in_progress
    by that rank), done last; newest-first within a rank."""
    rank = {"in_progress": 0, "assigned": 1, "todo": 2, "done": 3}
    tasks = load_board().get("tasks", [])
    return sorted(
        tasks,
        key=lambda t: (rank.get(t.get("status"), 2), -int(t.get("created_at", 0))),
    )


def _norm_status(value: str, default: str = "todo") -> str:
    value = (value or "").strip().lower()
    return value if value in _VALID_STATUS else default


def _norm_difficulty(value: str, default: str = "medium") -> str:
    value = (value or "").strip().lower()
    return value if value in _VALID_DIFFICULTY else default


def add_task(title: str, assignee: str = "unassigned", status: str = "todo",
             difficulty: str = "medium", notes: str = "") -> dict:
    title = (title or "").strip()
    if not title:
        raise ValueError("title required")
    with _lock:
        data = load_board()
        task = {
            "id": uuid.uuid4().hex[:8],
            "title": title[:200],
            "assignee": (assignee or "unassigned").strip() or "unassigned",
            "status": _norm_status(status),
            "difficulty": _norm_difficulty(difficulty),
            "created_at": _now(),
            "updated_at": _now(),
            "notes": (notes or "").strip(),
        }
        data["tasks"].append(task)
        save_board(data)
        return task


def update_task(task_id: str, **fields) -> dict | None:
    """Patch a task by id. Accepts title/assignee/status/difficulty/notes.
    Returns the updated task, or None if id not found."""
    task_id = (task_id or "").strip()
    if not task_id:
        return None
    with _lock:
        data = load_board()
        for task in data["tasks"]:
            if task.get("id") != task_id:
                continue
            if "title" in fields and fields["title"]:
                task["title"] = str(fields["title"]).strip()[:200]
            if "assignee" in fields and fields["assignee"]:
                task["assignee"] = str(fields["assignee"]).strip() or "unassigned"
            if "status" in fields and fields["status"]:
                task["status"] = _norm_status(fields["status"], task.get("status", "todo"))
            if "difficulty" in fields and fields["difficulty"]:
                task["difficulty"] = _norm_difficulty(fields["difficulty"], task.get("difficulty", "medium"))
            if "notes" in fields and fields["notes"] is not None:
                task["notes"] = str(fields["notes"]).strip()
            task["updated_at"] = _now()
            save_board(data)
            return task
        return None


def remove_task(task_id: str) -> bool:
    task_id = (task_id or "").strip()
    if not task_id:
        return False
    with _lock:
        data = load_board()
        before = len(data["tasks"])
        data["tasks"] = [t for t in data["tasks"] if t.get("id") != task_id]
        if len(data["tasks"]) != before:
            save_board(data)
            return True
        return False
