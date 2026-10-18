from __future__ import annotations

import csv
import json
import re
import time
import wave
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from vosk import KaldiRecognizer, Model

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "data" / "audio_library"
MAP_FILE = LIBRARY / "command_dataset_map.csv"
EXECUTED_MANIFEST = LIBRARY / "executed_commands" / "dataset_manifest.csv"
COMMANDS_FILE = ROOT / "commands.json"
MODEL_DIR = ROOT / "model" / "vosk-model-small-ru-0.22"
OUTPUT = ROOT / "data" / "review" / "dataset_gate_ablation_no_unk_20261015"
SAMPLE_RATE = 16_000
FRAME_SAMPLES = 320


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower().replace("ё", "е")).strip()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def decode_file(recognizer: KaldiRecognizer, path: Path) -> dict[str, Any]:
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        width = wav.getsampwidth()
        rate = wav.getframerate()
        frames = wav.getnframes()
        raw = wav.readframes(frames)
    if width != 2:
        raise ValueError(f"Expected 16-bit PCM, got sample width {width}")
    if rate != SAMPLE_RATE:
        raise ValueError(f"Expected {SAMPLE_RATE} Hz, got {rate} Hz")
    pcm = np.frombuffer(raw, dtype="<i2")
    if channels > 1:
        pcm = pcm.reshape(-1, channels).astype(np.int32).mean(axis=1).astype(np.int16)

    recognizer.Reset()
    partials: list[dict[str, Any]] = []
    started = time.perf_counter()
    for start in range(0, len(pcm), FRAME_SAMPLES):
        block = pcm[start : start + FRAME_SAMPLES]
        if recognizer.AcceptWaveform(block.tobytes()):
            partials.append(json.loads(recognizer.Result()))
    partials.append(json.loads(recognizer.FinalResult()))
    elapsed_ms = (time.perf_counter() - started) * 1000
    text = " ".join(
        str(item.get("text", "")).strip() for item in partials if item.get("text")
    )
    words = [word for item in partials for word in (item.get("result") or [])]
    confidences = [
        float(word["conf"]) for word in words if word.get("conf") is not None
    ]
    return {
        "transcript": text,
        "word_confidence_min": min(confidences) if confidences else "",
        "word_confidence_count": len(confidences),
        "duration_s": frames / rate,
        "decode_ms": elapsed_ms,
        "contains_unk": "[unk]" in normalize(text),
        "starts_with_ne": normalize(text).startswith("не "),
    }


def summarize(rows: list[dict[str, Any]], result_field: str) -> dict[str, Any]:
    tp = fp = tn = fn = 0
    for row in rows:
        predicted = bool(row[result_field])
        positive = row["Label"] == "positive"
        if positive and predicted:
            tp += 1
        elif positive:
            fn += 1
        elif predicted:
            fp += 1
        else:
            tn += 1
    total = tp + fp + tn + fn
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        "n": total,
        "tp": tp,
        "fn": fn,
        "fp": fp,
        "tn": tn,
        "accuracy_pct": 100 * (tp + tn) / total if total else 0.0,
        "precision_pct": 100 * precision,
        "recall_pct": 100 * recall,
        "false_launches": fp,
    }


def main() -> None:
    library_records = read_csv(MAP_FILE)
    records: list[dict[str, str]] = []
    paths_by_pair: dict[tuple[str, str], Path] = {}
    for source in library_records:
        row = dict(source)
        row["SourceDataset"] = source.get("SourceDataset") or "command_library"
        row["UniqueAudioID"] = source["UniqueAudioID"]
        row["GigaAMText"] = source["GigaAMText"]
        row["SHA256"] = source["SHA256"]
        row["CommandID"] = source["CommandID"]
        row["Label"] = source["Label"]
        path = LIBRARY / source["Folder"] / source["File"]
        records.append(row)
        paths_by_pair[(source["SHA256"], source["CommandID"])] = path

    executed_records = read_csv(EXECUTED_MANIFEST)
    for source in executed_records:
        pair_key = (source["sha256"], source["command_id"])
        if pair_key in paths_by_pair:
            continue
        row = dict(source)
        row["SourceDataset"] = "executed_commands"
        row["UniqueAudioID"] = source["observation_id"]
        row["GigaAMText"] = source["gigaam_text"]
        row["SHA256"] = source["sha256"]
        row["CommandID"] = source["command_id"]
        row["Label"] = source["label"]
        row["File"] = Path(source["organized_wav"]).name
        row["Folder"] = f"audio/{source['command_id']}/{source['label']}"
        path = LIBRARY / row["Folder"] / row["File"]
        records.append(row)
        paths_by_pair[pair_key] = path
    config = json.loads(COMMANDS_FILE.read_text(encoding="utf-8"))
    commands = config["commands"]
    phrase_to_command = {
        normalize(phrase): command["id"]
        for command in commands
        for phrase in command["phrases"]
    }
    all_phrases = list(phrase_to_command)
    negated_phrases = [f"не {phrase}" for phrase in all_phrases]
    grammar = json.dumps([*all_phrases, *negated_phrases], ensure_ascii=False)

    by_hash: dict[str, Path] = {}
    for row in records:
        path = paths_by_pair[(row["SHA256"], row["CommandID"])]
        if not path.is_file():
            raise FileNotFoundError(path)
        by_hash.setdefault(row["SHA256"], path)

    model = Model(str(MODEL_DIR))
    recognizer = KaldiRecognizer(model, SAMPLE_RATE, grammar)
    recognizer.SetWords(True)
    decoded: dict[str, dict[str, Any]] = {}
    for index, (digest, path) in enumerate(by_hash.items(), start=1):
        result = decode_file(recognizer, path)
        result.update({"sha256": digest, "source_file": path.name})
        decoded[digest] = result
        print(f"Decoded {index}/{len(by_hash)}", flush=True)

    variants = [
        ("raw_exact", None, False, False, False),
        ("confidence_0_5", 0.5, True, True, True),
        ("confidence_1_0_no_unk", 1.0, False, True, True),
        ("confidence_0_5_no_unk_gate", 0.5, False, True, True),
        ("no_confidence_gate_at_0_5_baseline", None, True, False, True),
        ("confidence_0_5_no_negation_gate", 0.5, True, True, False),
        ("exact_no_unk_confidence_negation_gates", None, False, False, False),
    ]

    evaluated: list[dict[str, Any]] = []
    for source in records:
        item = dict(source)
        result = decoded[source["SHA256"]]
        normalized = normalize(result["transcript"])
        predicted = phrase_to_command.get(normalized)
        confidence = result["word_confidence_min"]
        item.update(result)
        item["predicted_command"] = predicted or ""
        for name, threshold, unk_gate, confidence_gate, negation_gate in variants:
            accepted = predicted == source["CommandID"]
            if accepted and unk_gate and result["contains_unk"]:
                accepted = False
            if accepted and negation_gate and result["starts_with_ne"]:
                accepted = False
            if accepted and confidence_gate:
                accepted = bool(
                    confidence != "" and float(confidence) >= float(threshold)
                )
            item[name] = accepted
        evaluated.append(item)

    OUTPUT.mkdir(parents=True, exist_ok=True)
    with (OUTPUT / "decodes_by_unique_audio.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as stream:
        fields = [
            "sha256",
            "source_file",
            "transcript",
            "word_confidence_min",
            "word_confidence_count",
            "duration_s",
            "decode_ms",
            "contains_unk",
            "starts_with_ne",
        ]
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(
            [
                {key: decoded[digest].get(key, "") for key in fields}
                for digest in by_hash
            ]
        )

    pair_fields = list(
        dict.fromkeys(
            [
                "SourceDataset",
                "UniqueAudioID",
                "File",
                "SHA256",
                "CommandID",
                "Label",
                "GigaAMText",
                "Folder",
                "transcript",
                "predicted_command",
                "word_confidence_min",
                "duration_s",
                "decode_ms",
                *[item[0] for item in variants],
            ]
        )
    )
    with (OUTPUT / "decisions_by_command_pair.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=pair_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(evaluated)

    names = [item[0] for item in variants]
    totals = {name: summarize(evaluated, name) for name in names}
    by_command: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for command in sorted({row["id"] for row in commands}):
        selected = [row for row in evaluated if row["CommandID"] == command]
        for name in names:
            by_command[command][name] = summarize(selected, name)
    by_dataset = {
        dataset: {
            name: summarize(
                [row for row in evaluated if row["SourceDataset"] == dataset], name
            )
            for name in names
        }
        for dataset in sorted({row["SourceDataset"] for row in evaluated})
    }
    dataset_counts = {
        dataset: {
            "command_audio_pairs": sum(
                row["SourceDataset"] == dataset for row in evaluated
            ),
            "unique_hashes": len(
                {row["SHA256"] for row in evaluated if row["SourceDataset"] == dataset}
            ),
            "positive_pairs": sum(
                row["SourceDataset"] == dataset and row["Label"] == "positive"
                for row in evaluated
            ),
            "negative_pairs": sum(
                row["SourceDataset"] == dataset and row["Label"] == "negative"
                for row in evaluated
            ),
        }
        for dataset in sorted({row["SourceDataset"] for row in evaluated})
    }
    summary = {
        "records": len(records),
        "unique_audio": len(by_hash),
        "command_count": len({row["id"] for row in commands}),
        "tested_commands": sorted({row["CommandID"] for row in evaluated}),
        "dataset_counts": dataset_counts,
        "decodes": decoded,
        "totals": totals,
        "by_command": by_command,
        "by_dataset": by_dataset,
    }
    (OUTPUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(OUTPUT),
                "records": len(records),
                "unique_audio": len(by_hash),
                "commands": sorted({row["CommandID"] for row in records}),
                "totals": totals,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
