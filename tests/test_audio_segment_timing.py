from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from assistant.audio.ring_buffer import RingBuffer  # noqa: E402
from assistant.audio.speech_detector import AudioSegment, SpeechDetector  # noqa: E402


def make_segment(**markers: float | None) -> AudioSegment:
    return AudioSegment(
        pcm=np.zeros(320, dtype=np.int16),
        started_at=0.0,
        ended_at=1.0,
        speech_frames=10,
        total_frames=12,
        end_reason="silence",
        asr_eligible=True,
        speech_ended_at=0.990,
        **markers,
    )


def test_all_stage_markers_report_separate_latency_durations() -> None:
    segment = make_segment(
        decision_detected_at=1.030,
        transcript_ready_at=1.045,
        queued_at=1.050,
        worker_started_at=1.071,
    )

    timing = segment.latency_breakdown()

    assert timing == pytest.approx(
        {
            "last_voiced_frame_to_capture_end_seconds": 0.010,
            "capture_end_to_decision_seconds": 0.030,
            "last_voiced_frame_to_decision_seconds": 0.040,
            "last_voiced_frame_to_transcript_seconds": 0.055,
            "last_voiced_frame_to_queue_seconds": 0.060,
            "last_voiced_frame_to_worker_start_seconds": 0.081,
            "decision_to_transcript_seconds": 0.015,
            "transcript_to_queue_seconds": 0.005,
            "asr_queue_wait_seconds": 0.021,
            "capture_end_to_worker_start_seconds": 0.071,
        }
    )


def test_missing_stage_markers_are_reported_as_unavailable() -> None:
    segment = make_segment(decision_detected_at=1.030)

    timing = segment.latency_breakdown()

    assert timing["capture_end_to_decision_seconds"] == pytest.approx(0.030)
    assert timing["last_voiced_frame_to_decision_seconds"] == pytest.approx(0.040)
    assert timing["last_voiced_frame_to_transcript_seconds"] is None
    assert timing["decision_to_transcript_seconds"] is None
    assert timing["transcript_to_queue_seconds"] is None
    assert timing["asr_queue_wait_seconds"] is None
    assert timing["capture_end_to_worker_start_seconds"] is None


def test_vadSegmentKeepsLastVoicedFrameWhenSilenceClosesIt() -> None:
    detector = SpeechDetector(
        RingBuffer(640),
        start_frames=1,
        end_silence_frames=2,
        pre_samples=0,
        max_frames=10,
    )
    frame = np.zeros(320, dtype=np.int16)

    detector.update(frame, True, 1.000)
    detector.update(frame, True, 1.020)
    detector.update(frame, False, 1.040)
    segment = detector.update(frame, False, 1.060)

    assert segment is not None
    assert segment.speech_ended_at == pytest.approx(1.020)
    assert segment.ended_at == pytest.approx(1.060)
