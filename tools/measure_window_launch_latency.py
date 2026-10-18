from __future__ import annotations

import argparse
import csv
import ctypes
import statistics
import sys
import time
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    PROJECT / "data" / "review" / f"window_launch_latency_{datetime.now():%Y%m%d}.csv"
)
WINDOW_CLASS_NAMES = {"notepad", "notepadapp"}
WINDOW_TITLE_PARTS = ("notepad", "блокнот")
WM_CLOSE = 0x0010


def visible_notepad_windows() -> list[dict[str, object]]:
    user32 = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    found: list[dict[str, object]] = []

    @callback_type
    def visit(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        title_length = user32.GetWindowTextLengthW(hwnd)
        title_buffer = ctypes.create_unicode_buffer(title_length + 1)
        user32.GetWindowTextW(hwnd, title_buffer, title_length + 1)
        class_buffer = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_buffer, len(class_buffer))
        title = title_buffer.value
        class_name = class_buffer.value
        if class_name.casefold() in WINDOW_CLASS_NAMES or any(
            part in title.casefold() for part in WINDOW_TITLE_PARTS
        ):
            process_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
            found.append(
                {
                    "hwnd": int(hwnd),
                    "title": title,
                    "class_name": class_name,
                    "process_id": int(process_id.value),
                }
            )
        return True

    user32.EnumWindows(visit, 0)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure RADU open_app return and Notepad visible-window delay"
    )
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--app", type=Path, default=Path(r"C:\Windows\System32\notepad.exe")
    )
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("This diagnostic requires Windows")
    if args.count < 1:
        raise SystemExit("count must be positive")

    existing = visible_notepad_windows()
    if existing:
        raise SystemExit(
            "A Notepad window is already visible. Close it before this diagnostic"
        )

    sys.path.insert(0, str(PROJECT))
    from assistant.actions import execute

    rows: list[dict[str, object]] = []
    for run_index in range(1, args.count + 1):
        started = time.perf_counter()
        execute({"type": "open_app", "value": str(args.app)})
        action_returned = time.perf_counter()
        deadline = started + 20.0
        windows: list[dict[str, object]] = []
        while time.perf_counter() < deadline:
            windows = visible_notepad_windows()
            if windows:
                break
            time.sleep(0.005)
        if not windows:
            raise RuntimeError(f"No Notepad window appeared for run {run_index}")

        window_seen = time.perf_counter()
        target = windows[0]
        rows.append(
            {
                "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
                "run": run_index,
                "action_return_ms": round((action_returned - started) * 1000, 1),
                "window_visible_ms": round((window_seen - started) * 1000, 1),
                "window_after_action_ms": round(
                    (window_seen - action_returned) * 1000, 1
                ),
                "window_title": target["title"],
                "window_class": target["class_name"],
            }
        )

        ctypes.windll.user32.PostMessageW(target["hwnd"], WM_CLOSE, 0, 0)
        close_deadline = time.perf_counter() + 10.0
        while time.perf_counter() < close_deadline and visible_notepad_windows():
            time.sleep(0.02)
        if visible_notepad_windows():
            raise RuntimeError(
                "Notepad stayed open after WM_CLOSE; stopping to preserve the current state"
            )
        time.sleep(0.5)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    for row in rows:
        print(
            f"run={row['run']} action_return_ms={row['action_return_ms']} "
            f"window_visible_ms={row['window_visible_ms']} "
            f"window_after_action_ms={row['window_after_action_ms']}"
        )
    for field in ("action_return_ms", "window_visible_ms", "window_after_action_ms"):
        values = [float(row[field]) for row in rows]
        print(
            f"{field} median_ms={statistics.median(values):.1f} "
            f"mean_ms={statistics.mean(values):.1f} first_ms={values[0]:.1f}"
        )
    print(f"results={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
