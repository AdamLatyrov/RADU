from __future__ import annotations

import argparse
import json
import os
import wave
from collections import Counter, defaultdict
from pathlib import Path

import webrtcvad

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = PROJECT / "data" / "observations"
SAMPLE_RATE = 16_000
FRAME_MS = 20
FRAME_BYTES = SAMPLE_RATE * FRAME_MS // 1000 * 2
TAIL_FRAMES = 5


def iter_metadata(root: Path):
    for base, _dirs, names in os.walk(root):
        for name in names:
            if name.endswith(".json"):
                yield Path(base) / name


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit whether archived maximum-duration RADU clips end during voiced audio."
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--vad-mode", type=int, default=2)
    args = parser.parse_args()
    totals = Counter()
    by_date: dict[str, Counter] = defaultdict(Counter)
    duration_sum = 0.0
    max_seconds = Counter()

    for metadata_path in iter_metadata(args.root):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            totals["metadata_read_errors"] += 1
            continue
        capture = metadata.get("capture") or {}
        if capture.get("vad_end_reason") != "maximum_duration":
            continue

        totals["clips"] += 1
        day = str(metadata.get("created_at_utc", "unknown"))[:10]
        bucket = by_date[day]
        bucket["clips"] += 1
        duration = float(capture.get("duration_seconds") or 0.0)
        duration_sum += duration
        configured_limit = (metadata.get("vad") or {}).get(
            "max_utterance_seconds_including_end_pause"
        )
        if configured_limit is not None:
            max_seconds[str(configured_limit)] += 1
        if capture.get("submitted_to_asr") is False:
            totals["not_submitted_to_asr"] += 1
            bucket["not_submitted_to_asr"] += 1

        wav_path = metadata_path.parent / str(
            (metadata.get("audio") or {}).get("path", "")
        )
        if not wav_path.is_file():
            totals["missing_wav"] += 1
            bucket["missing_wav"] += 1
            continue
        try:
            with wave.open(str(wav_path), "rb") as audio:
                if (
                    audio.getframerate() != SAMPLE_RATE
                    or audio.getnchannels() != 1
                    or audio.getsampwidth() != 2
                ):
                    totals["format_mismatch"] += 1
                    bucket["format_mismatch"] += 1
                    continue
                start = max(
                    0, audio.getnframes() - FRAME_MS * TAIL_FRAMES * SAMPLE_RATE // 1000
                )
                audio.setpos(start)
                raw = audio.readframes(audio.getnframes() - start)
            frames = [
                raw[i : i + FRAME_BYTES]
                for i in range(0, len(raw) - FRAME_BYTES + 1, FRAME_BYTES)
            ]
            vad = webrtcvad.Vad(args.vad_mode)
            speech = [vad.is_speech(frame, SAMPLE_RATE) for frame in frames]
        except (OSError, wave.Error):
            totals["wav_read_errors"] += 1
            bucket["wav_read_errors"] += 1
            continue

        if speech and speech[-1]:
            totals["last_frame_voiced"] += 1
            bucket["last_frame_voiced"] += 1
        if any(speech):
            totals["voice_in_last_100ms"] += 1
            bucket["voice_in_last_100ms"] += 1
        if sum(speech) >= 3:
            totals["at_least_60ms_voiced_in_last_100ms"] += 1
            bucket["at_least_60ms_voiced_in_last_100ms"] += 1

    count = totals["clips"]
    if not count:
        print("No maximum-duration metadata found")
        return 1
    print(f"Clips {count}")
    print(f"Mean captured duration {duration_sum / count:.3f} sec")
    print(f"Configured duration limits {dict(sorted(max_seconds.items()))}")
    for key in (
        "not_submitted_to_asr",
        "last_frame_voiced",
        "voice_in_last_100ms",
        "at_least_60ms_voiced_in_last_100ms",
    ):
        print(
            f"{key} {totals[key]} of {count} ({100 * totals[key] / count:.1f} percent)"
        )
    print(
        f"Missing WAV {totals['missing_wav']}; format mismatches {totals['format_mismatch']}; read errors {totals['wav_read_errors'] + totals['metadata_read_errors']}"
    )
    print("By date")
    for day, bucket in sorted(by_date.items()):
        n = bucket["clips"]
        print(
            f"  {day} n={n} end_frame_voiced={bucket['last_frame_voiced']} "
            f"voice_last_100ms={bucket['voice_in_last_100ms']} not_sent_to_asr={bucket['not_submitted_to_asr']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
