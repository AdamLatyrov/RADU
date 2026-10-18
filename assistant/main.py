from __future__ import annotations

import hashlib
import json
import logging
import logging.handlers
import os
import queue
import statistics
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import sounddevice as sd
import webrtcvad
from colorama import init as colorama_init
from vosk import KaldiRecognizer, Model, SetLogLevel

from assistant.archive import (
    ObservationArchive,
    PartialObservationIndex,
    SessionArchive,
)
from assistant.audio.ring_buffer import RingBuffer
from assistant.audio.silence import trim_silence_vad
from assistant.audio.speech_detector import AudioSegment, SpeechDetector
from assistant.commands import CommandRegistry, normalize
from assistant.execution_history import ExecutionHistory
from assistant.feedback import create_command_tone
from assistant.service import EngineService
from assistant.recognition_pipeline import RecognitionPipeline
from assistant.time_utils import utc_from_monotonic
from settings import (
    ASR_QUEUE_MAXSIZE,
    COMMAND_HISTORY_FILE,
    COMMANDS_FILE,
    DATA_ROOT,
    END_SILENCE_FRAMES,
    FRAME_MS,
    FRAME_SAMPLES,
    MAX_SPEECH_SECONDS,
    MAX_UTTERANCE_SECONDS,
    PRE_SAMPLES,
    QUEUE_MAXSIZE,
    RING_SECONDS,
    SAMPLE_RATE,
    START_SPEECH_FRAMES,
    VAD_MODE,
    VOSK_MODEL,
    WORD_CONFIDENCE_MIN,
)

ENGINE_SERVICE = (
    os.environ.get("RADU_ENGINE_SERVICE") == "1" or "--engine-service" in sys.argv
)
if ENGINE_SERVICE:
    os.environ["RADU_ENGINE_SERVICE"] = "1"


def _configure_logging() -> Path:
    log_dir = DATA_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "radu-v3.log"
    formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03d %(levelname)-8s %(threadName)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console = logging.StreamHandler(sys.stderr if ENGINE_SERVICE else sys.stdout)
    console.setFormatter(formatter)
    file_handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logging.basicConfig(
        level=logging.INFO, handlers=[console, file_handler], force=True
    )
    return log_path


LOG_PATH = _configure_logging()
log = logging.getLogger("radu.v3")
SetLogLevel(-1)
colorama_init(autoreset=True)


def _runtime_fingerprint() -> str:
    digest = hashlib.sha256()
    source_root = Path(__file__).resolve().parent
    project_root = source_root.parent
    sources = sorted(source_root.rglob("*.py"))
    sources.extend(
        (
            project_root / "settings.py",
            project_root / "requirements.txt",
            COMMANDS_FILE,
        )
    )
    for path in sources:
        try:
            source_name = path.relative_to(project_root).as_posix()
        except ValueError:
            source_name = f"external_config/{path.name}"
        digest.update(source_name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


class VoiceAssistant:
    def __init__(self) -> None:
        self.registry = CommandRegistry(COMMANDS_FILE)
        self.stop_event = threading.Event()
        self.listening_event = threading.Event()
        self.listening_event.set()
        self.reload_event = threading.Event()
        self.engine_service = EngineService(
            self.stop_event, self.listening_event, self.reload_event, ENGINE_SERVICE
        )
        self.audio_queue: queue.Queue[tuple[np.ndarray, float]] = queue.Queue(
            maxsize=QUEUE_MAXSIZE
        )
        self.asr_queue: queue.Queue[AudioSegment] = queue.Queue(
            maxsize=ASR_QUEUE_MAXSIZE
        )
        self.ring = RingBuffer(RING_SECONDS * SAMPLE_RATE)
        self.vad = webrtcvad.Vad(VAD_MODE)
        self.detector = SpeechDetector(
            self.ring,
            start_frames=START_SPEECH_FRAMES,
            end_silence_frames=END_SILENCE_FRAMES,
            pre_samples=PRE_SAMPLES,
            max_frames=MAX_UTTERANCE_SECONDS * 1000 // FRAME_MS,
        )
        self.archive = ObservationArchive()
        self.partial_archive = PartialObservationIndex()
        self.session_archive = SessionArchive()
        self.execution_history = ExecutionHistory(COMMAND_HISTORY_FILE)
        self.session_finished = False
        self.live_recognizer: KaldiRecognizer | None = None
        self.live_recognition_seconds = 0.0
        self.live_recognition_parts: list[dict[str, object]] = []
        self.command_tone = create_command_tone()
        self.session_archive.add_configuration(
            {
                "runtime": "RADU V3",
                "asr_engine": "Vosk",
                "model_path": str(VOSK_MODEL),
                "sample_rate_hz": SAMPLE_RATE,
                "frame_ms": FRAME_MS,
                "vad_mode": VAD_MODE,
                "end_silence_ms": END_SILENCE_FRAMES * FRAME_MS,
                "max_speech_seconds": MAX_SPEECH_SECONDS,
                "minimum_word_confidence": WORD_CONFIDENCE_MIN,
                "command_ids": [item["id"] for item in self.registry.commands],
                "grammar_phrase_count": len(self.registry.phrase_map),
            }
        )
        self.session_archive.add_configuration(
            {
                "source_fingerprint_sha256": _runtime_fingerprint(),
                "acoustic_reference_gate": "disabled",
                "early_exact_partial_dispatch": True,
                "early_partial_requires_final_validation": True,
            }
        )
        log.info("STEP 1 of 5  Acoustic reference check is disabled")
        self.clock_mono = time.perf_counter()
        self.clock_utc = datetime.now(timezone.utc)

        self.recognition_pipeline = RecognitionPipeline(
            stop_event=self.stop_event,
            asr_queue=self.asr_queue,
            registry=self.registry,
            archive=self.archive,
            partial_archive=self.partial_archive,
            session_archive=self.session_archive,
            execution_history=self.execution_history,
            engine_service=self.engine_service,
            command_tone=self.command_tone,
            clock_mono=self.clock_mono,
            clock_utc=self.clock_utc,
            vad_metadata=self._vad_metadata,
        )

    def run(self) -> None:
        log.info("STEP 2 of 5  Loading Vosk model from %s", VOSK_MODEL)
        if not VOSK_MODEL.is_dir():
            raise FileNotFoundError(
                f"Vosk model folder is missing. Expected {VOSK_MODEL}. "
                "Install dependencies, then put the Russian model in this folder."
            )
        load_start = time.perf_counter()
        model = Model(str(VOSK_MODEL))
        recognizer = KaldiRecognizer(model, SAMPLE_RATE, self.registry.grammar)
        recognizer.SetWords(True)
        stream_recognizer = KaldiRecognizer(model, SAMPLE_RATE, self.registry.grammar)
        stream_recognizer.SetWords(True)
        self.live_recognizer = stream_recognizer
        self.session_archive.add_startup_stage(
            "vosk_model_and_grammar_load", time.perf_counter() - load_start
        )
        log.info(
            "STEP 2 of 5 COMPLETE  Vosk model and grammar ready in %.3f sec",
            time.perf_counter() - load_start,
        )
        log.info(
            "Capture settings  VAD mode %d, frames %d ms, end pause %d ms, speech limit %.1f sec, word confidence %.1f",
            VAD_MODE,
            FRAME_MS,
            END_SILENCE_FRAMES * FRAME_MS,
            MAX_SPEECH_SECONDS,
            WORD_CONFIDENCE_MIN,
        )
        log.info("All configured commands are eligible without acoustic references")
        worker = threading.Thread(
            target=self.recognition_pipeline.run, args=(recognizer, model)
        )
        worker.start()
        self.engine_service.start()
        log.info("Recognition worker started")
        session_status = "stopped"
        try:
            device_info = sd.query_devices(kind="input")
            self.session_archive.add_configuration(
                {
                    "input_device": device_info.get("name", "default input"),
                    "input_channels": device_info.get("max_input_channels", 0),
                }
            )
            log.info(
                "STEP 3 of 5  Opening microphone %r with %d input channels at %d Hz",
                device_info.get("name", "default input"),
                device_info.get("max_input_channels", 0),
                SAMPLE_RATE,
            )
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="int16",
                blocksize=FRAME_SAMPLES,
                callback=self._audio_callback,
            ):
                self.engine_service.emit("ready", commands=len(self.registry.commands))
                self.engine_service.emit("microphone", value=True)
                log.info(
                    "STEP 3 of 5 COMPLETE  Microphone stream active with %d-sample frames",
                    FRAME_SAMPLES,
                )
                log.info(
                    "STEP 4 of 5  Listening continuously. Speak one configured command. Press Ctrl+C to stop."
                )
                heartbeat_at = time.monotonic()
                progress_at = heartbeat_at
                window_peak = 0
                window_speech_frames = 0
                window_rms_sum = 0.0
                window_frame_count = 0
                capture_was_listening = True
                next_audio_level_at = 0.0
                while not self.stop_event.is_set():
                    if self.reload_event.is_set() and not self.detector.recording:
                        self.reload_event.clear()
                        self.registry = CommandRegistry(COMMANDS_FILE)
                        self.recognition_pipeline.registry = self.registry
                        self.live_recognizer = KaldiRecognizer(
                            model, SAMPLE_RATE, self.registry.grammar
                        )
                        self.live_recognizer.SetWords(True)
                        self.engine_service.emit(
                            "config_reload", value=len(self.registry.commands)
                        )
                    if not self.listening_event.is_set() and capture_was_listening:
                        self.detector.reset()
                        self.ring.clear()
                        capture_was_listening = False
                    try:
                        frame, timestamp = self.audio_queue.get(timeout=0.25)
                    except queue.Empty:
                        continue
                    if not self.listening_event.is_set():
                        if capture_was_listening:
                            self.detector.reset()
                            self.ring.clear()
                            capture_was_listening = False
                        continue
                    capture_was_listening = True
                    is_speech = self.vad.is_speech(frame.tobytes(), SAMPLE_RATE)
                    frame_peak = max(int(frame.max()), -int(frame.min()))
                    frame_rms = float(
                        np.sqrt(np.mean(np.square(frame, dtype=np.float32)))
                    )
                    window_peak = max(window_peak, frame_peak)
                    window_speech_frames += int(is_speech)
                    window_rms_sum += frame_rms
                    window_frame_count += 1
                    now = time.monotonic()
                    if now >= next_audio_level_at:
                        self.engine_service.emit(
                            "audio_level", value=min(1.0, frame_rms / 1800.0)
                        )
                        next_audio_level_at = now + 1 / 30
                    was_recording = self.detector.recording
                    segment = self.detector.update(frame, is_speech, timestamp)
                    if self.detector.recording and not was_recording:
                        self.live_recognizer.Reset()
                        self.live_recognition_seconds = 0.0
                        self.live_recognition_parts = []
                        prefix = self.ring.last(PRE_SAMPLES)
                        captured_onset = np.concatenate(self.detector.frames).astype(
                            np.int16, copy=False
                        )
                        onset_audio = captured_onset[len(prefix) :]
                        if len(onset_audio):
                            self._feed_live_audio(onset_audio)
                        log.info(
                            "VAD START  Speech detected after %d positive frames, about %d ms. Excluded %d ms pre-roll from Vosk stream. End silence %d ms. Max speech %.2f sec",
                            START_SPEECH_FRAMES,
                            START_SPEECH_FRAMES * FRAME_MS,
                            PRE_SAMPLES * 1000 // SAMPLE_RATE,
                            END_SILENCE_FRAMES * FRAME_MS,
                            MAX_SPEECH_SECONDS,
                        )
                        progress_at = time.monotonic()
                    elif was_recording and self.live_recognizer is not None:
                        self._feed_live_audio(frame)
                        early_segment = self._finish_on_exact_partial(timestamp)
                        if early_segment is not None:
                            self._submit(early_segment)
                    if segment is not None:
                        segment.decision_detected_at = time.perf_counter()
                        if segment.asr_eligible and self.live_recognizer is not None:
                            started = time.perf_counter()
                            self.live_recognition_parts.append(
                                json.loads(self.live_recognizer.FinalResult())
                            )
                            self.live_recognition_seconds += (
                                time.perf_counter() - started
                            )
                            segment.vosk_result = self._merge_live_results(
                                self.live_recognition_parts
                            )
                            segment.vosk_stream_seconds = self.live_recognition_seconds
                            segment.transcript_ready_at = time.perf_counter()
                            log.info(
                                "Vosk streaming decode completed at VAD end after %d ms silence. Result %s",
                                END_SILENCE_FRAMES * FRAME_MS,
                                segment.vosk_result,
                            )
                        elif self.live_recognizer is not None:
                            self.live_recognizer.Reset()
                        log.info(
                            "VAD END  Fragment %.2f sec, end reason %s, speech frames %d of %d, audio queue %d",
                            segment.ended_at - segment.started_at,
                            segment.end_reason,
                            segment.speech_frames,
                            segment.total_frames,
                            self.audio_queue.qsize(),
                        )
                        self._submit(segment)
                    self.ring.write(frame)
                    if self.detector.recording and now - progress_at >= 0.5:
                        log.info(
                            "CAPTURE PROGRESS  %.2f sec, speech frames %d, end-silence progress %d of %d frames, audio queue %d",
                            self.detector.recording_frames * FRAME_MS / 1000,
                            self.detector.speech_frames,
                            self.detector.silence_run,
                            END_SILENCE_FRAMES,
                            self.audio_queue.qsize(),
                        )
                        progress_at = now
                    if now - heartbeat_at >= 1.0:
                        rms = window_rms_sum / max(window_frame_count, 1)
                        state = (
                            "CAPTURING SPEECH"
                            if self.detector.recording
                            else "waiting for speech"
                        )
                        log.info(
                            "MIC LIVE  %s | peak %d PCM | mean RMS %.1f | VAD speech %d of %d frames | audio queue %d | ASR queue %d",
                            state,
                            window_peak,
                            rms,
                            window_speech_frames,
                            window_frame_count,
                            self.audio_queue.qsize(),
                            self.asr_queue.qsize(),
                        )
                        heartbeat_at = time.monotonic()
                        window_peak = 0
                        window_speech_frames = 0
                        window_rms_sum = 0.0
                        window_frame_count = 0
        except KeyboardInterrupt:
            log.info("Stopping on Ctrl+C")
        except Exception:
            session_status = "error"
            raise
        finally:
            self.engine_service.emit("microphone", value=False)
            self.stop_event.set()
            worker.join()
            self._finish_session(session_status)
            log.info("Stopped")

    def _feed_live_audio(self, pcm: np.ndarray) -> None:
        if self.live_recognizer is None:
            return
        started = time.perf_counter()
        completed = self.live_recognizer.AcceptWaveform(pcm.tobytes())
        self.live_recognition_seconds += time.perf_counter() - started
        if completed:
            result_started = time.perf_counter()
            self.live_recognition_parts.append(
                json.loads(self.live_recognizer.Result())
            )
            self.live_recognition_seconds += time.perf_counter() - result_started

    def _finish_on_exact_partial(self, now: float) -> AudioSegment | None:
        """Close the current utterance as soon as Vosk has the complete command."""
        if self.live_recognizer is None or not self.detector.recording:
            return None
        partial_started = time.perf_counter()
        try:
            partial_result = json.loads(self.live_recognizer.PartialResult())
        except (ValueError, TypeError):
            log.exception("Could not read Vosk partial result")
            return None
        self.live_recognition_seconds += time.perf_counter() - partial_started
        partial_text = str(partial_result.get("partial", "")).strip()
        if self.live_recognition_parts:
            partial_text = self._merge_live_results(
                [*self.live_recognition_parts, {"text": partial_text}]
            )["text"]
        command = self.registry.resolve(partial_text)
        if command is None or normalize(partial_text).startswith("не "):
            return None

        segment_decision_at = time.perf_counter()
        final_started = time.perf_counter()
        final_result = json.loads(self.live_recognizer.FinalResult())
        self.live_recognition_seconds += time.perf_counter() - final_started
        if self.live_recognition_parts:
            final_result = self._merge_live_results(
                [*self.live_recognition_parts, final_result]
            )
        segment = self.detector.finish_early(now, "exact_command")
        segment.decision_detected_at = segment_decision_at
        segment.transcript_ready_at = time.perf_counter()
        segment.vosk_result = final_result
        segment.vosk_stream_seconds = self.live_recognition_seconds
        segment.early_match_text = partial_text
        segment.early_match_at = now
        self.live_recognizer.Reset()
        self.live_recognition_parts = []
        log.info(
            "EARLY COMMAND MATCH  Vosk partial %r matched %s. Capture closed immediately without end-silence wait. Final text %r",
            partial_text,
            command["id"],
            final_result.get("text", ""),
        )
        return segment

    @staticmethod
    def _merge_live_results(parts: list[dict[str, object]]) -> dict[str, object]:
        texts = [
            str(part.get("text", "")).strip() for part in parts if part.get("text")
        ]
        words = [word for part in parts for word in (part.get("result") or [])]
        return {"text": " ".join(texts), "result": words}

    def _finish_session(self, status: str) -> None:
        if self.session_finished:
            return
        path = self.session_archive.finish(status)
        self.session_finished = True
        records = self.session_archive.observations
        accepted = [item for item in records if item.get("accepted")]
        rejected = [item for item in records if not item.get("accepted")]
        partial = [item for item in records if item.get("partial_pass")]
        log.info(
            "SESSION SUMMARY  captured %d | sent to Vosk %d | executed %d | rejected %d | partial candidates %d | status %s",
            len(records),
            sum(bool(item.get("submitted_to_asr")) for item in records),
            len(accepted),
            len(rejected),
            len(partial),
            status,
        )
        for label, group in (
            ("all", records),
            ("executed", accepted),
            ("rejected", rejected),
        ):
            durations = [
                float(item["timing"]["speech_detection_to_decision_seconds"])
                for item in group
                if item.get("timing", {}).get("speech_detection_to_decision_seconds")
                is not None
            ]
            if durations:
                log.info(
                    "SESSION LATENCY  %s | mean %.3f sec | median %.3f sec | n %d",
                    label,
                    statistics.fmean(durations),
                    statistics.median(durations),
                    len(durations),
                )
        log.info("Session summary saved to %s", path)

    def _audio_callback(
        self, indata: np.ndarray, frames: int, time_info: Any, status: Any
    ) -> None:
        if not self.listening_event.is_set():
            return
        if status:
            log.warning("Microphone callback status %s", status)
        sample = indata[:, 0].copy()
        try:
            self.audio_queue.put_nowait((sample, time.perf_counter()))
        except queue.Full:
            log.error(
                "Audio callback queue full. Microphone frames were dropped before VAD."
            )

    def _submit(self, segment: AudioSegment) -> None:
        segment.command_registry = self.registry
        log.info(
            "STEP 5 of 5  Preparing captured fragment. Duration %.3f sec, samples %d, end reason %s",
            len(segment.pcm) / SAMPLE_RATE,
            len(segment.pcm),
            segment.end_reason,
        )
        if not segment.asr_eligible:
            observation_id, wav_path, metadata = self.archive.create(
                segment, self._vad_metadata()
            )
            metadata["capture"]["submitted_to_asr"] = False
            metadata["capture"]["started_at_utc"] = utc_from_monotonic(
                segment.started_at, self.clock_mono, self.clock_utc
            )
            metadata["capture"]["ended_at_utc"] = utc_from_monotonic(
                segment.ended_at, self.clock_mono, self.clock_utc
            )
            metadata["decision"] = {
                "accepted": False,
                "reason": "maximum_utterance_duration",
                "checks": {
                    "speech_detected": segment.speech_frames > 0,
                    "within_speech_duration_limit": False,
                    "submitted_to_asr": False,
                    "exact_full_phrase": None,
                    "word_confidence": None,
                },
                "partial_pass": segment.speech_frames > 0,
            }
            decision_seconds = time.perf_counter() - segment.started_at
            metadata["timing"] = {
                "capture_seconds": round(segment.ended_at - segment.started_at, 6),
                "speech_detection_to_decision_seconds": round(decision_seconds, 6),
                "processing_after_capture_seconds": round(
                    max(
                        0.0, decision_seconds - (segment.ended_at - segment.started_at)
                    ),
                    6,
                ),
            }
            json_path = self.archive.write_metadata(wav_path, metadata)
            if self.partial_archive.add(wav_path, json_path, metadata):
                log.info("Overlong speech saved as a partial candidate")
            log.warning(
                "Fragment exceeded %.1f sec including end pause, above %.1f sec speech limit. Final recognition skipped. Saved %s",
                MAX_UTTERANCE_SECONDS,
                MAX_SPEECH_SECONDS,
                wav_path.name,
            )
            self.session_archive.add_observation(
                {
                    "observation_id": observation_id,
                    "captured_started_at_utc": metadata["capture"].get(
                        "started_at_utc"
                    ),
                    "captured_ended_at_utc": metadata["capture"].get("ended_at_utc"),
                    "transcript": "",
                    "command_id": None,
                    "accepted": False,
                    "partial_pass": metadata["decision"]["partial_pass"],
                    "checks": metadata["decision"]["checks"],
                    "submitted_to_asr": False,
                    "rejection_reason": "maximum_utterance_duration",
                    "timing": metadata["timing"],
                    "observation_audio": wav_path.name,
                    "asr_audio": None,
                    "observation_json": wav_path.with_suffix(".json").name,
                }
            )
            return
        try:
            (
                segment.asr_pcm,
                segment.leading_trim_samples,
                segment.trailing_trim_samples,
            ) = self._trim_silence(segment.pcm)
            log.info(
                "Silence trimming complete. Removed %.1f ms from start and %.1f ms from end. ASR input %.3f sec",
                segment.leading_trim_samples * 1000 / SAMPLE_RATE,
                segment.trailing_trim_samples * 1000 / SAMPLE_RATE,
                len(segment.asr_pcm) / SAMPLE_RATE,
            )
            segment.queued_at = time.perf_counter()
            self.asr_queue.put_nowait(segment)
            log.info(
                "Queued for Vosk. ASR queue %d of %d",
                self.asr_queue.qsize(),
                ASR_QUEUE_MAXSIZE,
            )
        except queue.Full:
            segment.queued_at = None
            observation_id, wav_path, metadata = self.archive.create(
                segment, self._vad_metadata()
            )
            metadata["capture"]["started_at_utc"] = utc_from_monotonic(
                segment.started_at, self.clock_mono, self.clock_utc
            )
            metadata["capture"]["ended_at_utc"] = utc_from_monotonic(
                segment.ended_at, self.clock_mono, self.clock_utc
            )
            metadata["decision"] = {
                "accepted": False,
                "reason": "recognition_queue_full",
                "checks": {
                    "speech_detected": segment.speech_frames > 0,
                    "within_speech_duration_limit": True,
                    "submitted_to_asr": False,
                    "exact_full_phrase": None,
                    "word_confidence": None,
                },
                "partial_pass": segment.speech_frames > 0,
            }
            decision_seconds = time.perf_counter() - segment.started_at
            metadata["timing"] = {
                "capture_seconds": round(segment.ended_at - segment.started_at, 6),
                "speech_detection_to_decision_seconds": round(decision_seconds, 6),
                "processing_after_capture_seconds": round(
                    max(
                        0.0, decision_seconds - (segment.ended_at - segment.started_at)
                    ),
                    6,
                ),
            }
            json_path = self.archive.write_metadata(wav_path, metadata)
            if self.partial_archive.add(wav_path, json_path, metadata):
                log.info("Queue-overflow speech saved as a partial candidate")
            log.error(
                "Recognition queue is full. Segment saved but not submitted to Vosk."
            )
            self.session_archive.add_observation(
                {
                    "observation_id": observation_id,
                    "captured_started_at_utc": metadata["capture"].get(
                        "started_at_utc"
                    ),
                    "captured_ended_at_utc": metadata["capture"].get("ended_at_utc"),
                    "transcript": "",
                    "command_id": None,
                    "accepted": False,
                    "partial_pass": metadata["decision"]["partial_pass"],
                    "checks": metadata["decision"]["checks"],
                    "submitted_to_asr": False,
                    "rejection_reason": "recognition_queue_full",
                    "timing": metadata["timing"],
                    "observation_audio": wav_path.name,
                    "asr_audio": None,
                    "observation_json": wav_path.with_suffix(".json").name,
                }
            )

    def _trim_silence(self, pcm: np.ndarray) -> tuple[np.ndarray, int, int]:
        return trim_silence_vad(pcm, SAMPLE_RATE, FRAME_MS, VAD_MODE)

    def _vad_metadata(self) -> dict[str, Any]:
        return {
            "session_id": self.session_archive.session_id,
            "engine": "WebRTC VAD",
            "mode": VAD_MODE,
            "frame_ms": FRAME_MS,
            "frame_samples": FRAME_SAMPLES,
            "start_speech_frames": START_SPEECH_FRAMES,
            "end_silence_ms": END_SILENCE_FRAMES * FRAME_MS,
            "pre_roll_samples": PRE_SAMPLES,
            "max_utterance_seconds_including_end_pause": MAX_UTTERANCE_SECONDS,
            "max_active_speech_seconds": MAX_SPEECH_SECONDS,
        }


def main() -> int:
    assistant = None
    try:
        log.info(
            "RADU V3 starting. Detailed live diagnostics will appear in this window and in %s",
            LOG_PATH,
        )
        log.info("Working folder %s", Path.cwd())
        assistant = VoiceAssistant()
        log.info(
            "Loaded %d commands and %d exact phrases",
            len(assistant.registry.commands),
            len(assistant.registry.phrase_map),
        )
        log.info("Vosk grammar %s", assistant.registry.grammar)
        assistant.run()
    except FileNotFoundError as exc:
        if assistant is not None:
            assistant._finish_session("startup_error")
        log.error("%s", exc)
        if ENGINE_SERVICE:
            print(
                json.dumps({"event": "fatal", "message": str(exc)}, ensure_ascii=False),
                flush=True,
            )
        return 2
    except Exception:
        if assistant is not None:
            assistant._finish_session("error")
        log.exception("RADU V3 could not start")
        if ENGINE_SERVICE:
            print(
                json.dumps(
                    {"event": "fatal", "message": "Ошибка запуска Python-движка"},
                    ensure_ascii=False,
                ),
                flush=True,
            )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
