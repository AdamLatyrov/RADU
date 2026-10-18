from __future__ import annotations

import json
import sys
import threading
from typing import Any


class EngineService:
    """Small JSON-lines boundary between the Python engine and its host UI."""

    def __init__(
        self,
        stop_event: threading.Event,
        listening_event: threading.Event,
        reload_event: threading.Event,
        enabled: bool,
    ) -> None:
        self.enabled = enabled
        self._stop_event = stop_event
        self._listening_event = listening_event
        self._reload_event = reload_event
        self._write_lock = threading.Lock()
        self._action_feedback_ack = threading.Event()

    def start(self) -> None:
        if self.enabled:
            threading.Thread(target=self._read_controls, daemon=True).start()

    def emit(self, event: str, **values: Any) -> None:
        if not self.enabled:
            return
        message = json.dumps({"event": event, **values}, ensure_ascii=False)
        with self._write_lock:
            print(message, file=sys.stdout, flush=True)

    def begin_action(self) -> None:
        if not self.enabled:
            return
        self._action_feedback_ack.clear()
        self.emit("action_starting")
        self._action_feedback_ack.wait(timeout=2.0)

    def _read_controls(self) -> None:
        try:
            for line in sys.stdin:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(message, dict):
                    continue

                action = message.get("action")
                if action == "stop":
                    return
                if action == "listen":
                    enabled = message.get("enabled") is True
                    if enabled:
                        self._listening_event.set()
                    else:
                        self._listening_event.clear()
                    self.emit("listening", value=enabled)
                elif action == "reload_commands":
                    self._reload_event.set()
                elif action == "action_starting_ack":
                    self._action_feedback_ack.set()
        finally:
            self._action_feedback_ack.set()
            self._stop_event.set()
