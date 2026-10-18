from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from time import perf_counter

import gigaam
import torch

ROOT = Path(__file__).resolve().parents[1]
MODEL_ROOT = Path.home() / ".cache" / "gigaam"
MODEL_PATH = MODEL_ROOT / "v2_rnnt.ckpt"
DEFAULT_CROPS = (
    ROOT / "data" / "review" / "youtube_kws_pilot_20261015" / "crop_manifest.csv"
)
DEFAULT_OUTPUT = (
    ROOT / "data" / "review" / "youtube_kws_pilot_20261015" / "crop_validation.csv"
)
SAMPLE_RATE = 16000
TARGETS = {"открой ютуб", "открой ютюб"}


def decode_crop(model, wav: torch.Tensor, samples: int) -> tuple[str, int]:
    length = torch.tensor([samples], device=wav.device, dtype=torch.long)
    encoded, encoded_len = model.forward(wav, length)
    sequence_length = int(encoded_len[0].item())
    encoded_sequence = encoded[0].transpose(0, 1).unsqueeze(1)
    tokenizer = model.decoding.tokenizer
    if not tokenizer.charwise:
        raise RuntimeError("The cached RNNT tokenizer is not character based")

    token_ids: list[int] = []
    state = None
    last_label = None
    for frame_index in range(sequence_length):
        frame = encoded_sequence[frame_index, :, :].unsqueeze(1)
        active = True
        symbols = 0
        while active and symbols < model.decoding.max_symbols:
            prediction, next_state = model.head.decoder.predict(last_label, state)
            token_id = (
                model.head.joint.joint(frame, prediction)[0, 0, 0, :].argmax(0).item()
            )
            if token_id == model.decoding.blank_id:
                active = False
                continue
            token_ids.append(token_id)
            state = next_state
            last_label = torch.tensor([[token_id]], device=encoded_sequence.device)
            symbols += 1
    return tokenizer.decode(token_ids), sequence_length


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Re-decode proposed target crops in memory"
    )
    parser.add_argument("--crops", type=Path, default=DEFAULT_CROPS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-crops", type=int, default=None)
    args = parser.parse_args()
    if not MODEL_PATH.is_file():
        raise SystemExit("The cached GigaAM v2 RNNT checkpoint is missing")

    with args.crops.open(encoding="utf-8-sig", newline="") as source:
        rows = [
            row
            for row in csv.DictReader(source)
            if row.get("resolved_label") == "positive"
        ]
    with (ROOT / "data" / "audio_library" / "command_dataset_map.csv").open(
        encoding="utf-8-sig", newline=""
    ) as source:
        source_folders = {
            row["SHA256"]: row["Folder"]
            for row in csv.DictReader(source)
            if row.get("CommandID") == "youtube"
        }
    if args.max_crops is not None:
        rows = rows[: max(0, args.max_crops)]

    torch.set_num_threads(2)
    load_started = perf_counter()
    model = gigaam.load_model("v2_rnnt", device="cpu", download_root=str(MODEL_ROOT))
    load_seconds = perf_counter() - load_started
    results: list[dict[str, object]] = []
    for index, row in enumerate(rows, start=1):
        folder = source_folders.get(row.get("sha256", ""))
        if not folder:
            results.append(
                {
                    "file": row["file"],
                    "occurrence": row["occurrence"],
                    "target_found_in_crop": False,
                    "error": "source_folder_missing",
                }
            )
            continue
        wav_path = ROOT / "data" / "audio_library" / folder / row["file"]
        try:
            full_wav, _ = model.prepare_wav(str(wav_path))
            start = max(0, int(float(row["crop_start_seconds"]) * SAMPLE_RATE))
            end = min(
                full_wav.shape[-1], int(float(row["crop_end_seconds"]) * SAMPLE_RATE)
            )
            crop = full_wav[..., start:end]
            started = perf_counter()
            transcript, frames = decode_crop(model, crop, end - start)
            elapsed = perf_counter() - started
            normalized = " ".join(transcript.casefold().split())
            found = any(target in normalized for target in TARGETS)
            results.append(
                {
                    "file": row["file"],
                    "occurrence": row["occurrence"],
                    "crop_duration_seconds": row["crop_duration_seconds"],
                    "original_transcript": row["gigaam_transcript"],
                    "crop_transcript": transcript,
                    "target_found_in_crop": found,
                    "encoded_frames": frames,
                    "inference_seconds": round(elapsed, 4),
                    "error": "",
                }
            )
            print(
                f"{index}/{len(rows)} target={found} duration={row['crop_duration_seconds']}",
                flush=True,
            )
        except Exception as error:
            results.append(
                {
                    "file": row["file"],
                    "occurrence": row["occurrence"],
                    "target_found_in_crop": False,
                    "error": repr(error),
                }
            )
            print(f"{index}/{len(rows)} failed: {error!r}", flush=True)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "file",
        "occurrence",
        "crop_duration_seconds",
        "original_transcript",
        "crop_transcript",
        "target_found_in_crop",
        "encoded_frames",
        "inference_seconds",
        "error",
    ]
    with args.output.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)
    summary = {
        "input_rows": len(rows),
        "model_load_seconds": round(load_seconds, 3),
        "target_found": sum(row.get("target_found_in_crop") is True for row in results),
        "target_missing_or_error": sum(
            row.get("target_found_in_crop") is not True for row in results
        ),
        "errors": sum(bool(row.get("error")) for row in results),
        "crop_wav_files_written": 0,
        "result_csv": str(args.output),
        "interpretation": "GigaAM re-decode is an alignment sanity check, not independent ground-truth verification.",
        "label_counts": dict(
            Counter(str(row.get("target_found_in_crop")) for row in results)
        ),
    }
    args.output.with_suffix(".summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
