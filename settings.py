import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SAMPLE_RATE = 16_000
CHANNELS = 1
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000
RING_SECONDS = 2
VAD_MODE = 2
START_SPEECH_FRAMES = 3
END_SILENCE_MS = 200
END_SILENCE_FRAMES = END_SILENCE_MS // FRAME_MS
PRE_ROLL_MS = 300
PRE_SAMPLES = SAMPLE_RATE * PRE_ROLL_MS // 1000
MAX_SPEECH_SECONDS = 4.0
# Allow the configured end-of-speech pause after the speech-duration budget.
MAX_UTTERANCE_SECONDS = MAX_SPEECH_SECONDS + END_SILENCE_MS / 1000
QUEUE_MAXSIZE = 200
ASR_QUEUE_MAXSIZE = 20
WORD_CONFIDENCE_MIN = 1.0
MIN_REFERENCES = 2
REFERENCE_DISTANCE_MULTIPLIER = 1.5
VOSK_MODEL = Path(
    os.environ.get(
        "RADU_VOSK_MODEL",
        str(ROOT / "model" / "vosk-model-small-ru-0.22"),
    )
)
LOCAL_COMMANDS_FILE = (
    Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    / "RADU-V3-WPF"
    / "commands.json"
)
DEFAULT_COMMANDS_FILE = (
    LOCAL_COMMANDS_FILE if LOCAL_COMMANDS_FILE.is_file() else ROOT / "commands.json"
)
COMMANDS_FILE = Path(os.environ.get("RADU_COMMANDS_FILE", DEFAULT_COMMANDS_FILE))
DATA_ROOT = Path(os.environ.get("RADU_DATA_ROOT", str(ROOT / "data")))
OBSERVATIONS_ROOT = DATA_ROOT / "observations"
SESSIONS_ROOT = DATA_ROOT / "sessions"
COMMAND_HISTORY_FILE = DATA_ROOT / "command_history" / "executed_commands.jsonl"
DATASET_ROOT = DATA_ROOT / "dataset"
ACOUSTIC_CACHE_ROOT = DATA_ROOT / "cache" / "acoustic"
