from __future__ import annotations

import math

import numpy as np

try:
    from numba import njit
except ImportError:
    njit = None


def _hz_to_mel(hz: np.ndarray) -> np.ndarray:
    return 2595.0 * np.log10(1.0 + hz / 700.0)


def _mel_to_hz(mel: np.ndarray) -> np.ndarray:
    return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)


def _mel_filterbank(rate: int, fft_size: int, bins: int = 40) -> np.ndarray:
    mel_edges = np.linspace(
        _hz_to_mel(np.array([0.0]))[0], _hz_to_mel(np.array([rate / 2]))[0], bins + 2
    )
    hz_edges = _mel_to_hz(mel_edges)
    frequencies = np.linspace(0.0, rate / 2, fft_size // 2 + 1)
    bank = np.zeros((bins, len(frequencies)), dtype=np.float32)
    for i in range(bins):
        left, center, right = hz_edges[i : i + 3]
        bank[i] = np.maximum(
            0.0,
            np.minimum(
                (frequencies - left) / max(center - left, 1e-6),
                (right - frequencies) / max(right - center, 1e-6),
            ),
        )
    return bank


def _delta(matrix: np.ndarray) -> np.ndarray:
    padded = np.pad(matrix, ((2, 2), (0, 0)), mode="edge")
    return (2 * (padded[3:-1] - padded[1:-3]) + padded[4:] - padded[:-4]) / 10.0


def extract_features(pcm: np.ndarray, sample_rate: int = 16_000) -> dict[str, object]:
    signal = np.asarray(pcm, dtype=np.float32).reshape(-1) / 32768.0
    frame_length = int(sample_rate * 0.025)
    hop = int(sample_rate * 0.010)
    fft_size = 1 << (frame_length - 1).bit_length()
    if len(signal) < frame_length:
        signal = np.pad(signal, (0, frame_length - len(signal)))
    count = 1 + max(0, (len(signal) - frame_length) // hop)
    indices = np.arange(frame_length)[None, :] + hop * np.arange(count)[:, None]
    frames = signal[indices] * np.hanning(frame_length).astype(np.float32)[None, :]
    spectrum = np.abs(np.fft.rfft(frames, n=fft_size, axis=1)).astype(np.float32) ** 2
    mel = np.maximum(spectrum @ _mel_filterbank(sample_rate, fft_size).T, 1e-10)
    log_mel = np.log(mel).astype(np.float32)

    frequencies = (
        np.arange(fft_size // 2 + 1, dtype=np.float32) * sample_rate / fft_size
    )
    spectral_mass = spectrum.sum(axis=1) + 1e-12
    centroid = (spectrum @ frequencies) / spectral_mass
    cumulative = np.cumsum(spectrum, axis=1)
    roll_index = np.argmax(cumulative >= 0.85 * spectral_mass[:, None], axis=1)
    rolloff = frequencies[roll_index]
    frame_signal = signal[indices]
    zcr = (np.diff(np.signbit(frame_signal), axis=1) != 0).mean(axis=1)
    rms = np.sqrt(np.mean(frame_signal**2, axis=1) + 1e-12)

    n_mels = log_mel.shape[1]
    n_coeffs = 13
    n = np.arange(n_mels, dtype=np.float32)
    k = np.arange(n_coeffs, dtype=np.float32)[:, None]
    dct = np.cos(np.pi / n_mels * (n + 0.5) * k).astype(np.float32)
    dct[0] *= math.sqrt(1.0 / n_mels)
    dct[1:] *= math.sqrt(2.0 / n_mels)
    mfcc = (log_mel @ dct.T).astype(np.float32)
    delta_mfcc = np.concatenate((mfcc, _delta(mfcc)), axis=1).astype(np.float32)
    return {
        "log_mel": log_mel,
        "mfcc": mfcc,
        "delta_mfcc": delta_mfcc,
        "summary": {
            "frame_count": int(count),
            "frame_ms": 25,
            "hop_ms": 10,
            "log_mel": _summary(log_mel),
            "mfcc": _summary(mfcc),
            "delta_mfcc": _summary(delta_mfcc),
            "rms_mean": float(np.mean(rms)),
            "zero_crossing_rate_mean": float(np.mean(zcr)),
            "spectral_centroid_hz_mean": float(np.mean(centroid)),
            "spectral_rolloff_85_hz_mean": float(np.mean(rolloff)),
        },
    }


def _summary(matrix: np.ndarray) -> dict[str, object]:
    return {
        "dimensions": int(matrix.shape[1]),
        "mean_per_dimension": np.mean(matrix, axis=0).round(5).tolist(),
        "std_per_dimension": np.std(matrix, axis=0).round(5).tolist(),
    }


if njit is not None:

    @njit(cache=True, nogil=True)
    def _dtw_path_distance(costs: np.ndarray) -> float:
        rows, columns = costs.shape
        accumulated = np.full((rows + 1, columns + 1), np.inf, dtype=np.float32)
        lengths = np.zeros((rows + 1, columns + 1), dtype=np.int32)
        accumulated[0, 0] = 0.0
        for row in range(1, rows + 1):
            for column in range(1, columns + 1):
                best_cost = accumulated[row - 1, column - 1]
                best_length = lengths[row - 1, column - 1]
                up_cost = accumulated[row - 1, column]
                if up_cost < best_cost:
                    best_cost = up_cost
                    best_length = lengths[row - 1, column]
                left_cost = accumulated[row, column - 1]
                if left_cost < best_cost:
                    best_cost = left_cost
                    best_length = lengths[row, column - 1]
                accumulated[row, column] = best_cost + costs[row - 1, column - 1]
                lengths[row, column] = best_length + 1
        return accumulated[rows, columns] / max(1, lengths[rows, columns])
else:
    _dtw_path_distance = None


def _numpy_dtw_path_distance(costs: np.ndarray) -> float:
    """Exact cosine-DTW fallback for installations without the optional JIT."""
    rows, columns = costs.shape
    accumulated = np.full((rows + 1, columns + 1), np.inf, dtype=np.float32)
    lengths = np.zeros((rows + 1, columns + 1), dtype=np.int32)
    accumulated[0, 0] = 0.0
    for diagonal in range(2, rows + columns + 1):
        first_row = max(1, diagonal - columns)
        last_row = min(rows, diagonal - 1)
        row_indices = np.arange(first_row, last_row + 1)
        column_indices = diagonal - row_indices

        diagonal_cost = accumulated[row_indices - 1, column_indices - 1]
        up_cost = accumulated[row_indices - 1, column_indices]
        left_cost = accumulated[row_indices, column_indices - 1]
        candidates = np.stack((diagonal_cost, up_cost, left_cost), axis=0)
        best = np.argmin(candidates, axis=0)
        best_cost = candidates[best, np.arange(best.size)]
        best_length = np.where(
            best == 0,
            lengths[row_indices - 1, column_indices - 1],
            np.where(
                best == 1,
                lengths[row_indices - 1, column_indices],
                lengths[row_indices, column_indices - 1],
            ),
        )
        accumulated[row_indices, column_indices] = (
            best_cost + costs[row_indices - 1, column_indices - 1]
        )
        lengths[row_indices, column_indices] = best_length + 1
    return float(accumulated[rows, columns] / max(1, lengths[rows, columns]))


def feature_distance(
    first: dict[str, object], second: dict[str, object], key: str
) -> float:
    """Cosine-DTW with the original alignment and a compiled dynamic-programming kernel."""
    return feature_distance_with_norms(first, second, key)


def feature_norms(features: dict[str, object]) -> dict[str, np.ndarray]:
    return {
        key: np.maximum(
            np.linalg.norm(np.asarray(features[key], dtype=np.float32), axis=1), 1e-8
        )
        for key in ("mfcc", "delta_mfcc", "log_mel")
    }


def warm_up_dtw() -> None:
    """Compile or load the DTW kernel before the first live command."""
    if _dtw_path_distance is not None:
        _dtw_path_distance(np.zeros((2, 2), dtype=np.float32))


def feature_distance_with_norms(
    first: dict[str, object],
    second: dict[str, object],
    key: str,
    first_norm: np.ndarray | None = None,
    second_norm: np.ndarray | None = None,
) -> float:
    a = np.asarray(first[key], dtype=np.float32)
    b = np.asarray(second[key], dtype=np.float32)
    a_norm = (
        first_norm
        if first_norm is not None
        else np.maximum(np.linalg.norm(a, axis=1), 1e-8)
    )
    b_norm = (
        second_norm
        if second_norm is not None
        else np.maximum(np.linalg.norm(b, axis=1), 1e-8)
    )
    costs = 1.0 - (a @ b.T) / (a_norm[:, None] * b_norm[None, :])
    if _dtw_path_distance is not None:
        return float(_dtw_path_distance(costs))
    return _numpy_dtw_path_distance(costs)


def sequence_distance(first: dict[str, object], second: dict[str, object]) -> float:
    values = [
        feature_distance(first, second, key)
        for key in ("mfcc", "delta_mfcc", "log_mel")
    ]
    return float(np.mean(values))
