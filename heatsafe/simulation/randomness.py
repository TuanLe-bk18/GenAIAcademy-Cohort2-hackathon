from __future__ import annotations

import hashlib
import json
import math
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class DeterministicRandom:
    """Hash-backed per-entity random stream with no shared mutable state."""

    def __init__(self, *key_parts: object):
        self._key = "\x1f".join(str(part) for part in key_parts).encode("utf-8")
        self._counter = 0

    def _uint64(self) -> int:
        payload = self._key + b"\x1e" + self._counter.to_bytes(8, "big")
        self._counter += 1
        return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")

    def uniform(self) -> float:
        return (self._uint64() + 0.5) / (2**64)

    def randint(self, low: int, high: int) -> int:
        if high < low:
            raise ValueError("high must be >= low")
        return low + self._uint64() % (high - low + 1)

    def normal(self) -> float:
        u1 = max(self.uniform(), 1e-15)
        u2 = self.uniform()
        return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)

    def triangular(self, low: float, mode: float, high: float) -> float:
        if not low <= mode <= high or low == high:
            raise ValueError("invalid triangular bounds")
        u = self.uniform()
        split = (mode - low) / (high - low)
        if u <= split:
            return low + math.sqrt(u * (high - low) * (mode - low))
        return high - math.sqrt((1 - u) * (high - low) * (high - mode))

    def gamma(self, shape: float, scale: float = 1.0) -> float:
        if shape <= 0 or scale <= 0:
            raise ValueError("gamma parameters must be positive")
        if shape < 1:
            return self.gamma(shape + 1, scale) * self.uniform() ** (1 / shape)
        d = shape - 1 / 3
        c = 1 / math.sqrt(9 * d)
        for _ in range(128):
            x = self.normal()
            v = (1 + c * x) ** 3
            if v <= 0:
                continue
            u = self.uniform()
            if u < 1 - 0.0331 * x**4:
                return scale * d * v
            if math.log(u) < 0.5 * x * x + d * (1 - v + math.log(v)):
                return scale * d * v
        raise RuntimeError("bounded gamma sampler did not converge")

    def poisson(self, mean: float) -> int:
        if not math.isfinite(mean) or mean < 0:
            raise ValueError("Poisson mean must be finite and non-negative")
        if mean == 0:
            return 0
        if mean < 30:
            threshold = math.exp(-mean)
            product = 1.0
            count = 0
            while product > threshold:
                count += 1
                product *= self.uniform()
            return count - 1

        # Hörmann PTRS transformed rejection.
        sqrt_mean = math.sqrt(mean)
        b = 0.931 + 2.53 * sqrt_mean
        a = -0.059 + 0.02483 * b
        inverse_alpha = 1.1239 + 1.1328 / (b - 3.4)
        vr = 0.9277 - 3.6224 / (b - 2)
        for _ in range(128):
            u = self.uniform() - 0.5
            v = self.uniform()
            us = 0.5 - abs(u)
            if us <= 0:
                continue
            candidate = math.floor((2 * a / us + b) * u + mean + 0.43)
            if us >= 0.07 and v <= vr and candidate >= 0:
                return candidate
            if candidate < 0 or (us < 0.013 and v > us):
                continue
            lhs = math.log(v * inverse_alpha / (a / (us * us) + b))
            rhs = (
                -mean
                + candidate * math.log(mean)
                - math.lgamma(candidate + 1)
            )
            if lhs <= rhs:
                return candidate
        raise RuntimeError("bounded Poisson sampler did not converge")

    def negative_binomial(self, mean: float, dispersion: float = 40.0) -> int:
        if mean <= 0:
            return 0
        mixed_mean = self.gamma(dispersion, mean / dispersion)
        return self.poisson(mixed_mean)


def stable_int(*parts: object, bits: int = 64) -> int:
    digest = hashlib.sha256("\x1f".join(map(str, parts)).encode("utf-8")).digest()
    return int.from_bytes(digest[: bits // 8], "big")


def _canonical(value: Any) -> Any:
    if is_dataclass(value):
        return {
            field.name: _canonical(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("canonical datetimes must be timezone-aware")
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical values must be finite")
        return format(value, ".6f")
    if isinstance(value, dict):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    return json.dumps(
        _canonical(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_checksum(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
