from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

CACHE_SCHEMA_VERSION = 1
FEATURE_EXTRACTOR_VERSION = 1
DISTANCE_ALGORITHM_VERSION = 3


class AcousticCache:
    """Persistent reference features and per-command calibration profiles."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.feature_root = root / "features"
        self.profile_root = root / "profiles"

    @staticmethod
    def file_hash(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def manifest_hash(entries: list[dict[str, str]]) -> str:
        payload = json.dumps(
            entries, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def load_or_extract(
        self, path: Path, extractor
    ) -> tuple[dict[str, Any], str, bool]:
        source_hash = self.file_hash(path)
        cache_key = hashlib.sha256(
            f"{CACHE_SCHEMA_VERSION}|{FEATURE_EXTRACTOR_VERSION}|{source_hash}".encode(
                "ascii"
            )
        ).hexdigest()
        cache_path = self.feature_root / f"{cache_key}.npz"
        if cache_path.is_file():
            try:
                with np.load(cache_path, allow_pickle=False) as stored:
                    result = {
                        "log_mel": stored["log_mel"],
                        "mfcc": stored["mfcc"],
                        "delta_mfcc": stored["delta_mfcc"],
                        "summary": json.loads(str(stored["summary"].item())),
                    }
                return result, source_hash, True
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                cache_path.unlink(missing_ok=True)

        result = extractor(path)
        self.feature_root.mkdir(parents=True, exist_ok=True)
        temporary = cache_path.with_suffix(".npz.tmp")
        with temporary.open("wb") as target:
            np.savez(
                target,
                log_mel=np.asarray(result["log_mel"], dtype=np.float32),
                mfcc=np.asarray(result["mfcc"], dtype=np.float32),
                delta_mfcc=np.asarray(result["delta_mfcc"], dtype=np.float32),
                summary=np.asarray(json.dumps(result["summary"], ensure_ascii=False)),
            )
        temporary.replace(cache_path)
        return result, source_hash, False

    def load_profile(
        self,
        command_id: str,
        manifest_hash: str,
        min_references: int,
        distance_multiplier: float,
    ) -> dict[str, Any] | None:
        path = self._profile_path(command_id)
        if not path.is_file():
            return None
        try:
            profile = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        expected = {
            "cache_schema_version": CACHE_SCHEMA_VERSION,
            "feature_extractor_version": FEATURE_EXTRACTOR_VERSION,
            "distance_algorithm_version": DISTANCE_ALGORITHM_VERSION,
            "manifest_hash": manifest_hash,
            "min_references": min_references,
            "distance_multiplier": distance_multiplier,
        }
        if any(profile.get(key) != value for key, value in expected.items()):
            return None
        return profile

    def save_profile(
        self,
        command_id: str,
        manifest_hash: str,
        min_references: int,
        distance_multiplier: float,
        thresholds: dict[str, float | None],
        calibration_distances: dict[str, list[float]],
    ) -> None:
        path = self._profile_path(command_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        value = {
            "cache_schema_version": CACHE_SCHEMA_VERSION,
            "feature_extractor_version": FEATURE_EXTRACTOR_VERSION,
            "distance_algorithm_version": DISTANCE_ALGORITHM_VERSION,
            "manifest_hash": manifest_hash,
            "min_references": min_references,
            "distance_multiplier": distance_multiplier,
            "thresholds": thresholds,
            "calibration_distances": calibration_distances,
        }
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(path)

    def _profile_path(self, command_id: str) -> Path:
        key = hashlib.sha256(command_id.encode("utf-8")).hexdigest()
        return self.profile_root / f"{key}.json"
