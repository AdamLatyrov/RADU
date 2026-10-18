from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import soundfile as sf

from assistant.acoustic_cache import AcousticCache
from assistant.audio.features import (
    extract_features,
    feature_distance,
    feature_distance_with_norms,
    feature_norms,
    warm_up_dtw,
)
from assistant.audio.silence import trim_silence_vad
from settings import ACOUSTIC_CACHE_ROOT, MIN_REFERENCES, REFERENCE_DISTANCE_MULTIPLIER


@dataclass
class CommandProfile:
    references: list[dict[str, object]]
    reference_norms: list[dict[str, np.ndarray]]
    thresholds: dict[str, float | None]
    calibration_distances: dict[str, list[float]]


FEATURES = {"MFCC": "mfcc", "Delta MFCC": "delta_mfcc", "Log Mel": "log_mel"}
log = logging.getLogger("radu.v3.acoustic")


class AcousticGate:
    """Small CPU-only MFCC, delta-MFCC and log-mel reference comparison."""

    def __init__(self, dataset_root: Path, command_ids: list[str]) -> None:
        self.profiles: dict[str, CommandProfile] = {}
        self.cache = AcousticCache(ACOUSTIC_CACHE_ROOT)
        warmup_start = time.perf_counter()
        warm_up_dtw()
        log.info(
            "Acoustic DTW kernel ready in %.1f ms",
            (time.perf_counter() - warmup_start) * 1000,
        )
        self.calibrate(dataset_root, command_ids)

    def calibrate(self, root: Path, command_ids: list[str]) -> None:
        started = time.perf_counter()
        log.info(
            "Acoustic calibration found %d command profiles in %s",
            len(command_ids),
            root,
        )
        for command_index, command_id in enumerate(command_ids, start=1):
            folder = root / command_id / "references"
            paths = sorted(folder.glob("*.wav")) if folder.exists() else []
            log.info(
                "Calibration %d of %d  %s has %d reference WAVs",
                command_index,
                len(command_ids),
                command_id,
                len(paths),
            )
            references = []
            manifest = []
            for reference_index, path in enumerate(paths, start=1):
                reference_start = time.perf_counter()
                features, source_hash, cache_hit = self.cache.load_or_extract(
                    path, self._read_features
                )
                references.append(features)
                manifest.append({"name": path.name, "sha256": source_hash})
                log.info(
                    "Reference %d of %d %s features %s in %.1f ms",
                    reference_index,
                    len(paths),
                    path.name,
                    "cache hit" if cache_hit else "extracted and cached",
                    (time.perf_counter() - reference_start) * 1000,
                )
            manifest_hash = self.cache.manifest_hash(manifest)
            reference_norms = [feature_norms(reference) for reference in references]
            cached_profile = self.cache.load_profile(
                command_id, manifest_hash, MIN_REFERENCES, REFERENCE_DISTANCE_MULTIPLIER
            )
            if cached_profile is not None:
                thresholds = cached_profile["thresholds"]
                calibration = cached_profile["calibration_distances"]
                self.profiles[command_id] = CommandProfile(
                    references, reference_norms, thresholds, calibration
                )
                log.info(
                    "Calibration cache hit for %s. Reused stored reference distances and thresholds",
                    command_id,
                )
                continue

            pair_count = len(references) * (len(references) - 1) // 2
            log.info(
                "Comparing %d positive reference pairs across %d features for %s",
                pair_count,
                len(FEATURES),
                command_id,
            )
            pair_start = time.perf_counter()
            calibration = {label: [] for label in FEATURES}
            for feature_label, feature_key in FEATURES.items():
                comparison_index = 0
                for i in range(len(references)):
                    for j in range(i + 1, len(references)):
                        comparison_index += 1
                        comparison_start = time.perf_counter()
                        log.info(
                            "Reference comparison %d of %d  %s  %s <> %s",
                            comparison_index,
                            pair_count,
                            feature_label,
                            paths[i].name,
                            paths[j].name,
                        )
                        distance = feature_distance(
                            references[i], references[j], feature_key
                        )
                        calibration[feature_label].append(distance)
                        log.info(
                            "Reference comparison complete  distance %.5f  elapsed %.1f ms",
                            distance,
                            (time.perf_counter() - comparison_start) * 1000,
                        )
            thresholds = {
                label: (
                    max(values) * REFERENCE_DISTANCE_MULTIPLIER + 0.01
                    if len(references) >= MIN_REFERENCES and values
                    else None
                )
                for label, values in calibration.items()
            }
            self.cache.save_profile(
                command_id,
                manifest_hash,
                MIN_REFERENCES,
                REFERENCE_DISTANCE_MULTIPLIER,
                thresholds,
                calibration,
            )
            log.info(
                "Calibration profile ready  %s  thresholds %s  pair comparison %.1f ms",
                command_id,
                thresholds,
                (time.perf_counter() - pair_start) * 1000,
            )
            self.profiles[command_id] = CommandProfile(
                references, reference_norms, thresholds, calibration
            )
        log.info(
            "Acoustic calibration complete in %.2f sec", time.perf_counter() - started
        )

    @staticmethod
    def _read_features(path: Path) -> dict[str, object]:
        audio, sample_rate = sf.read(path, dtype="int16", always_2d=True)
        if sample_rate != 16_000:
            raise ValueError(f"Reference must be 16 kHz PCM WAV: {path}")
        mono = np.mean(audio.astype(np.float32), axis=1).astype(np.int16)
        mono, _, _ = trim_silence_vad(mono, sample_rate)
        return extract_features(mono, sample_rate)

    def score(self, command_id: str, features: dict[str, object]) -> dict[str, object]:
        profile = self.profiles[command_id]
        if len(profile.references) < MIN_REFERENCES:
            return {
                "available": False,
                "accepted": False,
                "reason": "needs_two_reference_recordings",
                "reference_count": len(profile.references),
                "thresholds": profile.thresholds,
                "features": {},
            }
        measurements = {}
        accepted = True
        candidate_norms = feature_norms(features)
        feature_items = list(FEATURES.items())
        for feature_index, (label, key) in enumerate(feature_items):
            feature_start = time.perf_counter()
            threshold = profile.thresholds[label]
            distances = []
            for reference, reference_norms in zip(
                profile.references, profile.reference_norms
            ):
                distances.append(
                    feature_distance_with_norms(
                        features,
                        reference,
                        key,
                        candidate_norms[key],
                        reference_norms[key],
                    )
                )
                if threshold is not None and distances[-1] <= threshold:
                    break
            feature_seconds = time.perf_counter() - feature_start
            distance = min(distances)
            passed = threshold is not None and distance <= threshold
            accepted = accepted and passed
            log.info(
                "Acoustic feature scoring %s completed in %.1f ms after checking %d of %d references",
                label,
                feature_seconds * 1000,
                len(distances),
                len(profile.references),
            )
            measurements[label] = {
                "distance": distance,
                "threshold": threshold,
                "passed": passed,
                "elapsed_seconds": round(feature_seconds, 6),
                "reference_distances": distances,
                "references_compared": len(distances),
                "calibration_pair_distances": profile.calibration_distances[label],
            }
            if not passed:
                accepted = False
                for skipped_label, _ in feature_items[feature_index + 1 :]:
                    measurements[skipped_label] = {
                        "distance": None,
                        "threshold": profile.thresholds[skipped_label],
                        "passed": None,
                        "skipped": True,
                        "elapsed_seconds": 0.0,
                        "reference_distances": [],
                        "calibration_pair_distances": profile.calibration_distances[
                            skipped_label
                        ],
                    }
                if feature_index + 1 < len(feature_items):
                    log.info(
                        "Early acoustic rejection after %s. Skipped %s",
                        label,
                        ", ".join(
                            name for name, _ in feature_items[feature_index + 1 :]
                        ),
                    )
                break
        return {
            "available": True,
            "accepted": accepted,
            "reason": "all_feature_thresholds_passed"
            if accepted
            else "one_or_more_features_outside_threshold",
            "reference_count": len(profile.references),
            "features": measurements,
            "calibration_method": "per_feature_max_positive_pair_distance_times_1_5_plus_0_01",
        }
