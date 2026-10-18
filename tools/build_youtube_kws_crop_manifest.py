from __future__ import annotations

import argparse
import csv
import hashlib
import json
import wave
from pathlib import Path
from time import perf_counter

import gigaam
import torch

ROOT = Path(__file__).resolve().parents[1]
MODEL_ROOT = Path.home() / ".cache" / "gigaam"
MODEL_PATH = MODEL_ROOT / "v2_rnnt.ckpt"
MANIFEST = ROOT / "data" / "audio_library" / "command_dataset_map.csv"
OUTPUT = ROOT / "data" / "review" / "youtube_kws_pilot_20261015" / "crop_manifest.csv"
TARGET_VARIANTS = ("открой ютуб", "открой ютюб")
EDGE_PADDING_SECONDS = 0.16
SAMPLE_RATE = 16000


def read_audio_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as audio:
        return audio.getnframes() / audio.getframerate()


def decode_with_token_frames(
    model, wav_path: Path
) -> tuple[str, list[tuple[str, int]], float]:
    wav, length = model.prepare_wav(str(wav_path))
    encoded, encoded_len = model.forward(wav, length)
    sequence_length = int(encoded_len[0].item())
    frame_shift = float(length[0].item()) / SAMPLE_RATE / sequence_length
    encoded_sequence = encoded[0].transpose(0, 1).unsqueeze(1)
    tokenizer = model.decoding.tokenizer
    if not tokenizer.charwise:
        raise RuntimeError(
            "This crop utility currently expects the cached character-tokenized v2 RNNT model"
        )

    token_ids: list[int] = []
    token_frames: list[int] = []
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
            token_frames.append(frame_index)
            state = next_state
            last_label = torch.tensor([[token_id]], device=encoded_sequence.device)
            symbols += 1

    text = tokenizer.decode(token_ids)
    tokens = [
        (tokenizer.vocab[token_id], frame)
        for token_id, frame in zip(token_ids, token_frames)
    ]
    return text, tokens, frame_shift


def find_phrase_crops(
    text: str, tokens: list[tuple[str, int]], frame_shift: float, duration: float
) -> list[dict[str, object]]:
    lowered = text.casefold()
    char_spans: list[tuple[int, int, str]] = []
    for phrase in TARGET_VARIANTS:
        offset = 0
        while True:
            start = lowered.find(phrase, offset)
            if start < 0:
                break
            char_spans.append((start, start + len(phrase), phrase))
            offset = start + len(phrase)

    crops = []
    if len(tokens) != len(text):
        raise RuntimeError("Character timing count does not match decoded GigaAM text")
    for occurrence, (char_start, char_end, phrase) in enumerate(
        sorted(char_spans), start=1
    ):
        start = max(0.0, tokens[char_start][1] * frame_shift - EDGE_PADDING_SECONDS)
        end = min(
            duration, (tokens[char_end - 1][1] + 1) * frame_shift + EDGE_PADDING_SECONDS
        )
        if end > start:
            crops.append(
                {
                    "target_phrase": phrase,
                    "occurrence": occurrence,
                    "crop_start_seconds": round(start, 3),
                    "crop_end_seconds": round(end, 3),
                    "crop_duration_seconds": round(end - start, 3),
                }
            )
    return crops


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as audio:
        for block in iter(lambda: audio.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create timestamped YouTube command crop references without copying WAV files"
    )
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument(
        "--max-files",
        type=int,
        default=None,
        help="Process only the first N manifest rows for a timed pilot",
    )
    parser.add_argument(
        "--library-label",
        choices=("positive", "negative"),
        default=None,
        help="Restrict the pilot to one reviewed label",
    )
    args = parser.parse_args()
    if not MODEL_PATH.is_file():
        raise SystemExit(f"Cached GigaAM checkpoint is missing. Expected {MODEL_PATH}")

    torch.set_num_threads(2)
    model_started = perf_counter()
    model = gigaam.load_model("v2_rnnt", device="cpu", download_root=str(MODEL_ROOT))
    model_load_seconds = perf_counter() - model_started

    with MANIFEST.open(encoding="utf-8-sig", newline="") as source:
        items = [
            row for row in csv.DictReader(source) if row.get("CommandID") == "youtube"
        ]
    if args.library_label:
        items = [row for row in items if row.get("Label") == args.library_label]
    if args.max_files is not None:
        items = items[: max(0, args.max_files)]
    print(
        f"GigaAM loaded in {model_load_seconds:.2f}s. Preparing {len(items)} WAVs.",
        flush=True,
    )

    rows: list[dict[str, object]] = []
    inference_seconds = 0.0
    for item in items:
        label = item["Label"]
        wav_path = ROOT / "data" / "audio_library" / item["Folder"] / item["File"]
        if not wav_path.is_file():
            rows.append(
                {"file": item["File"], "label": label, "status": "source_missing"}
            )
            continue
        duration = read_audio_duration(wav_path)
        started = perf_counter()
        try:
            transcript, tokens, frame_shift = decode_with_token_frames(model, wav_path)
        except Exception as error:
            rows.append(
                {
                    "file": item["File"],
                    "label": label,
                    "status": "alignment_error",
                    "error": str(error),
                }
            )
            continue
        elapsed = perf_counter() - started
        inference_seconds += elapsed
        crops = find_phrase_crops(transcript, tokens, frame_shift, duration)
        if label == "positive" and crops:
            resolved_label = "positive"
            status = "gigaam_phrase_located"
        elif label == "negative" and not crops and transcript.strip():
            resolved_label = "negative"
            status = "gigaam_non_target"
            crops = [
                {
                    "target_phrase": "",
                    "occurrence": 1,
                    "crop_start_seconds": 0.0,
                    "crop_end_seconds": round(duration, 3),
                    "crop_duration_seconds": round(duration, 3),
                }
            ]
        else:
            resolved_label = "unresolved"
            status = "label_conflicts_with_gigaam"
            crops = []
        for crop in crops:
            rows.append(
                {
                    "file": item["File"],
                    "sha256": item["SHA256"],
                    "library_label": label,
                    "resolved_label": resolved_label,
                    "status": status,
                    "gigaam_transcript": transcript,
                    "duration_seconds": round(duration, 3),
                    "frame_resolution_ms": round(frame_shift * 1000, 2),
                    "alignment_method": "v2_rnnt_greedy_character_frames_approximate",
                    "edge_padding_seconds": EDGE_PADDING_SECONDS,
                    **crop,
                    "inference_seconds": round(elapsed, 4),
                }
            )
        print(
            f"{len(rows)} output rows processed from {wav_path.name} in {elapsed:.2f}s. Label {resolved_label}.",
            flush=True,
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "file",
        "sha256",
        "library_label",
        "resolved_label",
        "status",
        "gigaam_transcript",
        "duration_seconds",
        "frame_resolution_ms",
        "alignment_method",
        "edge_padding_seconds",
        "target_phrase",
        "occurrence",
        "crop_start_seconds",
        "crop_end_seconds",
        "crop_duration_seconds",
        "inference_seconds",
        "error",
    ]
    with args.output.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get("resolved_label", row.get("status", "unknown")))
        counts[key] = counts.get(key, 0) + 1
    summary = {
        "source_manifest": str(MANIFEST.relative_to(ROOT)),
        "model": "v2_rnnt",
        "model_load_seconds": round(model_load_seconds, 3),
        "inference_seconds_total": round(inference_seconds, 3),
        "row_count": len(rows),
        "counts": counts,
        "output_csv": str(args.output),
        "wav_files_copied": 0,
        "alignment_warning": "Character-frame times are approximate greedy decoding positions, not a forced alignment. Keep padded intervals as candidates and verify before training.",
    }
    args.output.with_suffix(".summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
