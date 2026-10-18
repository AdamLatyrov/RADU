from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower().replace("ё", "е")).strip()


class CommandRegistry:
    def __init__(self, path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        self.commands = data["commands"]
        self.phrase_map: dict[str, dict[str, Any]] = {}
        for command in self.commands:
            for phrase in command["phrases"]:
                key = normalize(phrase)
                if key in self.phrase_map:
                    raise ValueError(f"Duplicate command phrase {phrase!r}")
                self.phrase_map[key] = command
        # Keep explicit negated phrases in the decoder vocabulary, but never
        # map them to an action. This gives the decoder a non-action path for
        # "не открой ..." instead of forcing the positive command.
        self.non_action_phrases = {
            normalize(f"не {phrase}") for phrase in self.phrase_map
        }
        self.grammar = json.dumps(
            [*self.phrase_map, *sorted(self.non_action_phrases), "[unk]"],
            ensure_ascii=False,
        )

    def resolve(self, phrase: str) -> dict[str, Any] | None:
        return self.phrase_map.get(normalize(phrase))
