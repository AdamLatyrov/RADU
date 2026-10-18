from __future__ import annotations

import argparse
import csv
import json
import time
import wave
from collections import Counter
from pathlib import Path

from analyze_recent_session_with_gigaam import PROJECT, normalize

DEFAULT_INPUT = (
    PROJECT
    / "data"
    / "review"
    / "live_session_gigaam_20261015"
    / "gigaam_transcripts.csv"
)
DEFAULT_MODEL = PROJECT / "model" / "vosk-model-small-ru-0.22"


def command_hits(text: str, aliases: dict[str, list[str]]) -> list[str]:
    tokens = normalize(text).split()
    hits = []
    for command_id, phrases in aliases.items():
        for phrase in phrases:
            target = normalize(phrase).split()
            for start in range(max(0, len(tokens) - len(target) + 1)):
                if target and tokens[start : start + len(target)] == target:
                    if start == 0 or tokens[start - 1] != "не":
                        hits.append(command_id)
                    break
            if command_id in hits:
                break
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare unconstrained Vosk with RADU grammar and local GigaAM on the same WAVs"
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT
        / "data"
        / "review"
        / "live_session_gigaam_20261015"
        / "vosk_unconstrained.csv",
    )
    args = parser.parse_args()

    commands = json.loads((PROJECT / "commands.json").read_text(encoding="utf-8-sig"))
    aliases = {
        command["id"]: [normalize(phrase) for phrase in command.get("phrases", [])]
        for command in commands["commands"]
    }
    with args.input.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise SystemExit(f"No observations found in {args.input}")

    import vosk

    vosk.SetLogLevel(-1)
    print(
        f"Loading local unconstrained Vosk model for {len(rows)} WAV files", flush=True
    )
    model = vosk.Model(str(args.model))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "observation_id",
        "rejection_reason",
        "radu_command_id",
        "radu_vosk_text",
        "gigaam_text",
        "gigaam_command_hits",
        "unconstrained_vosk_text",
        "unconstrained_command_hits",
        "matches_radu_command",
        "inference_seconds",
        "error",
    ]
    outcomes = Counter()
    with args.output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for index, row in enumerate(rows, start=1):
            wav_path = PROJECT / Path(row["source_wav"])
            started = time.perf_counter()
            try:
                with wave.open(str(wav_path), "rb") as audio:
                    sample_rate = audio.getframerate()
                    frames = audio.readframes(audio.getnframes())
                recognizer = vosk.KaldiRecognizer(model, sample_rate)
                recognizer.SetWords(False)
                recognizer.AcceptWaveform(frames)
                free_text = json.loads(recognizer.FinalResult()).get("text", "")
                error = ""
            except Exception as exc:
                free_text = ""
                error = repr(exc)
            elapsed = time.perf_counter() - started
            free_hits = command_hits(free_text, aliases)
            expected = row.get("radu_command_id", "")
            matches = bool(expected and expected in free_hits)
            reason = row.get("rejection_reason", "")
            if reason == "accepted":
                outcomes["baseline_executed"] += 1
                if expected in row.get("gigaam_command_hits", "").split(","):
                    outcomes["gigaam_supported_execution"] += 1
                    outcomes["unconstrained_keeps_gigaam_supported"] += int(matches)
                    outcomes["unconstrained_misses_gigaam_supported"] += int(
                        not matches
                    )
                else:
                    outcomes["gigaam_disagrees_with_execution"] += 1
                    outcomes["unconstrained_blocks_disagreement"] += int(not matches)
                    outcomes["unconstrained_keeps_disagreement"] += int(matches)
            writer.writerow(
                {
                    "observation_id": row["observation_id"],
                    "rejection_reason": reason,
                    "radu_command_id": expected,
                    "radu_vosk_text": row.get("vosk_text", ""),
                    "gigaam_text": row.get("gigaam_text", ""),
                    "gigaam_command_hits": row.get("gigaam_command_hits", ""),
                    "unconstrained_vosk_text": free_text,
                    "unconstrained_command_hits": ",".join(free_hits),
                    "matches_radu_command": matches,
                    "inference_seconds": round(elapsed, 4),
                    "error": error,
                }
            )
            stream.flush()
            if index == 1 or index % 50 == 0 or index == len(rows):
                print(f"{index}/{len(rows)} decoded", flush=True)

    readme = args.output.with_name("vosk_unconstrained_README.md")
    readme.write_text(
        "# Unconstrained Vosk comparison\n\n"
        f"Decoded the same {len(rows)} WAV files with the local Russian Vosk model without a phrase grammar. The existing constrained Vosk result and GigaAM transcript are retained beside the new transcript. No runtime settings or source audio were changed.\n\n"
        f"Executed-action comparison counts `{dict(outcomes)}`. Full per-record results are in `{args.output.name}`.\n",
        encoding="utf-8",
    )
    print(f"Saved {len(rows)} free-recognition rows to {args.output}", flush=True)
    print(json.dumps(dict(outcomes), ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
