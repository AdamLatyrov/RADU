from __future__ import annotations

import numpy as np
import webrtcvad


def trim_silence_vad(
    pcm: np.ndarray, sample_rate: int = 16_000, frame_ms: int = 20, mode: int = 2
) -> tuple[np.ndarray, int, int]:
    """Trim PCM to the first and last frame marked as speech by WebRTC VAD."""
    frame_samples = sample_rate * frame_ms // 1000
    frame_count = len(pcm) // frame_samples
    if frame_count == 0:
        return pcm.copy(), 0, 0
    frames = np.asarray(pcm[: frame_count * frame_samples], dtype=np.int16).reshape(
        frame_count, frame_samples
    )
    vad = webrtcvad.Vad(mode)
    active = [
        index
        for index, frame in enumerate(frames)
        if vad.is_speech(frame.tobytes(), sample_rate)
    ]
    if not active:
        return np.empty(0, dtype=np.int16), len(pcm), 0
    start = active[0] * frame_samples
    end = min(len(pcm), (active[-1] + 1) * frame_samples)
    return np.asarray(pcm[start:end], dtype=np.int16).copy(), start, len(pcm) - end
