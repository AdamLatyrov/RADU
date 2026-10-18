from datetime import datetime
from pathlib import Path

from tools.record_voice_sample import suggest_output_path


def test_suggest_output_path_keeps_phrase_and_timestamp(tmp_path: Path):
    result = suggest_output_path(
        "открой блокнот",
        base_dir=tmp_path,
        timestamp=datetime(2026, 10, 17, 12, 30, 45),
    )

    assert result == tmp_path / "radu_20261017_123045_открой_блокнот.wav"


def test_suggest_output_path_removes_path_separators(tmp_path: Path):
    result = suggest_output_path("../../секретная папка", base_dir=tmp_path)

    assert result.parent == tmp_path
    assert "/" not in result.name
    assert "\\" not in result.name
