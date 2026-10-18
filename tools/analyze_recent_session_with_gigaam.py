from __future__ import annotations

import argparse
import csv
import json
import re
import time
from collections import Counter
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
OBSERVATIONS = PROJECT / "data" / "observations"
COMMANDS = PROJECT / "commands.json"


def normalize(text: str) -> str:
    value = (text or "").lower().replace("ё", "е")
    value = value.replace("youtube", "ютуб").replace("you tube", "ютуб")
    value = value.replace("telegram", "телеграм").replace("телеграмм", "телеграм")
    value = value.replace("mail.ru", "майл ру").replace("gmail", "джи мейл")
    value = re.sub(r"[^\w\s]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def load_events(session_id: str) -> list[tuple[Path, dict]]:
    selected = []
    for metadata_path in OBSERVATIONS.rglob("*.json"):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if metadata.get("session_id") != session_id:
            continue
        reason = metadata.get("decision", {}).get("rejection_reason")
        accepted = metadata.get("decision", {}).get("accepted") is True
        if accepted or reason in {
            "transcript_not_an_exact_configured_phrase",
            "explicit_negation",
            "word_confidence_below_threshold_or_missing",
        }:
            audio_path = metadata_path.parent / metadata.get("audio", {}).get(
                "path", ""
            )
            if audio_path.is_file():
                selected.append((metadata_path, metadata))
    selected.sort(key=lambda item: item[1].get("created_at_utc", ""))
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Review recent Vosk command decisions with local GigaAM without copying WAV files"
    )
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--model", default="v2_rnnt")
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT / "data" / "review" / "live_session_gigaam_20261015",
    )
    args = parser.parse_args()

    commands = json.loads(COMMANDS.read_text(encoding="utf-8-sig"))
    aliases = {
        command["id"]: [normalize(phrase) for phrase in command.get("phrases", [])]
        for command in commands["commands"]
    }
    events = load_events(args.session_id)
    if not events:
        raise SystemExit(
            f"No eligible WAV observations found for session {args.session_id}"
        )

    import gigaam

    print(
        f"Loading cached local GigaAM model {args.model} for {len(events)} observations",
        flush=True,
    )
    model = gigaam.load_model(args.model)
    args.output.mkdir(parents=True, exist_ok=True)
    result_path = args.output / "gigaam_transcripts.csv"
    columns = [
        "observation_id",
        "created_at_utc",
        "rejection_reason",
        "radu_command_id",
        "vosk_text",
        "vosk_min_word_confidence",
        "gigaam_text",
        "gigaam_command_hits",
        "contains_explicit_negation",
        "duration_seconds",
        "gigaam_seconds",
        "source_wav",
        "error",
    ]
    counts: Counter[str] = Counter()
    with result_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for index, (metadata_path, metadata) in enumerate(events, start=1):
            wav_path = metadata_path.parent / metadata.get("audio", {}).get("path", "")
            started = time.perf_counter()
            try:
                transcript = str(model.transcribe(str(wav_path))).strip()
                error = ""
            except (
                Exception
            ) as exc:  # Keep the batch moving while retaining diagnostic detail.
                transcript = ""
                error = repr(exc)
            elapsed = time.perf_counter() - started
            normalized = normalize(transcript)
            tokens = normalized.split()
            hits = []
            for command_id, phrases in aliases.items():
                if any(
                    any(
                        tokens[start : start + len(phrase.split())] == phrase.split()
                        for start in range(
                            max(1, len(tokens) - len(phrase.split()) + 1)
                        )
                    )
                    for phrase in phrases
                    if phrase
                ):
                    hits.append(command_id)
            negated = any(
                tokens[index - 1] == "не"
                for command_id in hits
                for phrase in aliases[command_id]
                if phrase
                for index in range(1, len(tokens))
                if tokens[index : index + len(phrase.split())] == phrase.split()
            )
            reason = metadata.get("decision", {}).get("rejection_reason") or "accepted"
            counts[reason] += 1
            counts["gigaam_command_hit:" + ",".join(hits or ["none"])] += 1
            writer.writerow(
                {
                    "observation_id": metadata.get(
                        "observation_id", metadata_path.stem
                    ),
                    "created_at_utc": metadata.get("created_at_utc", ""),
                    "rejection_reason": reason,
                    "radu_command_id": metadata.get("decision", {}).get(
                        "command_id", ""
                    ),
                    "vosk_text": metadata.get("recognition", {}).get("raw_text", "")
                    or "",
                    "vosk_min_word_confidence": metadata.get("recognition", {}).get(
                        "word_confidence_min"
                    ),
                    "gigaam_text": transcript,
                    "gigaam_command_hits": ",".join(hits),
                    "contains_explicit_negation": negated,
                    "duration_seconds": metadata.get("audio", {}).get(
                        "duration_seconds"
                    ),
                    "gigaam_seconds": round(elapsed, 4),
                    "source_wav": str(wav_path),
                    "error": error,
                }
            )
            stream.flush()
            if index == 1 or index % 25 == 0 or index == len(events):
                print(f"{index}/{len(events)} reviewed", flush=True)

    readme = args.output / "README.md"
    readme.write_text(
        "# GigaAM review of one RADU observation session\n\n"
        f"Session ID `{args.session_id}`. Model `{args.model}`. GigaAM read {len(events)} WAV files selected from executed actions, explicit-negation decisions, confidence rejections, and non-exact Vosk transcripts. Long segments rejected before Vosk and empty Vosk results are excluded.\n\n"
        "The tool does not copy, move, rename, or modify source WAV or JSON files. The CSV keeps every transcript and the matching configured command phrase, if any. A phrase hit is a search aid and still requires review before it is treated as ground truth.\n\n"
        f"Result file `{result_path.name}`. Selected decision counts `{dict(counts)}`.\n",
        encoding="utf-8",
    )
    print(f"Saved {len(events)} transcript rows to {result_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
