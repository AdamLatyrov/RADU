from __future__ import annotations

import argparse
import csv
import json
import sys
import wave
from pathlib import Path

from vosk import KaldiRecognizer, Model, SetLogLevel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from assistant.commands import CommandRegistry, normalize  # noqa: E402

DEFAULT_MANIFEST = (
    ROOT / "data" / "audio_library" / "executed_commands" / "dataset_manifest.csv"
)
DEFAULT_MODEL = ROOT / "model" / "vosk-model-small-ru-0.22"
DEFAULT_OUTPUT = (
    ROOT / "data" / "review" / "negation_stream_replay_20261014" / "results.json"
)
FRAME_SAMPLES = 320


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay explicit-negation WAVs through RADU's current streaming Vosk grammar"
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    rows = list(
        csv.DictReader(args.manifest.open("r", encoding="utf-8-sig", newline=""))
    )
    rows = [row for row in rows if row.get("gigaam_text", "").lower().startswith("не ")]
    if not rows:
        raise RuntimeError("No direct-negation examples found in the manifest")

    registry = CommandRegistry(ROOT / "commands.json")
    SetLogLevel(-1)
    model = Model(str(args.model))
    results: list[dict[str, object]] = []

    for row in rows:
        observation_id = row["observation_id"]
        wav_path = (
            ROOT
            / "data"
            / "observations"
            / observation_id[:4]
            / observation_id[4:6]
            / observation_id[6:8]
            / f"{observation_id}.wav"
        )
        metadata = json.loads(wav_path.with_suffix(".json").read_text(encoding="utf-8"))
        capture = metadata.get("capture", {})
        archived_grammar = metadata.get("recognition", {}).get("grammar", "")

        with wave.open(str(wav_path), "rb") as source:
            if source.getnchannels() != 1 or source.getsampwidth() != 2:
                raise ValueError(
                    f"Expected mono PCM16 WAV, got {source.getparams()} for {observation_id}"
                )
            sample_rate = source.getframerate()
            audio = source.readframes(source.getnframes())

        first = int(capture.get("leading_silence_trimmed_samples", 0)) * 2
        last = len(audio) - int(capture.get("trailing_silence_trimmed_samples", 0)) * 2
        audio = audio[first:last]
        recognizer = KaldiRecognizer(model, sample_rate, registry.grammar)
        finalized: list[str] = []
        partials: list[str] = []
        early_match: str | None = None

        for offset in range(0, len(audio) - FRAME_SAMPLES * 2 + 1, FRAME_SAMPLES * 2):
            frame = audio[offset : offset + FRAME_SAMPLES * 2]
            if recognizer.AcceptWaveform(frame):
                completed = json.loads(recognizer.Result())
                if completed.get("text"):
                    finalized.append(str(completed["text"]))

            partial = str(
                json.loads(recognizer.PartialResult()).get("partial", "")
            ).strip()
            merged = " ".join([*finalized, partial]).strip()
            if merged and (not partials or partials[-1] != merged):
                partials.append(merged)

            command = registry.resolve(merged)
            if command is not None and not normalize(merged).startswith("не "):
                early_match = merged
                break

        final_result = json.loads(recognizer.FinalResult())
        final_text = " ".join(
            [*finalized, str(final_result.get("text", "")).strip()]
        ).strip()
        results.append(
            {
                "observation_id": observation_id,
                "command_id": row.get("command_id", ""),
                "gigaam_text": row.get("gigaam_text", ""),
                "archived_vosk_text": row.get("vosk_text", ""),
                "archived_grammar_had_explicit_negation": "не открой"
                in archived_grammar,
                "current_grammar_had_explicit_negation": bool(
                    registry.non_action_phrases
                ),
                "partial_sequence": partials,
                "early_positive_match": early_match,
                "current_stream_final": final_text,
                "wav": str(wav_path),
            }
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {"count": len(results), "results": results}, ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    print(f"Replayed {len(results)} negation recordings to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
