from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import SupportsFloat

USD_TO_VND = 25_000


def usd_to_vnd(amount_usd: SupportsFloat | Decimal) -> int:
    """Convert USD to whole VND using the product's fixed planning rate."""
    amount = Decimal(str(amount_usd))
    return int(
        (amount * USD_TO_VND).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


def vnd_to_usd(amount_vnd: SupportsFloat | Decimal) -> float:
    """Convert VND to USD using the product's fixed planning rate."""
    return float(Decimal(str(amount_vnd)) / USD_TO_VND)


__all__ = ["USD_TO_VND", "usd_to_vnd", "vnd_to_usd"]
