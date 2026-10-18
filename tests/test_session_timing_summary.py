from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.summarize_session_timing import summarize_records  # noqa: E402


def testSessionGroupsSummarizeTimingByOutcomeAndCommand() -> None:
    records = [
        {
            "accepted": True,
            "command_id": "youtube",
            "timing": {
                "last_voiced_frame_to_capture_end_seconds": 0.010,
                "asr_queue_wait_seconds": 0.020,
            },
        },
        {
            "accepted": True,
            "command_id": "youtube",
            "timing": {
                "last_voiced_frame_to_capture_end_seconds": 0.030,
                "asr_queue_wait_seconds": 0.040,
            },
        },
        {
            "accepted": False,
            "command_id": None,
            "timing": {"last_voiced_frame_to_capture_end_seconds": 0.050},
        },
    ]

    summary = summarize_records(records)

    assert summary["all"]["count"] == 3
    assert summary["accepted"]["count"] == 2
    assert summary["rejected"]["count"] == 1
    assert summary["command_youtube"]["count"] == 2
    assert summary["command_youtube_accepted"]["count"] == 2
    assert summary["command_youtube_rejected"]["count"] == 0
    assert summary["all"]["metrics"]["last_voiced_frame_to_capture_end_seconds"] == {
        "count": 3,
        "median_ms": 30.0,
        "p90_ms": 50.0,
    }
    assert summary["command_youtube"]["metrics"]["asr_queue_wait_seconds"] == {
        "count": 2,
        "median_ms": 30.0,
        "p90_ms": 40.0,
    }


def testSessionGroupsOmitMetricsMissingFromAllRecords() -> None:
    summary = summarize_records([{"accepted": False, "command_id": None, "timing": {}}])

    assert summary["all"]["count"] == 1
    assert summary["all"]["metrics"] == {}
    assert "command_none" not in summary
