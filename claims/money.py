"""Single rupee/paise conversion used by the intake layer.

Money is stored and compared in integer paise. Every rupee value read from a
form, a document, or a saved record goes through :func:`to_paise`, which rounds
half-up to the nearest paisa (the same rule the policy engine uses), so a
fractional paisa can never be silently truncated in one layer and rounded in
another.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any


def to_paise(value: Any, *, allow_negative: bool = False) -> int:
    """Convert a rupee amount (str, int, float, or Decimal) to integer paise.

    Raises ``ValueError`` for non-numeric, non-finite, or (unless allowed)
    negative input. Floats are converted through ``str`` so ``0.1`` is 10 paise.
    """
    if isinstance(value, bool):
        raise ValueError(f"Invalid money amount: {value!r}")
    try:
        amount = value if isinstance(value, Decimal) else Decimal(str(value).strip().replace(",", ""))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"Invalid money amount: {value!r}") from exc
    if not amount.is_finite() or (amount < 0 and not allow_negative):
        raise ValueError(f"Invalid money amount: {value!r}")
    return int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def has_subpaise_precision(value: Any) -> bool:
    """True when a rupee amount has more than two decimal places (e.g. 10.005)."""
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return True
    return not amount.is_finite() or amount * 100 != (amount * 100).to_integral_value()


def to_rupees(paise: int) -> int | float:
    """Paise to a JSON-friendly rupee number: whole rupees as int, else a 2dp float."""
    return paise // 100 if paise % 100 == 0 else float(Decimal(paise) / 100)
