from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ExecutionHistory:
    """Append one durable record after an action handler returns successfully."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def record_success(
        self,
        *,
        session_id: str,
        observation_id: str,
        command_id: str,
        transcript: str,
        action: dict[str, Any],
        word_confidence_min: float | None,
        elapsed_seconds: float,
        observation_wav: str,
        observation_json: str,
    ) -> None:
        record = {
            "executed_at_utc": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "observation_id": observation_id,
            "command_id": command_id,
            "transcript": transcript,
            "action": action,
            "word_confidence_min": word_confidence_min,
            "speech_detection_to_execution_seconds": round(elapsed_seconds, 6),
            "observation_wav": observation_wav,
            "observation_json": observation_json,
        }
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(line + "\n")
                stream.flush()
