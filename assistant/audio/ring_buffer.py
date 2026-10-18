from __future__ import annotations

import numpy as np


class RingBuffer:
    def __init__(self, size: int) -> None:
        self.buffer = np.zeros(size, dtype=np.int16)
        self.position = 0
        self.filled = 0

    def write(self, frame: np.ndarray) -> None:
        count = len(frame)
        if count >= len(self.buffer):
            self.buffer[:] = frame[-len(self.buffer) :]
            self.position = 0
            self.filled = len(self.buffer)
            return
        end = self.position + count
        if end <= len(self.buffer):
            self.buffer[self.position : end] = frame
        else:
            first = len(self.buffer) - self.position
            self.buffer[self.position :] = frame[:first]
            self.buffer[: end - len(self.buffer)] = frame[first:]
        self.position = end % len(self.buffer)
        self.filled = min(len(self.buffer), self.filled + count)

    def clear(self) -> None:
        self.position = 0
        self.filled = 0

    def snapshot(self) -> np.ndarray:
        if self.filled == 0:
            return np.empty(0, dtype=np.int16)
        start = (self.position - self.filled) % len(self.buffer)
        if start + self.filled <= len(self.buffer):
            return self.buffer[start : start + self.filled].copy()
        tail = self.buffer[start:]
        head = self.buffer[: self.filled - len(tail)]
        return np.concatenate((tail, head))

    def last(self, count: int) -> np.ndarray:
        return self.snapshot()[-count:].copy()
