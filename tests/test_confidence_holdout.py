from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "analyze_confidence_holdout.py"
SPEC = importlib.util.spec_from_file_location("confidence_holdout", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def main() -> None:
    separated = MODULE.fit_threshold([0.8, 0.9], [0.5, 0.7])
    assert separated["threshold"] == 0.75
    assert separated["status"] == "separated"

    overlapping = MODULE.fit_threshold([0.8, 0.9], [0.5, 0.85])
    assert overlapping["threshold"] is None
    assert overlapping["status"] == "overlap"

    missing_negative = MODULE.fit_threshold([0.8, 0.9], [])
    assert missing_negative["threshold"] is None
    assert missing_negative["status"] == "no_calibration_negatives"

    print("Passed session-held-out confidence threshold checks")


if __name__ == "__main__":
    main()
