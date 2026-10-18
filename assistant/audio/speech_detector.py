from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

import numpy as np

from assistant.audio.ring_buffer import RingBuffer

if TYPE_CHECKING:
    from assistant.commands import CommandRegistry


@dataclass
class AudioSegment:
    pcm: np.ndarray
    started_at: float
    ended_at: float
    speech_frames: int
    total_frames: int
    end_reason: str
    asr_eligible: bool
    speech_ended_at: float | None = None
    asr_pcm: np.ndarray | None = None
    leading_trim_samples: int = 0
    trailing_trim_samples: int = 0
    vosk_result: dict[str, object] | None = None
    vosk_stream_seconds: float = 0.0
    early_match_text: str | None = None
    early_match_at: float | None = None
    decision_detected_at: float | None = None
    transcript_ready_at: float | None = None
    queued_at: float | None = None
    worker_started_at: float | None = None
    command_registry: CommandRegistry | None = None

    def latency_breakdown(self) -> dict[str, float | None]:
        def elapsed(start: float | None, end: float | None) -> float | None:
            if start is None or end is None:
                return None
            return end - start

        return {
            "last_voiced_frame_to_capture_end_seconds": elapsed(
                self.speech_ended_at, self.ended_at
            ),
            "capture_end_to_decision_seconds": elapsed(
                self.ended_at, self.decision_detected_at
            ),
            "last_voiced_frame_to_decision_seconds": elapsed(
                self.speech_ended_at, self.decision_detected_at
            ),
            "last_voiced_frame_to_transcript_seconds": elapsed(
                self.speech_ended_at, self.transcript_ready_at
            ),
            "last_voiced_frame_to_queue_seconds": elapsed(
                self.speech_ended_at, self.queued_at
            ),
            "last_voiced_frame_to_worker_start_seconds": elapsed(
                self.speech_ended_at, self.worker_started_at
            ),
            "decision_to_transcript_seconds": elapsed(
                self.decision_detected_at, self.transcript_ready_at
            ),
            "transcript_to_queue_seconds": elapsed(
                self.transcript_ready_at, self.queued_at
            ),
            "asr_queue_wait_seconds": elapsed(self.queued_at, self.worker_started_at),
            "capture_end_to_worker_start_seconds": elapsed(
                self.ended_at, self.worker_started_at
            ),
        }


class SpeechDetector:
    """VAD segmentation retained from the earliest hand-written pipeline."""

    def __init__(
        self,
        ring: RingBuffer,
        start_frames: int,
        end_silence_frames: int,
        pre_samples: int,
        max_frames: int,
    ) -> None:
        self.ring = ring
        self.start_frames = start_frames
        self.end_silence_frames = end_silence_frames
        self.pre_samples = pre_samples
        self.max_frames = max_frames
        self.reset()

    def reset(self) -> None:
        self.speech_run = 0
        self.silence_run = 0
        self.recording = False
        self.frames: list[np.ndarray] = []
        self.started_at = 0.0
        self.speech_frames = 0
        self.recording_frames = 0
        self.ignore_until_silence = False
        self.last_speech_at: float | None = None

    def update(
        self, frame: np.ndarray, is_speech: bool, now: float
    ) -> Optional[AudioSegment]:
        self.speech_run = self.speech_run + 1 if is_speech else 0
        self.silence_run = 0 if is_speech else self.silence_run + 1
        if is_speech:
            self.last_speech_at = now
        if self.ignore_until_silence:
            if self.silence_run >= self.end_silence_frames:
                self.ignore_until_silence = False
                self.speech_run = 0
                self.silence_run = 0
            return None
        if not self.recording and self.speech_run >= self.start_frames:
            self.recording = True
            self.started_at = now
            self.speech_frames = 0
            self.recording_frames = 0
            prefix = self.ring.last(self.pre_samples)
            self.frames = [prefix] if len(prefix) else []
        if not self.recording:
            return None
        self.frames.append(frame.copy())
        self.recording_frames += 1
        self.speech_frames += int(is_speech)
        if not is_speech and self.silence_run >= self.end_silence_frames:
            return self._finish(now, "silence")
        if self.recording_frames >= self.max_frames:
            self.ignore_until_silence = True
            return self._finish(now, "maximum_duration", asr_eligible=False)
        return None

    def _finish(
        self, now: float, reason: str, asr_eligible: bool = True
    ) -> AudioSegment:
        segment = AudioSegment(
            pcm=np.concatenate(self.frames).astype(np.int16, copy=False),
            started_at=self.started_at,
            ended_at=now,
            speech_frames=self.speech_frames,
            total_frames=self.recording_frames,
            end_reason=reason,
            asr_eligible=asr_eligible,
            speech_ended_at=self.last_speech_at,
        )
        self.frames = []
        self.recording = False
        self.speech_run = 0
        self.silence_run = 0
        self.recording_frames = 0
        self.speech_frames = 0
        self.last_speech_at = None
        return segment

    def finish_early(self, now: float, reason: str) -> AudioSegment:
        """Close a recognized segment now and ignore its speech tail until silence."""
        if not self.recording:
            raise RuntimeError("Cannot close an inactive speech segment")
        segment = self._finish(now, reason)
        self.ignore_until_silence = True
        return segment
