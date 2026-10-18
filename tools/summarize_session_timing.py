from __future__ import annotations

import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

STAGE_LABELS = {
    "last_voiced_frame_to_capture_end_seconds": "voice tail",
    "last_voiced_frame_to_decision_seconds": "voice to decision",
    "capture_end_to_decision_seconds": "capture end to decision",
    "decision_to_transcript_seconds": "decision to final text",
    "transcript_to_queue_seconds": "text to queue",
    "asr_queue_wait_seconds": "ASR queue wait",
    "capture_end_to_worker_start_seconds": "capture end to worker",
    "recognition_setup_seconds": "archive and input setup",
    "asr_seconds": "recognizer work",
    "command_checks_seconds": "command checks",
    "action_seconds": "action invocation",
    "processing_after_capture_seconds": "all processing after capture",
    "capture_end_to_action_seconds": "capture end to action",
    "result_metadata_write_seconds": "result metadata write",
}


def _percentile90_ms(values: list[float]) -> float:
    ordered = sorted(values)
    index = max(0, math.ceil(0.9 * len(ordered)) - 1)
    return round(ordered[index] * 1000, 1)


def _summarize_group(records: list[dict[str, Any]]) -> dict[str, Any]:
    metrics: dict[str, dict[str, float | int]] = {}
    for field in STAGE_LABELS:
        values = [
            float(record["timing"][field])
            for record in records
            if isinstance(record.get("timing"), dict)
            and isinstance(record["timing"].get(field), (int, float))
            and math.isfinite(float(record["timing"][field]))
        ]
        if values:
            metrics[field] = {
                "count": len(values),
                "median_ms": round(statistics.median(values) * 1000, 1),
                "p90_ms": _percentile90_ms(values),
            }
    return {"count": len(records), "metrics": metrics}


def summarize_records(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {
        "all": records,
        "accepted": [record for record in records if record.get("accepted") is True],
        "rejected": [record for record in records if record.get("accepted") is False],
    }
    command_ids = sorted(
        {str(record["command_id"]) for record in records if record.get("command_id")}
    )
    for command_id in command_ids:
        command_records = [
            record for record in records if record.get("command_id") == command_id
        ]
        groups[f"command_{command_id}"] = command_records
        groups[f"command_{command_id}_accepted"] = [
            record for record in command_records if record.get("accepted") is True
        ]
        groups[f"command_{command_id}_rejected"] = [
            record for record in command_records if record.get("accepted") is False
        ]
    return {name: _summarize_group(group) for name, group in groups.items()}


def analyze_session(path: Path) -> dict[str, Any]:
    session = json.loads(path.read_text(encoding="utf-8"))
    observations = session.get("observations")
    if not isinstance(observations, list):
        raise ValueError("session has no observations list")
    return {
        "session_id": session.get("session_id", path.stem),
        "status": session.get("status", "unknown"),
        "groups": summarize_records(observations),
    }


def main(args: list[str]) -> int:
    if len(args) != 1:
        print("Pass one path to a RADU session JSON file")
        return 2
    path = Path(args[0])
    if not path.is_file():
        print("Session file not found")
        return 2
    try:
        summary = analyze_session(path)
    except (OSError, json.JSONDecodeError, ValueError):
        print("Could not read the RADU session file")
        return 2

    print(f"Session {summary['session_id']} | status {summary['status']}")
    print("Group | samples | stage (measured samples) | median ms | P90 ms")
    for group_name, group in summary["groups"].items():
        for field, metrics in group["metrics"].items():
            print(
                f"{group_name} | {group['count']} | {STAGE_LABELS[field]} "
                f"({metrics['count']}) | {metrics['median_ms']} | {metrics['p90_ms']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
