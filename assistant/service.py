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

    def start(self) -> None:
        if self.enabled:
            threading.Thread(target=self._read_controls, daemon=True).start()

    def emit(self, event: str, **values: Any) -> None:
        if not self.enabled:
            return
        # Keep the process protocol ASCII-only so Windows code-page differences
        # cannot corrupt Russian text before the WPF JSON reader sees it.
        message = json.dumps({"event": event, **values}, ensure_ascii=True)
        with self._write_lock:
            print(message, file=sys.stdout, flush=True)

    def begin_action(self) -> None:
        if not self.enabled:
            return
        self.emit("action_starting")

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
        finally:
            self._stop_event.set()
