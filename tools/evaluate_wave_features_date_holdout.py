from __future__ import annotations

import argparse
import csv
import math
import struct
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CALIBRATION = (
    ROOT
    / "data"
    / "review"
    / "kaldi_oct14_session_holdout_20261014"
    / "calibration_manifest.csv"
)
DEFAULT_HOLDOUT = (
    ROOT
    / "data"
    / "review"
    / "kaldi_oct14_session_holdout_20261014"
    / "session_manifest.csv"
)
DEFAULT_OUTPUT = ROOT / "data" / "review" / "wave_feature_date_holdout_20261015"
DATE_FOLDS = ("20261010", "20261012", "20261014")
FRAME_SAMPLES = 320
LOW_ENERGY_RMS = 200.0


def audio_features(path: Path) -> dict:
    with wave.open(str(path), "rb") as stream:
        channels = stream.getnchannels()
        rate = stream.getframerate()
        sample_width = stream.getsampwidth()
        sample_count = stream.getnframes()
        raw = stream.readframes(sample_count)
    if channels != 1 or rate != 16000 or sample_width != 2:
        raise ValueError(
            f"Expected 16 kHz mono PCM16 audio, got {rate} Hz, {channels} ch, {sample_width} byte samples in {path}"
        )

    sample_count = len(raw) // 2
    samples = struct.unpack(f"<{sample_count}h", raw)
    square_sum = math.fsum(sample * sample for sample in samples)
    rms = math.sqrt(square_sum / sample_count) if sample_count else 0.0
    peak = max((abs(sample) for sample in samples), default=0)

    frame_rms = []
    for start in range(0, sample_count, FRAME_SAMPLES):
        frame = samples[start : start + FRAME_SAMPLES]
        if frame:
            frame_rms.append(
                math.sqrt(math.fsum(sample * sample for sample in frame) / len(frame))
            )

    low = [value <= LOW_ENERGY_RMS for value in frame_rms]
    leading_low_frames = next(
        (index for index, is_low in enumerate(low) if not is_low), len(low)
    )
    trailing_low_frames = next(
        (index for index, is_low in enumerate(reversed(low)) if not is_low), len(low)
    )
    return {
        "wav_duration_seconds": sample_count / rate,
        "rms_pcm": rms,
        "peak_abs_pcm": peak,
        "low_energy_frame_ratio": sum(low) / len(low) if low else 1.0,
        "leading_low_energy_ms": leading_low_frames * 20,
        "trailing_low_energy_ms": trailing_low_frames * 20,
    }


def resolve_wav_path(value: str) -> Path:
    path = Path(value)
    if path.exists():
        return path
    workspace_relative = ROOT.parents[2] / path
    if workspace_relative.exists():
        return workspace_relative
    raise FileNotFoundError(value)


def load_events(calibration_manifest: Path, holdout_manifest: Path) -> list[dict]:
    score_root = ROOT / "data" / "review" / "kaldi_oct14_session_holdout_20261014"
    score_paths = {
        "20261010": score_root
        / "command_specific_date_transfer"
        / "holdout_20261010"
        / "event_scores.csv",
        "20261012": score_root
        / "command_specific_date_transfer"
        / "holdout_20261012"
        / "event_scores.csv",
        "20261014": score_root
        / "command_specific_decoys"
        / "holdout_20261014"
        / "event_scores.csv",
    }
    baseline_by_id = {}
    for score_path in score_paths.values():
        with score_path.open("r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                baseline_by_id[row["observation_id"]] = (
                    row["baseline_accept"].casefold() == "true"
                )

    events = []
    for manifest in (calibration_manifest, holdout_manifest):
        with manifest.open("r", encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        for row in rows:
            date = row["observation_id"][:8]
            if date not in DATE_FOLDS or row["label"] not in {"positive", "negative"}:
                continue
            wav_path = resolve_wav_path(row["organized_wav"])
            event = {
                "observation_id": row["observation_id"],
                "session_date": date,
                "command_id": row["command_id"],
                "label": row["label"],
                "wav_path": str(wav_path),
                "baseline_accept": baseline_by_id[row["observation_id"]],
            }
            event.update(audio_features(wav_path))
            events.append(event)

    ids = [event["observation_id"] for event in events]
    if len(ids) != len(set(ids)):
        raise ValueError("The manifests contain duplicate observation IDs")
    return events


def confusion(
    events: list[dict], feature: str, direction: str, boundary: float
) -> dict:
    result = {"tp": 0, "fn": 0, "fp": 0, "tn": 0}
    for event in events:
        accepted = (
            event[feature] < boundary
            if direction == "below"
            else event[feature] > boundary
        )
        key = (
            ("tp" if accepted else "fn")
            if event["label"] == "positive"
            else ("fp" if accepted else "tn")
        )
        result[key] += 1
    return result


def gated_confusion(
    events: list[dict], feature: str, direction: str, boundary: float
) -> dict:
    result = {"tp": 0, "fn": 0, "fp": 0, "tn": 0}
    for event in events:
        feature_pass = (
            event[feature] < boundary
            if direction == "below"
            else event[feature] > boundary
        )
        accepted = event["baseline_accept"] and feature_pass
        key = (
            ("tp" if accepted else "fn")
            if event["label"] == "positive"
            else ("fp" if accepted else "tn")
        )
        result[key] += 1
    return result


def evaluate(events: list[dict]) -> tuple[list[dict], list[dict]]:
    features = [
        "wav_duration_seconds",
        "rms_pcm",
        "peak_abs_pcm",
        "low_energy_frame_ratio",
        "leading_low_energy_ms",
        "trailing_low_energy_ms",
    ]
    fold_rows = []
    for heldout_date in DATE_FOLDS:
        training = [event for event in events if event["session_date"] != heldout_date]
        heldout = [event for event in events if event["session_date"] == heldout_date]
        training_negatives = [
            event for event in training if event["label"] == "negative"
        ]
        baseline = {
            "baseline_tp": sum(
                event["label"] == "positive" and event["baseline_accept"]
                for event in heldout
            ),
            "baseline_fn": sum(
                event["label"] == "positive" and not event["baseline_accept"]
                for event in heldout
            ),
            "baseline_fp": sum(
                event["label"] == "negative" and event["baseline_accept"]
                for event in heldout
            ),
            "baseline_tn": sum(
                event["label"] == "negative" and not event["baseline_accept"]
                for event in heldout
            ),
        }
        for feature in features:
            values = [event[feature] for event in training_negatives]
            for direction in ("below", "above"):
                boundary = min(values) if direction == "below" else max(values)
                feature_counts = confusion(heldout, feature, direction, boundary)
                gate_counts = gated_confusion(heldout, feature, direction, boundary)
                fold_rows.append(
                    {
                        "heldout_date": heldout_date,
                        "feature": feature,
                        "direction": direction,
                        "training_negative_count": len(training_negatives),
                        "boundary": boundary,
                        "heldout_positive_count": sum(
                            event["label"] == "positive" for event in heldout
                        ),
                        "heldout_negative_count": sum(
                            event["label"] == "negative" for event in heldout
                        ),
                        **baseline,
                        **{
                            f"feature_{key}": value
                            for key, value in feature_counts.items()
                        },
                        **{f"gate_{key}": value for key, value in gate_counts.items()},
                    }
                )

    summary_rows = []
    for feature in sorted({row["feature"] for row in fold_rows}):
        for direction in ("below", "above"):
            rows = [
                row
                for row in fold_rows
                if row["feature"] == feature and row["direction"] == direction
            ]
            summary_rows.append(
                {
                    "feature": feature,
                    "direction": direction,
                    "folds": len(rows),
                    "heldout_positive_count": sum(
                        row["heldout_positive_count"] for row in rows
                    ),
                    "heldout_negative_count": sum(
                        row["heldout_negative_count"] for row in rows
                    ),
                    "baseline_tp": sum(row["baseline_tp"] for row in rows),
                    "baseline_fn": sum(row["baseline_fn"] for row in rows),
                    "baseline_fp": sum(row["baseline_fp"] for row in rows),
                    "baseline_tn": sum(row["baseline_tn"] for row in rows),
                    "feature_tp": sum(row["feature_tp"] for row in rows),
                    "feature_fn": sum(row["feature_fn"] for row in rows),
                    "feature_fp": sum(row["feature_fp"] for row in rows),
                    "feature_tn": sum(row["feature_tn"] for row in rows),
                    "gate_tp": sum(row["gate_tp"] for row in rows),
                    "gate_fn": sum(row["gate_fn"] for row in rows),
                    "gate_fp": sum(row["gate_fp"] for row in rows),
                    "gate_tn": sum(row["gate_tn"] for row in rows),
                    "zero_fp_folds": sum(row["gate_fp"] == 0 for row in rows),
                }
            )
    return fold_rows, summary_rows


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Test simple waveform thresholds with whole-date holdouts"
    )
    parser.add_argument(
        "--calibration-manifest", type=Path, default=DEFAULT_CALIBRATION
    )
    parser.add_argument("--holdout-manifest", type=Path, default=DEFAULT_HOLDOUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    events = load_events(args.calibration_manifest, args.holdout_manifest)
    fold_rows, summary_rows = evaluate(events)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "event_features.csv", events)
    write_csv(args.output_dir / "fold_results.csv", fold_rows)
    write_csv(args.output_dir / "summary.csv", summary_rows)

    print(f"Events {len(events)} across {len(DATE_FOLDS)} held-out dates")
    print("Feature | direction | gated TP | FN | FP | TN | zero-FP folds")
    for row in summary_rows:
        print(
            f"{row['feature']} | {row['direction']} | {row['gate_tp']} | {row['gate_fn']} | {row['gate_fp']} | {row['gate_tn']}"
            f" | {row['zero_fp_folds']}/{row['folds']}"
        )
    print(f"Results written to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
