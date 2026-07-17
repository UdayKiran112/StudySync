"""Reusable validators for API input."""

import re
from typing import Optional


def validate_hhmm(value: Optional[str]) -> Optional[str]:
    """Accept only zero-padded 24-hour times such as ``09:30``."""
    if value is None:
        return value
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
        raise ValueError("Time must be in HH:MM 24-hour format, e.g. 09:30")
    return value
