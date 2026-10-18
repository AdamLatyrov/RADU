from __future__ import annotations

import argparse
import csv
import difflib
import json
import re
import shutil
import time
from datetime import datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = PROJECT / "data" / "confidence_rejections" / "2026" / "10" / "14"
DEFAULT_OUTPUT_ROOT = PROJECT / "data" / "review"
COMMANDS_PATH = PROJECT / "commands.json"


def normalize(text: str) -> str:
    value = (text or "").lower().replace("ё", "е")
    value = value.replace("youtube", "ютуб").replace("you tube", "ютуб")
    for joined, spaced in (
        ("майлру", "майл ру"),
        ("мейлру", "мейл ру"),
        ("мэйлру", "мэйл ру"),
        ("мэвру", "мэв ру"),
    ):
        value = value.replace(joined, spaced)
    value = value.replace("откройте", "открой").replace("открыть", "открой")
    value = value.replace("телеграмм", "телеграм").replace("джеймини", "джемини")
    value = re.sub(r"[^\w\s]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def read_candidate(source: Path, phrases: dict[str, list[str]]) -> dict:
    sidecar = json.loads(source.with_suffix(".json").read_text(encoding="utf-8-sig"))
    recognition = sidecar.get("recognition", {})
    command_id = sidecar.get("decision", {}).get("command_id", "")
    if command_id not in phrases:
        raise ValueError(
            f"Unknown command id in metadata for {source.name}: {command_id}"
        )
    return {
        "source": source,
        "sidecar": sidecar,
        "observation_id": sidecar.get("observation_id", source.stem),
        "command_id": command_id,
        "vosk_text": recognition.get("normalized_text", ""),
        "confidence": recognition.get("word_confidence_min"),
        "target_phrases": phrases[command_id],
    }


def classify(gigaam_text: str, target_phrases: list[str]) -> tuple[str, str]:
    transcript = normalize(gigaam_text)
    if not transcript:
        return "unresolved", "GigaAM returned empty text"
    transcript_tokens = transcript.split()
    for phrase in target_phrases:
        target_tokens = normalize(phrase).split()
        for start in range(max(0, len(transcript_tokens) - len(target_tokens) + 1)):
            if transcript_tokens[start : start + len(target_tokens)] == target_tokens:
                if start > 0 and transcript_tokens[start - 1] == "не":
                    return (
                        "negative",
                        "GigaAM transcript contains the configured phrase preceded by explicit negation",
                    )
                return (
                    "positive",
                    "GigaAM transcript contains the configured command phrase",
                )
    for phrase in target_phrases:
        target_tokens = normalize(phrase).split()
        for start in range(len(transcript_tokens)):
            max_size = min(len(target_tokens), len(transcript_tokens) - start)
            for size in range(1, max_size + 1):
                heard = transcript_tokens[start : start + size]
                if heard[:-1] != target_tokens[: size - 1]:
                    continue
                target_last = target_tokens[size - 1]
                heard_last = heard[-1]
                close_last = (
                    target_last.startswith(heard_last)
                    or difflib.SequenceMatcher(None, target_last, heard_last).ratio()
                    >= 0.65
                )
                if close_last and len(heard_last) >= 2:
                    return (
                        "unresolved",
                        "GigaAM transcript may contain a truncated or confusable configured phrase",
                    )
    if transcript_tokens and any(token == "не" for token in transcript_tokens[:3]):
        return "negative", "GigaAM transcript contains an explicit negation"
    return (
        "negative",
        "GigaAM transcript does not contain the configured command phrase",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Transcribe selected RADU clips and sort safe copies by GigaAM label"
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--model", default="v2_rnnt")
    parser.add_argument(
        "--executed-only",
        action="store_true",
        help="Use JSON metadata to select recordings whose RADU action actually executed",
    )
    parser.add_argument(
        "--reuse-results",
        type=Path,
        help="Reuse prior GigaAM transcripts by observation ID while rebuilding labels and copies",
    )
    args = parser.parse_args()

    commands = json.loads(COMMANDS_PATH.read_text(encoding="utf-8-sig"))
    phrases = {command["id"]: command["phrases"] for command in commands["commands"]}
    if args.executed_only:
        candidates = []
        for metadata_path in sorted(args.source.glob("*.json")):
            try:
                sidecar = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                continue
            if sidecar.get("decision", {}).get("action_result") != "executed":
                continue
            source_path = args.source / sidecar.get("audio", {}).get(
                "path", f"{metadata_path.stem}.wav"
            )
            if source_path.is_file():
                candidates.append(read_candidate(source_path, phrases))
    else:
        candidates = [
            read_candidate(path, phrases) for path in sorted(args.source.glob("*.wav"))
        ]
    if not candidates:
        raise SystemExit(f"No WAV candidates found in {args.source}")

    cached = {}
    if args.reuse_results:
        with args.reuse_results.open("r", encoding="utf-8-sig", newline="") as stream:
            cached = {row["observation_id"]: row for row in csv.DictReader(stream)}
    pending = [
        candidate
        for candidate in candidates
        if candidate["observation_id"] not in cached
    ]
    model = None
    if pending:
        import gigaam

        print(
            f"Loading local GigaAM model {args.model} for {len(pending)} recordings",
            flush=True,
        )
        model = gigaam.load_model(args.model)
    run_time = datetime.now().astimezone()
    run_id = run_time.strftime("%Y%m%d_%H%M%S")
    prefix = (
        "executed_actions_gigaam"
        if args.executed_only
        else "confidence_rejections_gigaam"
    )
    output = args.output_root / f"{prefix}_{run_id}"
    for label in ("positive", "negative", "unresolved"):
        (output / label).mkdir(parents=True, exist_ok=True)

    rows = []
    for index, candidate in enumerate(candidates, start=1):
        started = time.perf_counter()
        prior = cached.get(candidate["observation_id"])
        try:
            if prior is not None:
                transcript = prior.get("gigaam_text", "")
                status = prior.get("gigaam_status", "ok")
                error = prior.get("gigaam_error", "")
                elapsed = float(prior.get("inference_seconds") or 0)
            else:
                transcript = str(model.transcribe(str(candidate["source"]))).strip()
                status = "ok"
                error = ""
                elapsed = time.perf_counter() - started
            label, reason = classify(transcript, candidate["target_phrases"])
        except Exception as exc:
            transcript = ""
            label = "unresolved"
            reason = "GigaAM inference failed"
            status = "error"
            error = repr(exc)
            elapsed = time.perf_counter() - started

        metadata = {
            "observation_id": candidate["observation_id"],
            "command_id": candidate["command_id"],
            "gigaam_model": args.model,
            "gigaam_text": transcript,
            "vosk_text": candidate["vosk_text"],
            "word_confidence_min": candidate["confidence"],
            "label": label,
            "label_reason": reason,
            "gigaam_status": status,
            "gigaam_error": error,
            "inference_seconds": round(elapsed, 4),
            "source_wav": str(candidate["source"]),
            "source_metadata": str(candidate["source"].with_suffix(".json")),
        }
        target = (
            output
            / label
            / f"{candidate['observation_id']} - GigaAM {normalize(transcript) or 'пусто'}.wav"
        )
        shutil.copy2(candidate["source"], target)
        shutil.copy2(
            candidate["source"].with_suffix(".json"), target.with_suffix(".json")
        )
        metadata["sorted_copy"] = str(target)
        target.with_suffix(".analysis.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        rows.append(metadata)
        print(
            f"{index}/{len(candidates)} {label} | Vosk {candidate['vosk_text']} | GigaAM {transcript or '[empty]'}",
            flush=True,
        )

    columns = [
        "observation_id",
        "command_id",
        "label",
        "label_reason",
        "gigaam_model",
        "gigaam_text",
        "vosk_text",
        "word_confidence_min",
        "gigaam_status",
        "inference_seconds",
        "source_wav",
        "source_metadata",
        "sorted_copy",
        "gigaam_error",
    ]
    with (output / "results.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        label: sum(row["label"] == label for row in rows)
        for label in ("positive", "negative", "unresolved")
    }
    selection_description = (
        "Input clips were selected from observation metadata because RADU action execution was recorded as successful."
        if args.executed_only
        else "Input clips were recognized as exact configured phrases and passed every RADU check except its word-confidence threshold."
    )
    readme = (
        (
            "# GigaAM review of RADU recordings with executed actions\n\n"
            if args.executed_only
            else "# GigaAM review of confidence-rejected RADU clips\n\n"
        )
        + f"Run {run_time.isoformat()} with model `{args.model}`. {selection_description}\n\n"
        "Transcripts that express the configured command, including known pronunciation and verb-form variants, are placed in `positive`. Clearly different phrases or explicit negation are in `negative`. Empty, truncated or confusable transcripts are kept in `unresolved` so uncertain audio is not silently called a false activation. Files here are copies; source observations are unchanged. `results.csv` contains each transcript, label, reason, confidence and source path.\n\n"
        f"Counts: positive {summary['positive']}, negative {summary['negative']}, unresolved {summary['unresolved']}.\n"
    )
    (output / "README.md").write_text(readme, encoding="utf-8")
    print(f"Saved review copies and results to {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
