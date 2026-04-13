"""Minimal service module for the build-system-extension demo."""

from __future__ import annotations


class TaskQueue:
    """A simple in-memory task queue."""

    def __init__(self) -> None:
        self._tasks: list[dict] = []

    def enqueue(self, name: str, payload: dict | None = None) -> dict:
        """Add a task to the queue and return it."""
        task = {
            "id": len(self._tasks) + 1,
            "name": name,
            "payload": payload or {},
            "status": "pending",
        }
        self._tasks.append(task)
        return task

    def dequeue(self) -> dict | None:
        """Remove and return the next pending task, or None."""
        for task in self._tasks:
            if task["status"] == "pending":
                task["status"] = "processing"
                return task
        return None

    def complete(self, task_id: int) -> bool:
        """Mark a task as complete. Return True if found."""
        for task in self._tasks:
            if task["id"] == task_id:
                task["status"] = "complete"
                return True
        return False

    def pending_count(self) -> int:
        """Return the number of pending tasks."""
        return sum(1 for t in self._tasks if t["status"] == "pending")
