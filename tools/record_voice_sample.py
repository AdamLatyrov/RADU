from __future__ import annotations

import re
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np
import sounddevice as sd
import soundfile as sf

SAMPLE_RATE = 16_000


def suggest_output_path(
    phrase: str,
    base_dir: Path | None = None,
    timestamp: datetime | None = None,
) -> Path:
    safe_phrase = re.sub(r"[^\w-]+", "_", phrase.strip(), flags=re.UNICODE).strip("_")
    label = safe_phrase[:48] or "clip"
    stamp = (timestamp or datetime.now()).strftime("%Y%m%d_%H%M%S")
    directory = base_dir or Path(tempfile.gettempdir())
    return directory / f"radu_{stamp}_{label}.wav"


def record_command_sample() -> Path:
    phrase = input("Фраза для записи, например открой блокнот: ").strip()
    output_path = suggest_output_path(phrase)
    blocks: list[np.ndarray] = []

    input("Нажми Enter и произнеси фразу. После неё нажми Enter ещё раз")
    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=1,
        dtype="int16",
        callback=lambda data, _frames, _time, _status: blocks.append(data.copy()),
    ):
        input("Идёт запись. Говори сейчас")

    if not blocks:
        raise RuntimeError("Микрофон не передал аудиоданные")

    audio = np.concatenate(blocks, axis=0)
    sf.write(output_path, audio, SAMPLE_RATE, subtype="PCM_16")
    return output_path


def main() -> int:
    try:
        path = record_command_sample()
    except (OSError, RuntimeError) as error:
        print(f"Запись не удалась: {error}")
        return 1

    print(f"Готово. WAV сохранён сюда: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
