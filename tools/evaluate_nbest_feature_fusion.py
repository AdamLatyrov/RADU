from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
from review_confidence_rejections_with_gigaam import classify
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_REVIEW = PROJECT / "data" / "review" / "kaldi_oct14_session_holdout_20261014"
FEATURE_NAMES = (
    "target_present",
    "target_rank",
    "target_confidence",
    "target_acoustic_cost_per_frame",
    "target_graph_cost_per_frame",
    "target_minus_top_acoustic_cost",
    "target_minus_top_graph_cost",
    "target_phone_frames",
)


def load_results(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["results"]


def make_features(record: dict, phrases: dict[str, list[str]]) -> list[float]:
    alternatives = record.get("unrestricted_alternatives", [])
    target_id = str(record["command_id"])
    target_phrases = phrases.get(target_id, [])
    candidates = [
        (rank, alternative)
        for rank, alternative in enumerate(alternatives, start=1)
        if classify(str(alternative.get("text", "")), target_phrases)[0] == "positive"
    ]
    if not candidates or not alternatives:
        return [0.0, 21.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

    rank, target = min(candidates, key=lambda item: item[0])
    top = alternatives[0]
    target_frames = max(1, int(target.get("phone_frames") or 1))
    top_frames = max(1, int(top.get("phone_frames") or 1))
    target_acoustic = float(
        target.get(
            "acoustic_cost_per_model_frame",
            target.get("acoustic_cost", 0.0) / target_frames,
        )
    )
    top_acoustic = float(
        top.get(
            "acoustic_cost_per_model_frame", top.get("acoustic_cost", 0.0) / top_frames
        )
    )
    target_graph = float(target.get("graph_cost", 0.0)) / target_frames
    top_graph = float(top.get("graph_cost", 0.0)) / top_frames
    return [
        1.0,
        float(rank),
        float(target.get("confidence", 0.0)),
        target_acoustic,
        target_graph,
        target_acoustic - top_acoustic,
        target_graph - top_graph,
        float(target_frames),
    ]


def summarize(
    scores: np.ndarray, labels: np.ndarray, threshold: float
) -> tuple[int, int, int, int]:
    accepted = scores >= threshold
    return (
        int(np.count_nonzero(accepted & labels)),
        int(np.count_nonzero(~accepted & labels)),
        int(np.count_nonzero(accepted & ~labels)),
        int(np.count_nonzero(~accepted & ~labels)),
    )


def baseline_counts(
    records: list[dict], phrases: dict[str, list[str]], decoder: str, depth: int
) -> tuple[int, int, int, int]:
    tp = fn = fp = tn = 0
    for record in records:
        alternatives = record.get(decoder, [])[:depth]
        accepted = any(
            classify(
                str(item.get("text", "")), phrases.get(str(record["command_id"]), [])
            )[0]
            == "positive"
            for item in alternatives
        )
        positive = record["label"] == "positive"
        if accepted and positive:
            tp += 1
        elif accepted:
            fp += 1
        elif positive:
            fn += 1
        else:
            tn += 1
    return tp, fn, fp, tn


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Calibrate a small N-best feature fusion model on earlier RADU sessions and score a fixed holdout."
    )
    parser.add_argument("--review-dir", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--commands", type=Path, default=PROJECT / "commands.json")
    parser.add_argument("--calibration-file", default="calibration_nbest.json")
    parser.add_argument("--holdout-file", default="lattices.json")
    args = parser.parse_args()

    command_data = json.loads(args.commands.read_text(encoding="utf-8"))
    phrases = {
        str(command["id"]): list(command.get("phrases", []))
        for command in command_data["commands"]
    }
    calibration = load_results(args.review_dir / args.calibration_file)
    holdout = load_results(args.review_dir / args.holdout_file)
    if not calibration or not holdout:
        parser.error("Calibration and holdout files must both contain records")

    x_train = np.asarray(
        [make_features(row, phrases) for row in calibration], dtype=float
    )
    y_train = np.asarray(
        [row["label"] == "positive" for row in calibration], dtype=bool
    )
    x_holdout = np.asarray(
        [make_features(row, phrases) for row in holdout], dtype=float
    )
    y_holdout = np.asarray([row["label"] == "positive" for row in holdout], dtype=bool)

    model = make_pipeline(
        StandardScaler(),
        LogisticRegression(C=1.0, max_iter=3000, random_state=0),
    )
    model.fit(x_train, y_train)
    calibration_scores = model.predict_proba(x_train)[:, 1]
    holdout_scores = model.predict_proba(x_holdout)[:, 1]
    negative_scores = calibration_scores[~y_train]
    if not len(negative_scores):
        parser.error("Calibration set needs at least one negative record")
    threshold = float(np.nextafter(float(negative_scores.max()), 1.0))

    train_metrics = summarize(calibration_scores, y_train, threshold)
    holdout_metrics = summarize(holdout_scores, y_holdout, threshold)
    print("Features", ", ".join(FEATURE_NAMES))
    print(
        f"Calibration n={len(y_train)} positive={int(y_train.sum())} negative={int((~y_train).sum())} "
        f"threshold={threshold:.6f} TP/FN/FP/TN={train_metrics}"
    )
    print(
        f"Holdout n={len(y_holdout)} positive={int(y_holdout.sum())} negative={int((~y_holdout).sum())} "
        f"TP/FN/FP/TN={holdout_metrics}"
    )
    print("Holdout baselines TP/FN/FP/TN")
    print(
        "  grammar top 1", baseline_counts(holdout, phrases, "grammar_alternatives", 1)
    )
    print(
        "  unrestricted top 20",
        baseline_counts(holdout, phrases, "unrestricted_alternatives", 20),
    )

    per_command: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for row, score, positive in zip(holdout, holdout_scores, y_holdout):
        accepted = score >= threshold
        index = (
            0
            if accepted and positive
            else 1
            if not accepted and positive
            else 2
            if accepted
            else 3
        )
        per_command[str(row["command_id"])][index] += 1
    print("Holdout by command TP/FN/FP/TN")
    for command_id, counts in sorted(per_command.items()):
        print(f"  {command_id}", tuple(counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
