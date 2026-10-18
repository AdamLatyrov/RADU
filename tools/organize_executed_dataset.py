from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
DATA = PROJECT / "data"
TARGETS = {
    "youtube": ("youtube", "ютуб", "ютюб"),
    "notepad": ("блокнот",),
    "gemini": ("гемини", "джемини", "джеммини", "джеймини", "gemini"),
    "google_mail": ("гугл почту", "gmail", "google mail"),
    "codex": ("кодекс", "codex", "chatgpt"),
    "tasks": ("задачи",),
    "task_tracker": ("task tracker", "таск трекер"),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def is_positive(command_id: str, transcript: str) -> bool:
    normalized = " ".join(transcript.lower().replace("ё", "е").split())
    for alias in TARGETS.get(command_id, ()):
        phrase = f"открой {alias}"
        start = 0
        while True:
            position = normalized.find(phrase, start)
            if position < 0:
                break
            preceding_words = normalized[:position].split()
            if not preceding_words or preceding_words[-1] not in {"не", "нельзя"}:
                return True
            start = position + len(phrase)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Keep only RADU audio that executed an action and passed an acoustic feature check."
    )
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Move eligible WAVs and delete other generated copies.",
    )
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    results_path = run_dir / "full_results.csv"
    history_path = DATA / "command_history" / "executed_commands.jsonl"
    if not results_path.is_file() or not history_path.is_file():
        parser.error("The run results or RADU execution history were not found")

    with results_path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
        fields = list(rows[0]) if rows else []
    executions = [
        json.loads(line)
        for line in history_path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    exec_by_id = {str(item.get("observation_id", "")): item for item in executions}
    target_root = DATA / "audio_library" / "executed_commands"
    manifest_rows: list[dict[str, str]] = []
    eligible_source_copies: set[Path] = set()
    counts: Counter[tuple[str, str]] = Counter()
    rows_by_id: dict[str, dict[str, str]] = {}
    for row in rows:
        if row.get("source_group") == "observations":
            rows_by_id[Path(row["source_path"]).stem] = row

    for observation_id, execution in exec_by_id.items():
        row = rows_by_id.get(observation_id)
        if (
            row is None
            or row.get("gigaam_status") != "ok"
            or not row.get("gigaam_text", "").strip()
        ):
            continue
        source_wav = DATA / execution["observation_wav"]
        sidecar = DATA / execution["observation_json"]
        if not source_wav.is_file() or not sidecar.is_file():
            continue
        observation = json.loads(sidecar.read_text(encoding="utf-8-sig"))
        decision = observation.get("decision") or {}
        gate = decision.get("acoustic_gate") or {}
        feature_results = gate.get("features") or {}
        passed_features = [
            name
            for name, result in feature_results.items()
            if result.get("passed") is True
        ]
        if decision.get("action_result") != "executed" or not passed_features:
            continue
        command_id = str(execution.get("command_id", ""))
        if command_id not in TARGETS:
            continue
        gigaam_text = row["gigaam_text"].strip()
        label = "positive" if is_positive(command_id, gigaam_text) else "negative"
        original_copy = Path(row["renamed_copy"])
        if not original_copy.is_file():
            continue
        destination = target_root / "audio" / command_id / label / original_copy.name
        eligible_source_copies.add(original_copy.resolve())
        counts[(command_id, label)] += 1
        manifest_rows.append(
            {
                "observation_id": observation_id,
                "command_id": command_id,
                "label": label,
                "gigaam_text": gigaam_text,
                "vosk_text": row.get("vosk_text", ""),
                "executed_transcript": str(execution.get("transcript", "")),
                "passed_features": ", ".join(passed_features),
                "executed_at_utc": str(execution.get("executed_at_utc", "")),
                "gigaam_analysis_time_moscow": row.get(
                    "gigaam_analysis_time_moscow", ""
                ),
                "sha256": row.get("sha256") or sha256(source_wav),
                "original_source_wav": str(source_wav),
                "organized_wav": str(destination),
                "radu_action": json.dumps(
                    decision.get("action", {}), ensure_ascii=False
                ),
            }
        )

    print(f"Qualifying actions {len(manifest_rows)}")
    print(f"Unique qualifying audio {len({row['sha256'] for row in manifest_rows})}")
    for key, count in sorted(counts.items()):
        print(f"{key[0]} {key[1]} {count}")
    all_copies = [
        Path(row["renamed_copy"]).resolve() for row in rows if row.get("renamed_copy")
    ]
    to_remove = [
        path
        for path in all_copies
        if path.is_file() and path not in eligible_source_copies
    ]
    print(f"Generated analysis copies to remove {len(to_remove)}")
    print("Original observation and archive WAVs will remain untouched")
    if not args.apply:
        print("Preview only. Rerun with --apply to organize the dataset")
        return 0

    for record in manifest_rows:
        source_copy = Path(
            next(
                row["renamed_copy"]
                for row in rows
                if Path(row["source_path"]).stem == record["observation_id"]
            )
        )
        destination = Path(record["organized_wav"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source_copy.resolve() == destination.resolve() and destination.is_file():
            continue
        if destination.exists():
            raise FileExistsError(destination)
        shutil.move(str(source_copy), str(destination))

    for path in to_remove:
        path.unlink()
    for directory in sorted((run_dir / "audio").rglob("*"), reverse=True):
        if directory.is_dir():
            try:
                directory.rmdir()
            except OSError:
                pass
    (run_dir / "audio").mkdir(exist_ok=True)

    for row in rows:
        copy = Path(row.get("renamed_copy", "")) if row.get("renamed_copy") else None
        if copy and copy.resolve() in eligible_source_copies:
            matching = next(
                (
                    item
                    for item in manifest_rows
                    if item["observation_id"] == Path(row["source_path"]).stem
                ),
                None,
            )
            row["retention_status"] = (
                "retained in executed command dataset"
                if matching
                else "excluded by retention rule"
            )
            if matching:
                row["renamed_copy"] = matching["organized_wav"]
                row["copy_status"] = "moved to command and label folder"
        else:
            row["retention_status"] = "excluded by retention rule"
            row["copy_status"] = "generated copy removed; original source preserved"
    for name in ("retention_status",):
        if name not in fields:
            fields.append(name)
    with results_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    manifest_path = target_root / "dataset_manifest.csv"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_fields = list(manifest_rows[0]) if manifest_rows else []
    with manifest_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=manifest_fields)
        writer.writeheader()
        writer.writerows(manifest_rows)

    with (target_root / "README.md").open("w", encoding="utf-8") as stream:
        positive_count = sum(
            count for (command, label), count in counts.items() if label == "positive"
        )
        negative_count = sum(
            count for (command, label), count in counts.items() if label == "negative"
        )
        stream.write(
            "# RADU executed command dataset\n\n"
            "Only WAV recordings with a confirmed executed action and at least one passing acoustic feature are retained here. "
            "Each command has `positive` and `negative` folders. Positive means GigaAM text contains the spoken target command. "
            "Negative means RADU executed the command, but GigaAM text does not contain it. GigaAM spelling variants listed in `TARGETS` "
            "are treated as equivalent. Current inventory contains "
            f"{len(manifest_rows)} unique WAVs, {positive_count} positive and {negative_count} negative.\n\n"
            "`dataset_manifest.csv` records the full GigaAM text, Vosk text, executed transcript, passed features, timestamps, source path and hash. "
            "A positive label requires the phrase `открой <target>`; direct negation such as `не открой` is negative. "
            "YouTube variants include YouTube, Ютуб and Ютюб; Gemini variants include гемини, джемини, джеммини, джеймини and Gemini. "
            "Original observation and archive WAV files are never deleted by the organizer. Generated analysis copies that do not qualify are removed.\n"
        )
    print(f"Organized dataset {target_root}")
    print(f"Manifest {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
