from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from assistant.commands import normalize  # noqa: E402
from settings import COMMAND_HISTORY_FILE, COMMANDS_FILE  # noqa: E402


def read_config() -> dict[str, Any]:
    return json.loads(COMMANDS_FILE.read_text(encoding="utf-8"))


def save_config(config: dict[str, Any]) -> None:
    temporary = COMMANDS_FILE.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(COMMANDS_FILE)


def list_commands() -> None:
    config = read_config()
    print("Голосовые команды")
    for command in config["commands"]:
        print(f"  {command['id']}")
        print(f"    фразы  {' | '.join(command['phrases'])}")
        print(
            f"    действие  {command['action']['type']}  {command['action']['value']}"
        )


def add_command(
    command_id: str, phrases: list[str], action_type: str, value: str
) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", command_id):
        raise ValueError("ID должен содержать латинские буквы, цифры и подчёркивания")
    phrases = [phrase.strip() for phrase in phrases if phrase.strip()]
    if not phrases:
        raise ValueError("Нужно добавить хотя бы одну фразу")
    if action_type not in {"open_url", "open_app"}:
        raise ValueError("Действие должно быть open_url или open_app")
    if action_type == "open_url" and not value.startswith(("https://", "http://")):
        raise ValueError("Для open_url укажи полный адрес сайта")
    config = read_config()
    if any(command["id"] == command_id for command in config["commands"]):
        raise ValueError(f"Команда уже существует  {command_id}")
    known_phrases = {
        normalize(phrase)
        for command in config["commands"]
        for phrase in command["phrases"]
    }
    duplicates = _find_duplicate_phrases(phrases, known_phrases)
    if duplicates:
        raise ValueError(f"Фраза уже назначена другой команде  {', '.join(duplicates)}")
    config["commands"].append(
        {
            "id": command_id,
            "phrases": phrases,
            "action": {"type": action_type, "value": value},
        }
    )
    save_config(config)
    print(f"Команда {command_id} добавлена. Перезапусти RADU, чтобы загрузить её.")


def replace_phrases(command_id: str, phrases: list[str]) -> None:
    phrases = [phrase.strip() for phrase in phrases if phrase.strip()]
    if not phrases:
        raise ValueError("Нужно оставить хотя бы одну фразу")
    config = read_config()
    command = next(
        (item for item in config["commands"] if item["id"] == command_id), None
    )
    if command is None:
        raise ValueError(f"Команда не найдена  {command_id}")
    known_phrases = {
        normalize(phrase)
        for item in config["commands"]
        if item["id"] != command_id
        for phrase in item["phrases"]
    }
    duplicates = _find_duplicate_phrases(phrases, known_phrases)
    if duplicates:
        raise ValueError(f"Фраза уже назначена другой команде  {', '.join(duplicates)}")
    previous = command["phrases"]
    if previous == phrases:
        print("Фразы не изменились")
        return
    command["phrases"] = phrases
    save_config(config)
    print("Фразы обновлены. Перезапусти RADU, чтобы загрузить изменения.")


def _find_duplicate_phrases(phrases: list[str], known_phrases: set[str]) -> list[str]:
    seen = set(known_phrases)
    duplicates = []
    for phrase in phrases:
        normalized = normalize(phrase)
        if normalized in seen:
            duplicates.append(phrase)
        else:
            seen.add(normalized)
    return duplicates


def show_history(limit: int = 20) -> None:
    if not COMMAND_HISTORY_FILE.exists():
        print("Успешных запусков пока нет")
        return
    lines = COMMAND_HISTORY_FILE.read_text(encoding="utf-8").splitlines()[-limit:]
    for line in lines:
        row = json.loads(line)
        print(
            f"{row['executed_at_utc']}  {row['command_id']}  {row['transcript']}  {row['speech_detection_to_execution_seconds']:.3f} секунды"
        )


def interactive() -> None:
    while True:
        print("\nRADU  управление командами")
        print("1  Показать команды")
        print("2  Добавить команду")
        print("3  Заменить фразы существующей команды")
        print("4  Показать последние успешные запуски")
        print("0  Выход")
        choice = input("Выбор  ").strip()
        if choice == "0":
            return
        if choice == "1":
            list_commands()
        elif choice == "2":
            command_id = input("ID команды латиницей  ").strip()
            phrases = input("Фразы через вертикальную черту  ").split("|")
            kind = (
                "open_url"
                if input("Действие  1 открыть сайт, 2 открыть приложение  ").strip()
                == "1"
                else "open_app"
            )
            value = input("Адрес сайта или путь приложения  ").strip()
            add_command(command_id, phrases, kind, value)
        elif choice == "3":
            command_id = input("ID команды  ").strip()
            phrases = input("Новые фразы через вертикальную черту  ").split("|")
            replace_phrases(command_id, phrases)
        elif choice == "4":
            show_history()
        else:
            print("Неизвестный пункт меню")


def main() -> int:
    parser = argparse.ArgumentParser(description="Настройка голосовых команд RADU")
    subparsers = parser.add_subparsers(dest="operation")
    subparsers.add_parser("list")
    add_parser = subparsers.add_parser("add")
    add_parser.add_argument("command_id")
    add_parser.add_argument("--phrase", action="append", required=True)
    add_parser.add_argument(
        "--action-type", choices=("open_url", "open_app"), required=True
    )
    add_parser.add_argument("--value", required=True)
    rename_parser = subparsers.add_parser("replace-phrases")
    rename_parser.add_argument("command_id")
    rename_parser.add_argument("--phrase", action="append", required=True)
    history_parser = subparsers.add_parser("history")
    history_parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    try:
        if args.operation is None:
            interactive()
        elif args.operation == "list":
            list_commands()
        elif args.operation == "add":
            add_command(args.command_id, args.phrase, args.action_type, args.value)
        elif args.operation == "replace-phrases":
            replace_phrases(args.command_id, args.phrase)
        elif args.operation == "history":
            show_history(max(1, args.limit))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Ошибка  {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
