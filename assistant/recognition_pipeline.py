from __future__ import annotations

import json
import logging
import queue
import threading
import time
from datetime import datetime
from typing import Any, Callable

import numpy as np
from vosk import KaldiRecognizer, Model

from assistant.actions import execute
from assistant.archive import (
    ObservationArchive,
    PartialObservationIndex,
    SessionArchive,
)
from assistant.audio.speech_detector import AudioSegment
from assistant.commands import CommandRegistry
from assistant.diagnostics import log_diagnostic_table
from assistant.execution_history import ExecutionHistory
from assistant.feedback import play_command_tone
from assistant.recognition import evaluate_transcript
from assistant.service import EngineService
from assistant.time_utils import utc_from_monotonic
from settings import DATA_ROOT, SAMPLE_RATE, VOSK_MODEL, WORD_CONFIDENCE_MIN

log = logging.getLogger("radu.v3")


class RecognitionPipeline:
    """Consumes captured speech, decides exact commands, runs actions and records outcomes."""

    def __init__(
        self,
        *,
        stop_event: threading.Event,
        asr_queue: queue.Queue[AudioSegment],
        registry: CommandRegistry,
        archive: ObservationArchive,
        partial_archive: PartialObservationIndex,
        session_archive: SessionArchive,
        execution_history: ExecutionHistory,
        engine_service: EngineService,
        command_tone: Any,
        clock_mono: float,
        clock_utc: datetime,
        vad_metadata: Callable[[], dict[str, Any]],
    ) -> None:
        self.stop_event = stop_event
        self.asr_queue = asr_queue
        self.registry = registry
        self.archive = archive
        self.partial_archive = partial_archive
        self.session_archive = session_archive
        self.execution_history = execution_history
        self.engine_service = engine_service
        self.command_tone = command_tone
        self.clock_mono = clock_mono
        self.clock_utc = clock_utc
        self.vad_metadata = vad_metadata

    def run(self, recognizer: KaldiRecognizer, model: Model) -> None:
        grammar = self.registry.grammar
        while not self.stop_event.is_set() or not self.asr_queue.empty():
            try:
                segment = self.asr_queue.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                segment_registry = segment.command_registry or self.registry
                if grammar != segment_registry.grammar:
                    grammar = segment_registry.grammar
                    recognizer = KaldiRecognizer(model, SAMPLE_RATE, grammar)
                    recognizer.SetWords(True)
                segment.worker_started_at = time.perf_counter()
                log.info(
                    "ASR worker picked up fragment. Queue remaining %d",
                    self.asr_queue.qsize(),
                )
                self._recognize(recognizer, segment)
            except Exception as exc:
                log.exception("Recognition pipeline failed")
                self.engine_service.emit(
                    "error", message="Не удалось обработать распознанную фразу"
                )
                failed_at = time.perf_counter()
                try:
                    self.session_archive.add_observation(
                        {
                            "observation_id": None,
                            "captured_started_at_utc": utc_from_monotonic(
                                segment.started_at, self.clock_mono, self.clock_utc
                            ),
                            "captured_ended_at_utc": utc_from_monotonic(
                                segment.ended_at, self.clock_mono, self.clock_utc
                            ),
                            "transcript": "",
                            "command_id": None,
                            "accepted": False,
                            "submitted_to_asr": True,
                            "rejection_reason": f"recognition_pipeline_error_{type(exc).__name__}",
                            "timing": {
                                "capture_seconds": round(
                                    segment.ended_at - segment.started_at, 6
                                ),
                                "speech_detection_to_decision_seconds": round(
                                    failed_at - segment.started_at, 6
                                ),
                                "processing_after_capture_seconds": round(
                                    failed_at - segment.ended_at, 6
                                ),
                            },
                            "observation_json": None,
                        }
                    )
                except Exception:
                    log.exception("Could not archive recognition pipeline failure")
            finally:
                self.asr_queue.task_done()

    def _recognize(self, recognizer: KaldiRecognizer, segment: AudioSegment) -> None:
        registry = segment.command_registry or self.registry
        pipeline_start = time.perf_counter()
        if segment.worker_started_at is None:
            segment.worker_started_at = pipeline_start
        if segment.decision_detected_at is None:
            segment.decision_detected_at = pipeline_start
        log.info(
            "Recognition pipeline started. Captured %.3f sec, voiced frames %d of %d",
            len(segment.pcm) / SAMPLE_RATE,
            segment.speech_frames,
            segment.total_frames,
        )
        setup_start = time.perf_counter()
        observation_id, wav_path, metadata = self.archive.create(
            segment, self.vad_metadata()
        )
        log.info("Original fragment archived as %s", wav_path)
        asr_pcm = segment.asr_pcm if segment.asr_pcm is not None else segment.pcm
        metadata["capture"]["submitted_to_asr"] = True
        metadata["capture"]["leading_silence_trimmed_samples"] = (
            segment.leading_trim_samples
        )
        metadata["capture"]["trailing_silence_trimmed_samples"] = (
            segment.trailing_trim_samples
        )
        metadata["recognition"]["input_audio_source"] = wav_path.name
        metadata["recognition"]["input_audio_leading_trim_samples"] = (
            segment.leading_trim_samples
        )
        metadata["recognition"]["input_audio_trailing_trim_samples"] = (
            segment.trailing_trim_samples
        )
        metadata["recognition"]["input_duration_seconds"] = round(
            len(asr_pcm) / SAMPLE_RATE, 6
        )
        metadata["capture"]["started_at_utc"] = utc_from_monotonic(
            segment.started_at, self.clock_mono, self.clock_utc
        )
        metadata["capture"]["ended_at_utc"] = utc_from_monotonic(
            segment.ended_at, self.clock_mono, self.clock_utc
        )
        self.archive.write_metadata(wav_path, metadata)
        log.info(
            "Vosk input prepared from archived fragment, duration %.3f sec",
            len(asr_pcm) / SAMPLE_RATE,
        )
        recognition_setup_seconds = time.perf_counter() - setup_start
        processing_start = time.perf_counter()
        try:
            if segment.vosk_result is not None:
                result = segment.vosk_result
                asr_seconds = segment.vosk_stream_seconds
                log.info(
                    "Using Vosk result accumulated during capture in %.1f ms of recognizer work. Raw result %s",
                    asr_seconds * 1000,
                    result,
                )
            else:
                asr_start = time.perf_counter()
                if segment.transcript_ready_at is None:
                    segment.decision_detected_at = asr_start
                log.info(
                    "Sending %.3f sec audio to Vosk with %d grammar options",
                    len(asr_pcm) / SAMPLE_RATE,
                    len(registry.phrase_map),
                )
                recognizer.Reset()
                recognizer.AcceptWaveform(asr_pcm.tobytes())
                result = json.loads(recognizer.FinalResult())
                asr_seconds = time.perf_counter() - asr_start
                segment.transcript_ready_at = time.perf_counter()
                log.info(
                    "Vosk finished in %.1f ms. Raw result %s",
                    asr_seconds * 1000,
                    result,
                )
        except Exception as exc:
            metadata["recognition"] = {
                "engine": "Vosk KaldiRecognizer",
                "model_path": str(VOSK_MODEL),
                "grammar": registry.grammar,
                "status": "processing_error",
                "error": f"{type(exc).__name__}: {exc}",
            }
            metadata["decision"] = {
                "accepted": False,
                "rejection_reason": "processing_error",
            }
            metadata["timing"] = {
                "processing_seconds": round(time.perf_counter() - processing_start, 6)
            }
            self.archive.write_metadata(wav_path, metadata)
            raise

        command_checks_start = time.perf_counter()
        text = (result.get("text") or "").strip()
        words = result.get("result") or []
        decision = evaluate_transcript(
            text,
            words,
            registry,
            duration_pass=segment.asr_eligible,
            speech_detected=segment.speech_frames > 0,
        )
        # Measure from the last voiced frame to the point where the transcript
        # has been resolved against the command registry and its checks.
        segment.decision_detected_at = time.perf_counter()
        confidences = decision.confidences
        command = decision.command
        checks = decision.checks
        rejection_reason = decision.rejection_reason
        log.info(
            "Word confidence check %s. Values %s; required minimum %.3f",
            "PASS" if decision.confidence_pass else "FAIL",
            [round(value, 4) for value in confidences] if confidences else "missing",
            WORD_CONFIDENCE_MIN,
        )
        command_name = command["id"] if command else None
        log.info(
            "Exact full-phrase match %s. Transcript %r, normalized %r, command %s",
            "FOUND" if command else "NOT FOUND",
            text,
            decision.normalized_text,
            command_name or "none",
        )
        action_result = "not_run"
        action_seconds = 0.0
        action_started_at: float | None = None

        rejection_messages = {
            "explicit_negation": "Transcript starts with explicit negation and is never executable",
            "empty_transcript": "Vosk returned no transcript",
            "transcript_not_an_exact_configured_phrase": "No exact configured phrase matched",
            "word_confidence_below_threshold_or_missing": "Word confidence is below threshold or missing",
        }
        if not decision.can_execute:
            log.warning(
                "REJECTED. %s",
                rejection_messages.get(rejection_reason, rejection_reason),
            )
        else:
            action_start: float | None = None
            try:
                log.info(
                    "All recognition checks passed. Executing action %s",
                    command["action"],
                )
                try:
                    if self.engine_service.enabled:
                        self.engine_service.begin_action()
                    else:
                        play_command_tone(self.command_tone)
                    log.info("Command feedback completed")
                except Exception:
                    log.exception("Could not play command feedback tone")
                action_start = time.perf_counter()
                action_started_at = action_start
                browser_executable = next(
                    (
                        configured["action"]["value"]
                        for configured in registry.commands
                        if configured.get("id") == "yandex_browser"
                        and configured.get("action", {}).get("type") == "open_app"
                    ),
                    None,
                )
                execute(command["action"], browser_executable=browser_executable)
                action_seconds = time.perf_counter() - action_start
                action_result = "executed"
                checks["action_execution"] = True
                log.info(
                    "COMMAND EXECUTED successfully. Id %s, phrase %r",
                    command_name,
                    text,
                )
                try:
                    self.execution_history.record_success(
                        session_id=self.session_archive.session_id,
                        observation_id=observation_id,
                        command_id=command_name,
                        transcript=text,
                        action=command["action"],
                        word_confidence_min=min(confidences) if confidences else None,
                        elapsed_seconds=time.perf_counter() - segment.started_at,
                        observation_wav=str(wav_path.relative_to(DATA_ROOT)),
                        observation_json=str(
                            wav_path.with_suffix(".json").relative_to(DATA_ROOT)
                        ),
                    )
                    log.info(
                        "Successful command added to execution history %s", command_name
                    )
                except Exception:
                    log.exception(
                        "Could not write successful command to execution history"
                    )
            except Exception as exc:
                if action_start is not None:
                    action_seconds = time.perf_counter() - action_start
                action_result = f"failed: {type(exc).__name__}: {exc}"
                rejection_reason = "action_execution_failed"
                checks["action_execution"] = False
                log.exception("Command action failed")
        command_checks_seconds = (
            time.perf_counter() - command_checks_start - action_seconds
        )
        completed_at = time.perf_counter()
        metadata["recognition"] = {
            "engine": "Vosk KaldiRecognizer",
            "model_path": str(VOSK_MODEL),
            "grammar": registry.grammar,
            "raw_text": text,
            "normalized_text": decision.normalized_text,
            "words": words,
            "word_confidence_min": min(confidences) if confidences else None,
            "word_confidence_mean": float(np.mean(confidences))
            if confidences
            else None,
            "required_word_confidence_min": WORD_CONFIDENCE_MIN,
            "confidence_pass": decision.confidence_pass,
        }
        metadata["decision"] = {
            "command_id": command_name,
            "exact_phrase_match": command is not None,
            "accepted": action_result == "executed",
            "checks": checks,
            "partial_pass": bool(
                any(value is True for value in checks.values())
                and any(value is False for value in checks.values())
            ),
            "rejection_reason": rejection_reason,
            "action": command["action"] if command else None,
            "action_result": action_result,
        }
        metadata["timing"] = {
            "capture_seconds": round(segment.ended_at - segment.started_at, 6),
            **{
                key: round(value, 6) if value is not None else None
                for key, value in segment.latency_breakdown().items()
            },
            "recognition_setup_seconds": round(recognition_setup_seconds, 6),
            "asr_seconds": round(asr_seconds, 6),
            "command_checks_seconds": round(max(0.0, command_checks_seconds), 6),
            "action_seconds": round(action_seconds, 6),
            "processing_after_capture_seconds": round(
                completed_at - segment.ended_at, 6
            ),
            "capture_end_to_action_seconds": round(
                action_started_at - segment.ended_at, 6
            )
            if action_started_at is not None
            else None,
            "speech_detection_to_action_seconds": round(
                action_started_at - segment.started_at, 6
            )
            if action_started_at is not None
            else None,
            "speech_detection_to_action_completed_seconds": round(
                completed_at - segment.started_at, 6
            ),
            "speech_detection_to_decision_seconds": round(
                completed_at - segment.started_at, 6
            ),
            "early_match": segment.early_match_at is not None,
            "early_match_text": segment.early_match_text,
            "early_match_seconds": round(segment.early_match_at - segment.started_at, 6)
            if segment.early_match_at is not None
            else None,
        }
        metadata["session_id"] = self.session_archive.session_id
        metadata_write_start = time.perf_counter()
        json_path = self.archive.write_metadata(wav_path, metadata)
        confidence_candidate = self.archive.archive_confidence_rejection(
            wav_path, json_path, metadata
        )
        if confidence_candidate is not None:
            log.info("Confidence-only rejection copied to %s", confidence_candidate)
        if self.partial_archive.add(wav_path, json_path, metadata):
            log.info(
                "PARTIAL CANDIDATE archived in the partial-observation index. Passed %s. Failed %s",
                [key for key, value in checks.items() if value is True],
                [key for key, value in checks.items() if value is False],
            )
        result_metadata_write_seconds = time.perf_counter() - metadata_write_start
        total_seconds = completed_at - segment.started_at
        log.info(
            "ASR %r, word confidence %s, result %s, %.3f sec, archive %s",
            text,
            f"{metadata['recognition']['word_confidence_min']:.3f}"
            if confidences
            else "missing",
            action_result
            if action_result != "not_run"
            else f"rejected {rejection_reason}",
            asr_seconds,
            json_path.name,
        )
        log.info(
            "Latency from speech detection to decision %.1f ms; Vosk %.1f ms",
            total_seconds * 1000,
            asr_seconds * 1000,
        )
        log.info(
            "TIMING BREAKDOWN  capture %.1f ms | voice tail %.1f ms | "
            "last voiced frame to decision %.1f ms | "
            "audio end to decision %.1f ms | "
            "decision to text %.1f ms | text to queue %.1f ms | ASR queue %.1f ms | "
            "archive and input %.1f ms | "
            "Vosk %.1f ms | command checks %.1f ms | action %.1f ms | result JSON %.1f ms",
            metadata["timing"]["capture_seconds"] * 1000,
            (metadata["timing"]["last_voiced_frame_to_capture_end_seconds"] or 0.0)
            * 1000,
            (metadata["timing"]["last_voiced_frame_to_decision_seconds"] or 0.0) * 1000,
            (metadata["timing"]["capture_end_to_decision_seconds"] or 0.0) * 1000,
            (metadata["timing"]["decision_to_transcript_seconds"] or 0.0) * 1000,
            (metadata["timing"]["transcript_to_queue_seconds"] or 0.0) * 1000,
            (metadata["timing"]["asr_queue_wait_seconds"] or 0.0) * 1000,
            recognition_setup_seconds * 1000,
            asr_seconds * 1000,
            metadata["timing"]["command_checks_seconds"] * 1000,
            action_seconds * 1000,
            result_metadata_write_seconds * 1000,
        )
        log.info(
            "Pipeline complete in %.1f ms. Observation %s. Decision %s",
            (time.perf_counter() - pipeline_start) * 1000,
            observation_id,
            "ACCEPTED"
            if action_result == "executed"
            else f"REJECTED ({rejection_reason})",
        )
        capture_seconds = segment.ended_at - segment.started_at
        post_capture_seconds = completed_at - segment.ended_at
        log_diagnostic_table(
            text,
            checks,
            action_result,
            rejection_reason,
            total_seconds,
            capture_seconds,
            post_capture_seconds,
            metadata["timing"],
            words,
        )
        self.session_archive.add_observation(
            {
                "observation_id": observation_id,
                "captured_started_at_utc": metadata["capture"].get("started_at_utc"),
                "captured_ended_at_utc": metadata["capture"].get("ended_at_utc"),
                "transcript": text,
                "command_id": command_name,
                "accepted": action_result == "executed",
                "partial_pass": metadata["decision"]["partial_pass"],
                "checks": checks,
                "submitted_to_asr": True,
                "rejection_reason": rejection_reason,
                "timing": {
                    **metadata["timing"],
                    "result_metadata_write_seconds": round(
                        result_metadata_write_seconds, 6
                    ),
                },
                "observation_audio": wav_path.name,
                "asr_audio": None,
                "observation_json": json_path.name,
            }
        )
        self.engine_service.emit(
            "command_result",
            accepted=action_result == "executed",
            transcript=text,
            phrase=(
                command["phrases"][0] if command and command.get("phrases") else text
            ),
            reason=rejection_reason or "",
            recognition_seconds=metadata["timing"].get(
                "last_voiced_frame_to_decision_seconds"
            ),
        )
