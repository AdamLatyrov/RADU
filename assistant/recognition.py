from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from assistant.commands import CommandRegistry, normalize
from settings import WORD_CONFIDENCE_MIN


@dataclass(slots=True)
class RecognitionDecision:
    transcript: str
    normalized_text: str
    words: list[dict[str, Any]]
    confidences: list[float]
    confidence_pass: bool
    explicit_negation: bool
    command: dict[str, Any] | None
    checks: dict[str, bool | None]
    rejection_reason: str | None

    @property
    def can_execute(self) -> bool:
        return self.rejection_reason is None


def evaluate_transcript(
    text: str,
    words: list[dict[str, Any]],
    registry: CommandRegistry,
    duration_pass: bool,
    speech_detected: bool,
) -> RecognitionDecision:
    normalized_text = normalize(text)
    confidences = [float(word["conf"]) for word in words if "conf" in word]
    confidence_pass = bool(confidences) and min(confidences) >= WORD_CONFIDENCE_MIN
    explicit_negation = normalized_text.startswith("не ")
    command = None if explicit_negation else registry.resolve(text)
    checks: dict[str, bool | None] = {
        "speech_detected": speech_detected,
        "within_speech_duration_limit": duration_pass,
        "transcript_nonempty": bool(normalized_text),
        "explicit_negation_absent": not explicit_negation,
        "exact_full_phrase": command is not None,
        "word_confidence": bool(confidence_pass),
        "action_execution": None,
    }

    rejection_reason = None
    if explicit_negation:
        rejection_reason = "explicit_negation"
    elif not normalized_text:
        rejection_reason = "empty_transcript"
    elif command is None:
        rejection_reason = "transcript_not_an_exact_configured_phrase"
    elif not confidence_pass:
        rejection_reason = "word_confidence_below_threshold_or_missing"

    return RecognitionDecision(
        transcript=text,
        normalized_text=normalized_text,
        words=words,
        confidences=confidences,
        confidence_pass=bool(confidence_pass),
        explicit_negation=explicit_negation,
        command=command,
        checks=checks,
        rejection_reason=rejection_reason,
    )
