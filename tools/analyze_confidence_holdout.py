from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = (
    ROOT
    / "data"
    / "review"
    / "confidence_calibration_20261014"
    / "confidence_examples.csv"
)
DEFAULT_OUTPUT = ROOT / "data" / "review" / "confidence_calibration_20261014"
CALIBRATION_COHORT = "existing_library"
HOLDOUT_PREFIX = "oct14_"


def fit_threshold(positive_scores: list[float], negative_scores: list[float]) -> dict:
    if not positive_scores:
        return {"threshold": None, "status": "no_calibration_positives"}
    if not negative_scores:
        return {"threshold": None, "status": "no_calibration_negatives"}
    min_positive = min(positive_scores)
    max_negative = max(negative_scores)
    if max_negative >= min_positive:
        return {"threshold": None, "status": "overlap"}
    return {
        "threshold": (max_negative + min_positive) / 2.0,
        "status": "separated",
    }


def numeric_score(row: dict) -> float | None:
    value = row.get("word_confidence_min", "")
    if not value:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def confusion(rows: list[dict], threshold: float | None) -> dict[str, int]:
    result = {"tp": 0, "fn": 0, "fp": 0, "tn": 0, "missing_score": 0}
    for row in rows:
        score = numeric_score(row)
        if score is None:
            result["missing_score"] += 1
            continue
        accepted = threshold is not None and score >= threshold
        if row["label"] == "positive":
            result["tp" if accepted else "fn"] += 1
        elif row["label"] == "negative":
            result["fp" if accepted else "tn"] += 1
    return result


def analyze(input_path: Path) -> list[dict]:
    with input_path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    commands = sorted({row["command_id"] for row in rows})
    output = []
    for command_id in commands:
        calibration = [
            row
            for row in rows
            if row["command_id"] == command_id
            and row["cohort"] == CALIBRATION_COHORT
            and row["label"] in {"positive", "negative"}
        ]
        holdout = [
            row
            for row in rows
            if row["command_id"] == command_id
            and row["cohort"].startswith(HOLDOUT_PREFIX)
            and row["label"] in {"positive", "negative"}
        ]
        positive_scores = [
            numeric_score(row) for row in calibration if row["label"] == "positive"
        ]
        negative_scores = [
            numeric_score(row) for row in calibration if row["label"] == "negative"
        ]
        fitted = fit_threshold(
            [score for score in positive_scores if score is not None],
            [score for score in negative_scores if score is not None],
        )
        output.append(
            {
                "command_id": command_id,
                "calibration_positive": sum(
                    row["label"] == "positive" for row in calibration
                ),
                "calibration_negative": sum(
                    row["label"] == "negative" for row in calibration
                ),
                "calibration_missing_scores": sum(
                    numeric_score(row) is None for row in calibration
                ),
                "calibration_status": fitted["status"],
                "fitted_threshold": fitted["threshold"],
                "holdout_positive": sum(row["label"] == "positive" for row in holdout),
                "holdout_negative": sum(row["label"] == "negative" for row in holdout),
                "holdout_missing_scores": sum(
                    numeric_score(row) is None for row in holdout
                ),
                "holdout_at_one": confusion(holdout, 1.0),
                "holdout_at_fitted_threshold": confusion(holdout, fitted["threshold"]),
            }
        )
    return output


def save_results(results: list[dict], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "session_holdout_results.json"
    json_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    csv_path = output_dir / "session_holdout_results.csv"
    fields = [
        "command_id",
        "calibration_positive",
        "calibration_negative",
        "calibration_missing_scores",
        "calibration_status",
        "fitted_threshold",
        "holdout_positive",
        "holdout_negative",
        "holdout_missing_scores",
        "at_one_tp",
        "at_one_fn",
        "at_one_fp",
        "at_one_tn",
        "at_one_missing_score",
        "at_fitted_tp",
        "at_fitted_fn",
        "at_fitted_fp",
        "at_fitted_tn",
        "at_fitted_missing_score",
    ]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in results:
            flat = {
                key: value for key, value in row.items() if not isinstance(value, dict)
            }
            for prefix, key in (
                ("at_one", "holdout_at_one"),
                ("at_fitted", "holdout_at_fitted_threshold"),
            ):
                flat.update(
                    {f"{prefix}_{metric}": value for metric, value in row[key].items()}
                )
            writer.writerow(flat)
    print(f"Saved {len(results)} command rows to {csv_path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Calibrate command confidence on earlier cohorts and evaluate on an October 14 holdout"
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    results = analyze(args.input)
    save_results(results, args.output_dir)
    for row in results:
        print(json.dumps(row, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
