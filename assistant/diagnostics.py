from __future__ import annotations

import os
import sys
from typing import Any

from colorama import Fore, Style

from settings import WORD_CONFIDENCE_MIN


def format_word_confidence(words: list[dict[str, Any]]) -> str:
    scored_words = [
        (str(item.get("word", "?")), float(item["conf"]))
        for item in words
        if "conf" in item
    ]
    if not scored_words:
        return f"оценки слов отсутствуют; порог {WORD_CONFIDENCE_MIN:.3f}"

    values = ", ".join(f"{word} {score:.3f}" for word, score in scored_words)
    minimum = min(score for _, score in scored_words)
    return f"слова {values}; минимум {minimum:.3f}; порог {WORD_CONFIDENCE_MIN:.3f}"


def log_diagnostic_table(
    text: str,
    checks: dict[str, bool | None],
    action_result: str,
    rejection_reason: str | None,
    total_seconds: float,
    capture_seconds: float,
    post_capture_seconds: float,
    timing: dict[str, Any],
    words: list[dict[str, Any]],
) -> None:
    labels = {
        "speech_detected": "Речь найдена VAD",
        "within_speech_duration_limit": "Лимит длины",
        "transcript_nonempty": "Текст Vosk получен",
        "explicit_negation_absent": "Нет начального НЕ",
        "exact_full_phrase": "Полная фраза совпала",
        "word_confidence": "Уверенность слов не ниже 1.0",
        "action_execution": "Действие выполнено",
    }
    details = {
        "transcript_nonempty": text or "пустая транскрипция",
        "explicit_negation_absent": (
            "начальный НЕ блокирует команду"
            if checks["explicit_negation_absent"] is False
            else ""
        ),
        "word_confidence": format_word_confidence(words),
    }
    rows = [(labels[key], value, details.get(key, "")) for key, value in checks.items()]
    rows.append(
        (
            "Действие",
            action_result == "executed",
            action_result if action_result != "not_run" else str(rejection_reason),
        )
    )

    action_seconds = timing.get("speech_detection_to_action_seconds")
    action_description = (
        f"вызов запуска через {action_seconds:.3f} с"
        if action_seconds is not None
        else f"действие не запускалось, решение за {total_seconds:.3f} с"
    )
    rows.append(
        (
            "От начала речи до действия",
            None,
            f"{action_description}. Фрагмент захвачен за {capture_seconds:.3f} с, "
            f"обработка после закрытия записи {post_capture_seconds:.3f} с",
        )
    )
    rows.append(
        (
            "Обработка по этапам",
            None,
            f"подготовка {timing['recognition_setup_seconds'] * 1000:.0f} мс | "
            f"Vosk всего {timing['asr_seconds'] * 1000:.0f} мс | "
            f"вызов действия {timing['action_seconds'] * 1000:.0f} мс",
        )
    )

    width = max(len(label) for label, _, _ in rows)
    output = sys.stderr if os.environ.get("RADU_ENGINE_SERVICE") == "1" else sys.stdout
    print("\nПроверки команды", file=output)
    print(f"{'ПРИЗНАК':<{width}}  СТАТУС  ЗНАЧЕНИЕ", file=output)
    for label, passed, value in rows:
        color = Fore.CYAN if passed is None else (Fore.GREEN if passed else Fore.RED)
        status = "ВРЕМЯ" if passed is None else ("ПРОШЁЛ" if passed else "ОТКЛОНЁН")
        print(
            f"{color}{label:<{width}}  {status:<8}  {value}{Style.RESET_ALL}",
            file=output,
        )
    print(file=output)
