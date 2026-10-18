from __future__ import annotations

import json
import logging
import shutil
import statistics
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from assistant.audio.speech_detector import AudioSegment
from settings import DATA_ROOT, OBSERVATIONS_ROOT, SAMPLE_RATE, SESSIONS_ROOT

log = logging.getLogger("radu.v3.archive")


class ObservationArchive:
    def __init__(self, root: Path = OBSERVATIONS_ROOT) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def create(
        self, segment: AudioSegment, vad_config: dict[str, Any]
    ) -> tuple[str, Path, dict[str, Any]]:
        now = datetime.now(timezone.utc)
        observation_id = f"{now.strftime('%Y%m%dT%H%M%S_%fZ')}_{uuid.uuid4().hex[:8]}"
        folder = (
            self.root / now.strftime("%Y") / now.strftime("%m") / now.strftime("%d")
        )
        folder.mkdir(parents=True, exist_ok=True)
        wav_path = folder / f"{observation_id}.wav"
        sf.write(wav_path, segment.pcm, SAMPLE_RATE, subtype="PCM_16", format="WAV")
        metadata: dict[str, Any] = {
            "schema_version": 1,
            "observation_id": observation_id,
            "created_at_utc": now.isoformat(),
            "audio": {
                "path": wav_path.name,
                "format": "WAV PCM_16",
                "sample_rate_hz": SAMPLE_RATE,
                "channels": 1,
                "sample_count": int(len(segment.pcm)),
                "duration_seconds": round(len(segment.pcm) / SAMPLE_RATE, 6),
                "peak_abs_pcm": int(np.max(np.abs(segment.pcm.astype(np.int32))))
                if len(segment.pcm)
                else 0,
                "rms_pcm": float(np.sqrt(np.mean(segment.pcm.astype(np.float64) ** 2)))
                if len(segment.pcm)
                else 0.0,
            },
            "capture": {
                "started_monotonic_seconds": segment.started_at,
                "ended_monotonic_seconds": segment.ended_at,
                "duration_seconds": round(segment.ended_at - segment.started_at, 6),
                "vad_speech_frames": segment.speech_frames,
                "vad_total_frames": segment.total_frames,
                "vad_end_reason": segment.end_reason,
                "submitted_to_asr": False,
            },
            "vad": vad_config,
            "recognition": {},
            "decision": {},
            "timing": {},
        }
        return observation_id, wav_path, metadata

    @staticmethod
    def write_metadata(wav_path: Path, metadata: dict[str, Any]) -> Path:
        path = wav_path.with_suffix(".json")
        path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return path

    def archive_confidence_rejection(
        self, wav_path: Path, json_path: Path, metadata: dict[str, Any]
    ) -> Path | None:
        """Copy exact-command observations rejected only by word confidence."""
        checks = metadata.get("decision", {}).get("checks", {})
        if checks.get("word_confidence") is not False:
            return None
        if any(
            result is not True
            for name, result in checks.items()
            if name not in {"word_confidence", "action_execution"}
        ):
            return None

        created_at = datetime.fromisoformat(
            metadata["created_at_utc"].replace("Z", "+00:00")
        )
        destination_dir = (
            DATA_ROOT
            / "confidence_rejections"
            / created_at.strftime("%Y")
            / created_at.strftime("%m")
            / created_at.strftime("%d")
        )
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination_wav = destination_dir / wav_path.name
        destination_json = destination_wav.with_suffix(".json")
        if destination_wav.exists() and destination_json.exists():
            return destination_wav
        shutil.copy2(wav_path, destination_wav)

        copied_metadata = json.loads(json_path.read_text(encoding="utf-8"))
        copied_metadata["confidence_review"] = {
            "reason": "all_checks_passed_except_word_confidence",
            "minimum_word_confidence": copied_metadata.get("recognition", {}).get(
                "word_confidence_min"
            ),
            "required_minimum": copied_metadata.get("recognition", {}).get(
                "required_word_confidence_min"
            ),
            "original_wav": str(wav_path.resolve()),
            "original_metadata": str(json_path.resolve()),
        }
        destination_json.write_text(
            json.dumps(copied_metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        index_path = DATA_ROOT / "confidence_rejections" / "index.jsonl"
        entry = {
            "observation_id": metadata.get("observation_id"),
            "created_at_utc": metadata.get("created_at_utc"),
            "command_id": metadata.get("decision", {}).get("command_id"),
            "transcript": metadata.get("recognition", {}).get("raw_text", ""),
            "words": metadata.get("recognition", {}).get("words", []),
            "minimum_word_confidence": metadata.get("recognition", {}).get(
                "word_confidence_min"
            ),
            "required_minimum": metadata.get("recognition", {}).get(
                "required_word_confidence_min"
            ),
            "audio_path": str(destination_wav.resolve()),
            "metadata_path": str(destination_json.resolve()),
        }
        with index_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return destination_wav


class PartialObservationIndex:
    """Index saved recordings that passed at least one gate and failed another."""

    def __init__(self, root: Path = DATA_ROOT / "partial_observations") -> None:
        self.root = root
        self.path = root / "index.jsonl"
        self.root.mkdir(parents=True, exist_ok=True)

    def add(self, wav_path: Path, json_path: Path, metadata: dict[str, Any]) -> bool:
        checks = metadata.get("decision", {}).get("checks", {})
        passed = [name for name, result in checks.items() if result is True]
        failed = [name for name, result in checks.items() if result is False]
        if not passed or not failed:
            return False

        entry = {
            "observation_id": metadata.get("observation_id"),
            "created_at_utc": metadata.get("created_at_utc"),
            "transcript": metadata.get("recognition", {}).get("raw_text", ""),
            "command_id": metadata.get("decision", {}).get("command_id"),
            "checks": checks,
            "passed_checks": passed,
            "failed_checks": failed,
            "audio_path": str(wav_path.resolve()),
            "metadata_path": str(json_path.resolve()),
        }
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return True


class SessionArchive:
    """Persist a compact, incrementally updated summary for one RADU run."""

    CHECKPOINT_OBSERVATIONS = 25
    CHECKPOINT_SECONDS = 30.0

    TIMING_FIELDS = (
        "capture_seconds",
        "last_voiced_frame_to_capture_end_seconds",
        "capture_end_to_decision_seconds",
        "last_voiced_frame_to_decision_seconds",
        "last_voiced_frame_to_transcript_seconds",
        "last_voiced_frame_to_queue_seconds",
        "last_voiced_frame_to_worker_start_seconds",
        "decision_to_transcript_seconds",
        "transcript_to_queue_seconds",
        "asr_queue_wait_seconds",
        "capture_end_to_worker_start_seconds",
        "recognition_setup_seconds",
        "asr_seconds",
        "command_checks_seconds",
        "action_seconds",
        "processing_after_capture_seconds",
        "speech_detection_to_action_seconds",
        "speech_detection_to_action_completed_seconds",
        "speech_detection_to_decision_seconds",
        "result_metadata_write_seconds",
    )

    def __init__(self, root: Path = SESSIONS_ROOT) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._recover_interrupted_sessions()
        self.started_at = datetime.now(timezone.utc)
        self.started_perf = time.perf_counter()
        self.session_id = (
            f"{self.started_at.strftime('%Y%m%dT%H%M%S_%fZ')}_{uuid.uuid4().hex[:8]}"
        )
        self.path = (
            self.root
            / self.started_at.strftime("%Y")
            / self.started_at.strftime("%m")
            / self.started_at.strftime("%d")
            / f"{self.session_id}.json"
        )
        self.journal_path = self.path.with_suffix(".jsonl")
        self.startup_stages: dict[str, float] = {}
        self.configuration: dict[str, Any] = {}
        self.observations: list[dict[str, Any]] = []
        self.counts = {
            "captured_fragments": 0,
            "sent_to_asr": 0,
            "accepted": 0,
            "rejected": 0,
            "partial_pass": 0,
        }
        self._last_checkpoint_count = 0
        self._last_checkpoint_at = time.monotonic()
        self.status = "running"
        self.ended_at: datetime | None = None
        self._lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.journal_path.touch()
        self._write()

    def add_startup_stage(self, stage: str, elapsed_seconds: float) -> None:
        with self._lock:
            self.startup_stages[stage] = round(max(0.0, elapsed_seconds), 6)
            self._write_locked()

    def add_configuration(self, values: dict[str, Any]) -> None:
        with self._lock:
            self.configuration.update(values)
            self._write_locked()

    def add_observation(self, observation: dict[str, Any]) -> None:
        with self._lock:
            with self.journal_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(
                    json.dumps(observation, ensure_ascii=False, separators=(",", ":"))
                    + "\n"
                )
            self.observations.append(observation)
            self.counts["captured_fragments"] += 1
            self.counts["sent_to_asr"] += bool(observation.get("submitted_to_asr"))
            self.counts["accepted"] += bool(observation.get("accepted"))
            self.counts["rejected"] += not bool(observation.get("accepted"))
            self.counts["partial_pass"] += bool(observation.get("partial_pass"))
            elapsed = time.monotonic() - self._last_checkpoint_at
            pending = len(self.observations) - self._last_checkpoint_count
            if (
                pending >= self.CHECKPOINT_OBSERVATIONS
                or elapsed >= self.CHECKPOINT_SECONDS
            ):
                self._write_locked()

    def finish(self, status: str = "stopped") -> Path:
        with self._lock:
            self.status = status
            self.ended_at = datetime.now(timezone.utc)
            self._write_locked(final=True)
            try:
                self.journal_path.unlink()
            except FileNotFoundError:
                pass
        return self.path

    def _write(self) -> None:
        with self._lock:
            self._write_locked()

    def _write_locked(self, *, final: bool = False) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        summary = {
            "schema_version": 1,
            "session_id": self.session_id,
            "status": self.status,
            "started_at_utc": self.started_at.isoformat(),
            "ended_at_utc": self.ended_at.isoformat() if self.ended_at else None,
            "elapsed_seconds": round(
                (self.ended_at - self.started_at).total_seconds()
                if self.ended_at
                else time.perf_counter() - self.started_perf,
                6,
            ),
            "configuration": self.configuration,
            "startup_stages_seconds": self.startup_stages,
            "counts": self.counts.copy(),
            "timing_summary_seconds": self._timing_summaries() if final else None,
            "observations": self.observations if final else None,
            "observation_journal": None if final else self.journal_path.name,
        }
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.path)
        self._last_checkpoint_count = len(self.observations)
        self._last_checkpoint_at = time.monotonic()

    def _timing_summaries(self) -> dict[str, dict[str, Any]]:
        accepted = [item for item in self.observations if item.get("accepted")]
        rejected = [item for item in self.observations if not item.get("accepted")]
        return {
            "all": self._timing_summary(self.observations),
            "accepted": self._timing_summary(accepted),
            "rejected": self._timing_summary(rejected),
        }

    def _recover_interrupted_sessions(self) -> None:
        for journal_path in self.root.rglob("*.jsonl"):
            session_path = journal_path.with_suffix(".json")
            try:
                summary = json.loads(session_path.read_text(encoding="utf-8"))
                if summary.get("status") not in {"running", "worker_still_finishing"}:
                    journal_path.unlink(missing_ok=True)
                    continue
                observations = []
                with journal_path.open(encoding="utf-8") as stream:
                    for line in stream:
                        try:
                            observations.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue

                ended_at = datetime.fromtimestamp(
                    journal_path.stat().st_mtime, timezone.utc
                )
                started_at = datetime.fromisoformat(summary["started_at_utc"])
                if ended_at < started_at:
                    ended_at = started_at
                accepted = [item for item in observations if item.get("accepted")]
                rejected = [item for item in observations if not item.get("accepted")]
                counts = {
                    "captured_fragments": len(observations),
                    "sent_to_asr": sum(
                        bool(item.get("submitted_to_asr")) for item in observations
                    ),
                    "accepted": len(accepted),
                    "rejected": len(rejected),
                    "partial_pass": sum(
                        bool(item.get("partial_pass")) for item in observations
                    ),
                }
                summary.update(
                    status="interrupted",
                    ended_at_utc=ended_at.isoformat(),
                    elapsed_seconds=round((ended_at - started_at).total_seconds(), 6),
                    counts=counts,
                    timing_summary_seconds={
                        "all": self._timing_summary(observations),
                        "accepted": self._timing_summary(accepted),
                        "rejected": self._timing_summary(rejected),
                    },
                    observations=observations,
                    observation_journal=None,
                )
                temporary = session_path.with_suffix(".json.tmp")
                temporary.write_text(
                    json.dumps(summary, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                temporary.replace(session_path)
                journal_path.unlink(missing_ok=True)
            except (OSError, ValueError, KeyError, TypeError):
                log.exception(
                    "Could not recover interrupted session journal %s", journal_path
                )
                continue

    @classmethod
    def _timing_summary(cls, observations: list[dict[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for field in cls.TIMING_FIELDS:
            values = [
                float(item["timing"][field])
                for item in observations
                if isinstance(item.get("timing"), dict)
                and item["timing"].get(field) is not None
            ]
            if values:
                result[field] = {
                    "mean": round(statistics.fmean(values), 6),
                    "median": round(statistics.median(values), 6),
                    "min": round(min(values), 6),
                    "max": round(max(values), 6),
                    "count": len(values),
                }
        return result
