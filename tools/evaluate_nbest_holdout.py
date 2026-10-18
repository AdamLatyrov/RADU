from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

from review_confidence_rejections_with_gigaam import classify

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = (
    PROJECT
    / "data"
    / "review"
    / "kaldi_oct14_session_holdout_20261014"
    / "lattices.json"
)
DEFAULT_COMMANDS = PROJECT / "commands.json"
DEFAULT_OUTPUT = DEFAULT_INPUT.parent / "nbest_analysis"
DECODERS = ("grammar_alternatives", "unrestricted_alternatives")
TOP_N_VALUES = (1, 3, 5, 10, 20)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate command phrase presence in held-out Vosk N-best lists"
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--commands", type=Path, default=DEFAULT_COMMANDS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    data = json.loads(args.input.read_text(encoding="utf-8"))
    command_data = json.loads(args.commands.read_text(encoding="utf-8"))
    phrases = {
        str(command["id"]): list(command.get("phrases", []))
        for command in command_data["commands"]
    }

    record_rows: list[dict] = []
    totals: dict[tuple[str, int, str], dict[str, int]] = defaultdict(
        lambda: {"tp": 0, "fn": 0, "fp": 0, "tn": 0}
    )
    for record in data["results"]:
        command_id = str(record["command_id"])
        label = str(record["label"])
        command_phrases = phrases.get(command_id, [])
        for decoder in DECODERS:
            alternatives = record.get(decoder, [])
            first_rank = None
            first_text = ""
            for rank, alternative in enumerate(alternatives, start=1):
                if (
                    classify(str(alternative.get("text", "")), command_phrases)[0]
                    == "positive"
                ):
                    first_rank = rank
                    first_text = str(alternative.get("text", ""))
                    break
            for top_n in TOP_N_VALUES:
                accepted = first_rank is not None and first_rank <= top_n
                positive = label == "positive"
                key = (decoder, top_n, command_id)
                outcome = (
                    "tp"
                    if accepted and positive
                    else "fp"
                    if accepted
                    else "fn"
                    if positive
                    else "tn"
                )
                totals[key][outcome] += 1
                record_rows.append(
                    {
                        "observation_id": record["observation_id"],
                        "command_id": command_id,
                        "label": label,
                        "decoder": decoder,
                        "top_n": top_n,
                        "accepted": accepted,
                        "first_command_rank": first_rank,
                        "first_command_text": first_text,
                    }
                )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    record_path = args.output_dir / "record_level_nbest.csv"
    with record_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(record_rows[0]))
        writer.writeheader()
        writer.writerows(record_rows)

    summary_rows = []
    for (decoder, top_n, command_id), counts in sorted(totals.items()):
        summary_rows.append(
            {
                "decoder": decoder,
                "top_n": top_n,
                "command_id": command_id,
                "positive_count": counts["tp"] + counts["fn"],
                "negative_count": counts["fp"] + counts["tn"],
                **counts,
            }
        )
    summary_path = args.output_dir / "command_summary.csv"
    with summary_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary_rows[0]))
        writer.writeheader()
        writer.writerows(summary_rows)

    overall = defaultdict(lambda: {"tp": 0, "fn": 0, "fp": 0, "tn": 0})
    for (decoder, top_n, _command_id), counts in totals.items():
        for key, value in counts.items():
            overall[(decoder, top_n)][key] += value
    print(f"records={len(data['results'])}")
    for (decoder, top_n), counts in sorted(overall.items()):
        print(
            f"{decoder} top{top_n} "
            f"TP={counts['tp']} FN={counts['fn']} FP={counts['fp']} TN={counts['tn']}"
        )
    print(f"outputs={args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
