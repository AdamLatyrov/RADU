from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import time
import warnings
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT = Path(__file__).resolve().parents[1]
DATA = PROJECT / "data"
MOSCOW = ZoneInfo("Europe/Moscow")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_part(text: str, limit: int = 28) -> str:
    text = text.strip().lower().replace("ё", "е")
    text = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", " ", text)
    text = re.sub(r"[^\w -]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", "-", text).strip("- _.")
    return text[:limit].rstrip("- _.") or "пусто"


def read_manifest_texts() -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    paths = [
        DATA / "audio_library" / "audio_manifest.csv",
        DATA / "audio_library_previous_20261011" / "previous_audio_manifest.csv",
    ]
    for path in paths:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                key = (row.get("SHA256") or "").lower()
                if not key:
                    continue
                current = result.setdefault(key, {})
                current["vosk"] = (
                    current.get("vosk") or row.get("RADUGrammarText", "") or ""
                )
                current["gigaam"] = (
                    current.get("gigaam") or row.get("GigaAMText", "") or ""
                )
    return result


def observation_text(path: Path) -> str:
    stem = path.stem[:-4] if path.stem.endswith("_asr") else path.stem
    sidecar = path.with_name(stem + ".json")
    if not sidecar.exists():
        return ""
    try:
        payload = json.loads(sidecar.read_text(encoding="utf-8-sig"))
        recognition = payload.get("recognition", {})
        return str(
            recognition.get("normalized_text") or recognition.get("raw_text") or ""
        )
    except (OSError, json.JSONDecodeError):
        return ""


def inventory() -> list[dict[str, str]]:
    roots = [
        ("archive_snapshot", DATA / "audio_library_previous_20261011"),
        ("observations", DATA / "observations"),
    ]
    records: list[dict[str, str]] = []
    for source_name, root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.wav")):
            try:
                digest = sha256(path)
            except OSError as exc:
                records.append(
                    {
                        "source_group": source_name,
                        "source_path": str(path),
                        "sha256": "",
                        "vosk_text": "",
                        "inventory_error": str(exc),
                    }
                )
                continue
            vosk_text = observation_text(path) if source_name == "observations" else ""
            if not vosk_text:
                vosk_text = read_manifest_cache.get(digest.lower(), {}).get("vosk", "")
            records.append(
                {
                    "source_group": source_name,
                    "source_path": str(path),
                    "sha256": digest,
                    "vosk_text": vosk_text,
                    "inventory_error": "",
                }
            )
    return records


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default="v2_rnnt")
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    global read_manifest_cache
    args = parse_args()
    run_started = datetime.now(MOSCOW)
    run_id = run_started.strftime("%Y%m%d_%H%M%S")
    output = args.output or DATA / "gigaam_analysis" / f"run_{run_id}"
    output.mkdir(parents=True, exist_ok=True)
    progress_path = output / "unique_transcripts.jsonl"
    inventory_path = output / "inventory.csv"
    read_manifest_cache = read_manifest_texts()

    records = inventory()
    if not records:
        print("No WAV files found", flush=True)
        return 1
    with inventory_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "source_group",
                "source_path",
                "sha256",
                "vosk_text",
                "inventory_error",
            ],
        )
        writer.writeheader()
        writer.writerows(records)

    unique_sources: dict[str, Path] = {}
    for record in records:
        if record["sha256"]:
            unique_sources.setdefault(record["sha256"], Path(record["source_path"]))
    unique_results: dict[str, dict[str, str]] = {}
    if progress_path.exists():
        with progress_path.open("r", encoding="utf-8-sig") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                    if row.get("status") == "ok":
                        unique_results[row["sha256"]] = row
                except (json.JSONDecodeError, KeyError):
                    continue

    pending = [
        (digest, path)
        for digest, path in unique_sources.items()
        if digest not in unique_results
    ]
    print(
        f"WAV files {len(records)}; unique audio {len(unique_sources)}; cached in this run {len(unique_results)}; to transcribe {len(pending)}",
        flush=True,
    )
    if pending:
        try:
            import gigaam

            model = gigaam.load_model(args.model)
        except Exception as exc:
            print(f"Could not load GigaAM model {args.model}: {exc!r}", flush=True)
            return 2
        with progress_path.open("a", encoding="utf-8") as stream:
            for index, (digest, path) in enumerate(pending, start=1):
                started = time.perf_counter()
                timestamp = datetime.now(MOSCOW).strftime("%Y%m%d_%H%M%S")
                try:
                    text = model.transcribe(str(path))
                    status = "ok"
                    error = ""
                except Exception as exc:
                    text = ""
                    status = "error"
                    error = repr(exc)
                row = {
                    "sha256": digest,
                    "sample_source": str(path),
                    "gigaam_model": args.model,
                    "gigaam_text": str(text).strip(),
                    "status": status,
                    "error": error,
                    "analysis_time_moscow": timestamp,
                    "inference_seconds": f"{time.perf_counter() - started:.4f}",
                }
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                stream.flush()
                unique_results[digest] = row
                if index == 1 or index % 25 == 0 or index == len(pending):
                    print(
                        f"GigaAM {index}/{len(pending)}; elapsed {time.perf_counter() - started:.2f}s; status {status}",
                        flush=True,
                    )

    labels_root = output / "audio"
    mapping_path = output / "full_results.csv"
    used_names: set[Path] = set()
    rows: list[dict[str, str]] = []
    counts: defaultdict[str, int] = defaultdict(int)
    for record in records:
        source = Path(record["source_path"])
        group = record["source_group"]
        counts[group] += 1
        result = unique_results.get(record["sha256"], {})
        gigaam_text = result.get("gigaam_text", "")
        gigaam_label = (
            "ошибка чтения" if result.get("status") == "error" else gigaam_text
        )
        vosk_text = record["vosk_text"]
        timestamp = result.get("analysis_time_moscow", run_id)
        date_time = timestamp.replace("_", "-")
        identifier = f"{group[:3].upper()}-{counts[group]:06d}"
        name = (
            f"RADU-V3 - GigaAM {safe_part(gigaam_label)} - "
            f"Vosk {safe_part(vosk_text)} - {date_time} - {identifier}.wav"
        )
        destination = labels_root / group / name
        serial = 2
        while destination in used_names or destination.exists():
            destination = labels_root / group / f"{destination.stem}-{serial}.wav"
            serial += 1
        used_names.add(destination)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copy_status = "copied"
        except OSError as exc:
            copy_status = f"copy_error {exc!r}"
        rows.append(
            {
                **record,
                "gigaam_model": result.get("gigaam_model", args.model),
                "gigaam_text": gigaam_text,
                "gigaam_status": result.get("status", "missing"),
                "gigaam_analysis_time_moscow": timestamp,
                "vosk_text": vosk_text,
                "renamed_copy": str(destination),
                "copy_status": copy_status,
                "inference_seconds": result.get("inference_seconds", ""),
            }
        )

    columns = [
        "source_group",
        "source_path",
        "sha256",
        "gigaam_model",
        "gigaam_text",
        "gigaam_status",
        "gigaam_analysis_time_moscow",
        "vosk_text",
        "renamed_copy",
        "copy_status",
        "inference_seconds",
        "inventory_error",
    ]
    with mapping_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    with (output / "README.md").open("w", encoding="utf-8") as stream:
        stream.write(
            "# RADU V3 GigaAM аудиоанализ\n\n"
            f"Запуск начат {run_started.strftime('%Y-%m-%d %H-%M-%S')} по Москве. Использована модель `{args.model}`.\n\n"
            "Исходные WAV не переименовывались и не изменялись. Размеченные копии находятся в `audio`.\n"
            "Точные повторы транскрибировались один раз по SHA-256. `full_results.csv` связывает каждый исходный файл с транскрипциями и именем копии.\n"
            "`unique_transcripts.jsonl` сохраняет результаты по уникальным аудио и позволяет продолжить прерванную обработку.\n\n"
            f"Наблюдения RADU {counts['observations']} WAV. Архивный снимок {counts['archive_snapshot']} WAV.\n"
        )
    print(
        f"Finished. Results {mapping_path}; copies {len(rows)}; groups {dict(counts)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    warnings.filterwarnings(
        "ignore", message="You are using `torch.load` with `weights_only=False`.*"
    )
    raise SystemExit(main())
