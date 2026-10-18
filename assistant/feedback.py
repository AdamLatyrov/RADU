from __future__ import annotations

import math
import os
import random
import struct
import wave
from pathlib import Path


def create_command_tone() -> Path:
    """Create a soft triple toggle-click sound in RADU's local cache."""
    data_root = Path(os.environ.get("RADU_DATA_ROOT", Path(__file__).resolve().parents[1] / "data"))
    path = data_root / "cache" / "feedback" / "command-toggle-click.wav"
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 16_000
    click_length = int(sample_rate * 0.012)
    click_offsets = (0, int(sample_rate * 0.038), int(sample_rate * 0.076))
    sample_count = click_offsets[-1] + click_length
    samples = [0] * sample_count
    noise = random.Random(431)
    for click_offset in click_offsets:
        for index in range(click_length):
            elapsed = index / sample_rate
            attack = min(1.0, index / 12)
            decay = math.exp(-elapsed * 430)
            resonance = math.sin(2 * math.pi * 1850 * elapsed)
            transient = noise.uniform(-1.0, 1.0)
            value = (0.48 * transient + 0.52 * resonance) * attack * decay
            samples[click_offset + index] += int(32767 * 0.12 * value)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return path


def play_command_tone(path: Path) -> None:
    """Start the confirmation tone asynchronously on Windows."""
    import winsound

    winsound.PlaySound(
        str(path),
        winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
    )
