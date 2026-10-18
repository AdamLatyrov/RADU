from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = (
    ROOT / "data" / "review" / "executed_actions_gigaam_20261014_202442" / "results.csv"
)
DEFAULT_OUTPUT = ROOT / "data" / "review" / "trigger_feature_overlap_20261015"


def read_events(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))

    events = []
    for row in rows:
        if row["label"] not in {"positive", "negative", "unresolved"}:
            continue
        metadata_path = ROOT / row["source_metadata"]
        metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        audio = metadata["audio"]
        capture = metadata["capture"]
        events.append(
            {
                "observation_id": row["observation_id"],
                "command_id": row["command_id"],
                "label": row["label"],
                "vosk_text": row["vosk_text"],
                "gigaam_text": row["gigaam_text"],
                "wav_duration_seconds": float(audio["duration_seconds"]),
                "capture_duration_seconds": float(capture["duration_seconds"]),
                "speech_frames": int(capture["vad_speech_frames"]),
                "leading_silence_ms": float(capture["leading_silence_trimmed_samples"])
                / 16.0,
                "rms_pcm": float(audio["rms_pcm"]),
                "peak_abs_pcm": int(audio["peak_abs_pcm"]),
                "word_confidence_min": float(
                    metadata["recognition"]["word_confidence_min"]
                ),
                "word_confidence_mean": float(
                    metadata["recognition"]["word_confidence_mean"]
                ),
            }
        )
    return events


def summarize(events: list[dict]) -> list[dict]:
    features = [
        "capture_duration_seconds",
        "speech_frames",
        "leading_silence_ms",
        "rms_pcm",
        "peak_abs_pcm",
        "wav_duration_seconds",
    ]
    positives = [event for event in events if event["label"] == "positive"]
    negatives = [event for event in events if event["label"] == "negative"]
    summaries = []
    for feature in features:
        negative_values = [event[feature] for event in negatives]
        for direction in ("below", "above"):
            boundary = (
                min(negative_values) if direction == "below" else max(negative_values)
            )
            retained = sum(
                event[feature] < boundary
                if direction == "below"
                else event[feature] > boundary
                for event in positives
            )
            heldout_negative_rejections = 0
            heldout_positive_retention = []
            for heldout_index, heldout_event in enumerate(negatives):
                calibration_values = [
                    event[feature]
                    for index, event in enumerate(negatives)
                    if index != heldout_index
                ]
                fold_boundary = (
                    min(calibration_values)
                    if direction == "below"
                    else max(calibration_values)
                )
                heldout_value = heldout_event[feature]
                rejected = (
                    heldout_value >= fold_boundary
                    if direction == "below"
                    else heldout_value <= fold_boundary
                )
                heldout_negative_rejections += rejected
                heldout_positive_retention.append(
                    sum(
                        event[feature] < fold_boundary
                        if direction == "below"
                        else event[feature] > fold_boundary
                        for event in positives
                    )
                )
            summaries.append(
                {
                    "feature": feature,
                    "zero_false_accept_direction": direction,
                    "strict_boundary_from_negatives": boundary,
                    "positives_retained": retained,
                    "positives_total": len(positives),
                    "positives_rejected": len(positives) - retained,
                    "negatives_retained": 0,
                    "negatives_total": len(negatives),
                    "leave_one_negative_out_rejections": heldout_negative_rejections,
                    "leave_one_negative_out_folds": len(negatives),
                    "mean_positives_retained_in_folds": round(
                        sum(heldout_positive_retention)
                        / len(heldout_positive_retention),
                        2,
                    ),
                }
            )
    return summaries


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError(f"No rows to write to {path}")
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare simple capture features on GigaAM-reviewed RADU actions"
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    events = read_events(args.input)
    scored = [event for event in events if event["label"] in {"positive", "negative"}]
    positives = sum(event["label"] == "positive" for event in scored)
    negatives = sum(event["label"] == "negative" for event in scored)
    if not positives or not negatives:
        raise ValueError("Both positive and negative reviewed records are required")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "event_features.csv", events)
    summaries = summarize(scored)
    write_csv(args.output_dir / "zero_false_accept_thresholds.csv", summaries)

    print(
        f"Events {len(events)}; positives {positives}; negatives {negatives}; unresolved {len(events) - len(scored)}"
    )
    for row in summaries:
        print(
            f"{row['feature']} {row['zero_false_accept_direction']} {row['strict_boundary_from_negatives']:.4f}"
            f" retains {row['positives_retained']}/{row['positives_total']} positives"
        )
    print(f"Results written to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
