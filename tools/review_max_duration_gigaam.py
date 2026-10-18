from __future__ import annotations

import argparse
import csv
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
OBSERVATIONS = PROJECT / "data" / "observations"
COMMANDS = PROJECT / "commands.json"


def normalize(text: str) -> str:
    value = (text or "").lower().replace("ё", "е")
    value = value.replace("youtube", "ютуб").replace("you tube", "ютуб")
    value = value.replace("telegram", "телеграм").replace("телеграмм", "телеграм")
    value = value.replace("mail.ru", "майл ру").replace("gmail", "джи мейл")
    return " ".join(re.sub(r"[^\w\s]+", " ", value, flags=re.UNICODE).split())


def find_command_hits(text: str, aliases: dict[str, list[str]]) -> list[str]:
    words = normalize(text).split()
    hits = []
    for command_id, phrases in aliases.items():
        for phrase in phrases:
            target = normalize(phrase).split()
            if target and any(
                words[index : index + len(target)] == target
                for index in range(len(words) - len(target) + 1)
            ):
                hits.append(command_id)
                break
    return hits


def collect() -> list[tuple[Path, dict, Path]]:
    rows = []
    for metadata_path in OBSERVATIONS.rglob("*.json"):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        capture = metadata.get("capture", {})
        reason = capture.get("vad_end_reason") or metadata.get("vad", {}).get(
            "end_reason"
        )
        if reason != "maximum_duration":
            continue
        audio_path = metadata_path.parent / metadata.get("audio", {}).get("path", "")
        if audio_path.is_file():
            rows.append((metadata_path, metadata, audio_path))
    rows.sort(key=lambda row: row[1].get("created_at_utc", ""))
    return rows


def evenly_spaced(rows: list, count: int) -> list:
    if count >= len(rows):
        return rows
    if count <= 0:
        return []
    if count == 1:
        return [rows[len(rows) // 2]]
    indexes = [round(i * (len(rows) - 1) / (count - 1)) for i in range(count)]
    return [rows[index] for index in indexes]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Review maximum-duration RADU observations with local GigaAM without copying WAV files"
    )
    parser.add_argument("--model", default="v2_rnnt")
    parser.add_argument("--sample-size", type=int, default=60)
    parser.add_argument("--all", action="store_true", help="Review every eligible WAV")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    candidates = collect()
    if not candidates:
        raise SystemExit("No maximum-duration WAV observations found")
    selected = candidates if args.all else evenly_spaced(candidates, args.sample_size)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = args.output or PROJECT / "data" / "review" / f"max_duration_gigaam_{stamp}"
    command_config = json.loads(COMMANDS.read_text(encoding="utf-8-sig"))
    aliases = {
        command["id"]: command.get("phrases", [])
        for command in command_config["commands"]
    }

    import gigaam

    print(
        f"Loading cached local GigaAM model {args.model} for {len(selected)} of {len(candidates)} WAV files",
        flush=True,
    )
    model = gigaam.load_model(args.model)
    output.mkdir(parents=True, exist_ok=True)
    result_path = output / "transcripts.csv"
    columns = [
        "observation_id",
        "created_at_utc",
        "source_wav",
        "duration_seconds",
        "vad_end_reason",
        "vosk_text",
        "gigaam_text",
        "configured_command_hits",
        "gigaam_seconds",
        "error",
    ]
    successful = empty = errors = command_hit_count = 0
    with result_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for index, (metadata_path, metadata, audio_path) in enumerate(
            selected, start=1
        ):
            started = time.perf_counter()
            try:
                transcript = str(model.transcribe(str(audio_path))).strip()
                error = ""
                if transcript:
                    successful += 1
                else:
                    empty += 1
            except Exception as exc:
                transcript = ""
                error = repr(exc)
                errors += 1
            elapsed = time.perf_counter() - started
            hits = find_command_hits(transcript, aliases)
            command_hit_count += bool(hits)
            writer.writerow(
                {
                    "observation_id": metadata.get(
                        "observation_id", metadata_path.stem
                    ),
                    "created_at_utc": metadata.get("created_at_utc", ""),
                    "source_wav": str(audio_path),
                    "duration_seconds": metadata.get("audio", {}).get(
                        "duration_seconds"
                    ),
                    "vad_end_reason": metadata.get("capture", {}).get("vad_end_reason"),
                    "vosk_text": metadata.get("recognition", {}).get("raw_text", "")
                    or "",
                    "gigaam_text": transcript,
                    "configured_command_hits": ",".join(hits),
                    "gigaam_seconds": round(elapsed, 4),
                    "error": error,
                }
            )
            stream.flush()
            if index == 1 or index % 10 == 0 or index == len(selected):
                print(f"{index}/{len(selected)} reviewed", flush=True)

    readme = output / "README.md"
    readme.write_text(
        "# GigaAM review of maximum-duration RADU observations\n\n"
        f"This deterministic sample selected {len(selected)} WAVs from {len(candidates)} observations stopped at the maximum-duration limit. Model `{args.model}`. Selection is evenly spaced over the recordings sorted by capture time.\n\n"
        f"GigaAM returned non-empty text for {successful}, empty text for {empty}, and errors for {errors}. Configured full-command phrases appeared in {command_hit_count} transcripts. The CSV references the original WAVs. No source audio or metadata was copied, moved, renamed, or modified. A transcript is evidence for manual label review, not an automatic ground-truth label.\n\n"
        f"Result file `{result_path.name}`.\n",
        encoding="utf-8",
    )
    print(f"Saved {len(selected)} transcript rows to {result_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
