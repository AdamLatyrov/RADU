from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from review_confidence_rejections_with_gigaam import classify

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_CALIBRATION = (
    PROJECT
    / "data"
    / "review"
    / "kaldi_oct14_session_holdout_20261014"
    / "calibration_nbest.json"
)
DEFAULT_HOLDOUT = (
    PROJECT
    / "data"
    / "review"
    / "kaldi_oct14_session_holdout_20261014"
    / "lattices.json"
)
DEFAULT_COMMANDS = PROJECT / "commands.json"
DEFAULT_OUTPUT = (
    PROJECT
    / "data"
    / "review"
    / "kaldi_oct14_session_holdout_20261014"
    / "phone_path_analysis"
)


def normalize_phone_duration_distance(
    left: list[tuple[int, float]], right: list[tuple[int, float]]
) -> float:
    if not left or not right:
        return float("inf")
    left_total = sum(duration for _, duration in left) or 1.0
    right_total = sum(duration for _, duration in right) or 1.0
    left_values = [duration / left_total for _, duration in left]
    right_values = [duration / right_total for _, duration in right]
    previous = [float(index) for index in range(len(right) + 1)]
    for i, (left_phone, _) in enumerate(left, start=1):
        current = [float(i)]
        for j, (right_phone, _) in enumerate(right, start=1):
            substitution = (
                abs(left_values[i - 1] - right_values[j - 1])
                if left_phone == right_phone
                else 1.0
            )
            current.append(
                min(
                    current[-1] + 1.0, previous[j] + 1.0, previous[j - 1] + substitution
                )
            )
        previous = current
    return previous[-1] / max(1, len(left), len(right))


def phone_path(alternative: dict) -> list[tuple[int, float]]:
    return [
        (int(phone["phone_id"]), max(0.0, float(phone["end"]) - float(phone["start"])))
        for phone in alternative.get("phone_path", [])
        if phone.get("phone_id") is not None
    ]


def command_path(
    record: dict, field: str, phrases: list[str]
) -> list[tuple[int, float]]:
    for alternative in record.get(field, []):
        if classify(str(alternative.get("text", "")), phrases)[0] == "positive":
            path = phone_path(alternative)
            if path:
                return path
    return []


def any_path(record: dict, field: str) -> list[list[tuple[int, float]]]:
    return [path for item in record.get(field, []) if (path := phone_path(item))]


def min_distance(
    path: list[tuple[int, float]], references: list[list[tuple[int, float]]]
) -> float | None:
    if not path or not references:
        return None
    return min(normalize_phone_duration_distance(path, ref) for ref in references)


def fit_zero_false_threshold(rows: list[dict]) -> tuple[float | None, dict]:
    positive_distances = [
        row["distance"]
        for row in rows
        if row["label"] == "positive" and row["distance"] is not None
    ]
    negative_distances = [
        row["distance"]
        for row in rows
        if row["label"] == "negative" and row["distance"] is not None
    ]
    details = {
        "calibration_positive_scored": len(positive_distances),
        "calibration_negative_scored": len(negative_distances),
        "max_positive_leave_one_out": max(positive_distances)
        if positive_distances
        else None,
        "min_negative": min(negative_distances) if negative_distances else None,
    }
    if not positive_distances or not negative_distances:
        return None, details
    max_positive = max(positive_distances)
    min_negative = min(negative_distances)
    if max_positive >= min_negative:
        return None, details
    return (max_positive + min_negative) / 2.0, details


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Calibrate phonetic-path distance on old sessions and evaluate on a held-out session"
    )
    parser.add_argument("--calibration", type=Path, default=DEFAULT_CALIBRATION)
    parser.add_argument("--holdout", type=Path, default=DEFAULT_HOLDOUT)
    parser.add_argument("--commands", type=Path, default=DEFAULT_COMMANDS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    command_data = json.loads(args.commands.read_text(encoding="utf-8"))
    phrases = {
        str(command["id"]): list(command.get("phrases", []))
        for command in command_data["commands"]
    }
    calibration = json.loads(args.calibration.read_text(encoding="utf-8"))["results"]
    holdout = json.loads(args.holdout.read_text(encoding="utf-8"))["results"]

    strategies = {
        "grammar_target_path": lambda record, cmd: [
            command_path(record, "grammar_alternatives", phrases[cmd])
        ],
        "free_any_path": lambda record, cmd: any_path(
            record, "unrestricted_alternatives"
        ),
    }
    detail_rows: list[dict] = []
    summary_rows: list[dict] = []
    for strategy_name, get_candidates in strategies.items():
        command_ids = sorted(
            {str(record["command_id"]) for record in calibration + holdout}
        )
        for command_id in command_ids:
            train_records = [
                record
                for record in calibration
                if str(record["command_id"]) == command_id
            ]
            test_records = [
                record for record in holdout if str(record["command_id"]) == command_id
            ]
            positive_paths = {
                record["observation_id"]: command_path(
                    record, "grammar_alternatives", phrases[command_id]
                )
                for record in train_records
                if record["label"] == "positive"
            }
            positive_refs = [path for path in positive_paths.values() if path]

            train_rows = []
            for record in train_records:
                candidates = get_candidates(record, command_id)
                candidates = [path for path in candidates if path]
                if record["label"] == "positive":
                    other_refs = [
                        path
                        for observation_id, path in positive_paths.items()
                        if observation_id != record["observation_id"] and path
                    ]
                else:
                    other_refs = positive_refs
                distances = [
                    min_distance(candidate, other_refs) for candidate in candidates
                ]
                usable = [value for value in distances if value is not None]
                distance = min(usable) if usable else None
                train_rows.append(
                    {
                        "observation_id": record["observation_id"],
                        "label": record["label"],
                        "distance": distance,
                    }
                )

            threshold, fit = fit_zero_false_threshold(train_rows)
            test_rows = []
            for record in test_records:
                candidates = get_candidates(record, command_id)
                distances = [
                    min_distance(candidate, positive_refs) for candidate in candidates
                ]
                usable = [value for value in distances if value is not None]
                distance = min(usable) if usable else None
                accepted = (
                    threshold is not None
                    and distance is not None
                    and distance <= threshold
                )
                test_rows.append(
                    {
                        "observation_id": record["observation_id"],
                        "label": record["label"],
                        "distance": distance,
                        "accepted": accepted,
                    }
                )
                detail_rows.append(
                    {
                        "strategy": strategy_name,
                        "command_id": command_id,
                        **test_rows[-1],
                        "threshold": threshold,
                        "holdout_candidate_paths": len(candidates),
                    }
                )

            summary_rows.append(
                {
                    "strategy": strategy_name,
                    "command_id": command_id,
                    **fit,
                    "threshold": threshold,
                    "holdout_positive": sum(
                        row["label"] == "positive" for row in test_rows
                    ),
                    "holdout_negative": sum(
                        row["label"] == "negative" for row in test_rows
                    ),
                    "tp": sum(
                        row["label"] == "positive" and row["accepted"]
                        for row in test_rows
                    ),
                    "fn": sum(
                        row["label"] == "positive" and not row["accepted"]
                        for row in test_rows
                    ),
                    "fp": sum(
                        row["label"] == "negative" and row["accepted"]
                        for row in test_rows
                    ),
                    "tn": sum(
                        row["label"] == "negative" and not row["accepted"]
                        for row in test_rows
                    ),
                    "fit_status": "zero_false_threshold"
                    if threshold is not None
                    else "no_zero_false_threshold",
                }
            )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for filename, rows in (
        ("holdout_phone_distances.csv", detail_rows),
        ("command_summary.csv", summary_rows),
    ):
        with (args.output_dir / filename).open(
            "w", encoding="utf-8-sig", newline=""
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    print(f"calibration_records={len(calibration)} holdout_records={len(holdout)}")
    for row in summary_rows:
        if row["threshold"] is not None:
            print(
                f"{row['strategy']} {row['command_id']} threshold={row['threshold']:.5f} TP={row['tp']} FN={row['fn']} FP={row['fp']} TN={row['tn']}"
            )
        else:
            print(f"{row['strategy']} {row['command_id']} no_zero_false_threshold")
    print(f"outputs={args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
