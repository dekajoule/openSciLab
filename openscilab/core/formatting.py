# Copyright (C) 2026 Julian Decker
# Copyright (C) Agustín Giménez Bernad (gusmanb), original LogicAnalyzer
#
# Part of openSciLab, a port and extension of his software;
# the changes are described in docs/improvements.md and CHANGELOG.md.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Human readable formatting of times, frequencies and sample counts.

Port of ``Extensions/DoubleExtensions.cs`` with correct SI symbols (the original
printed ``us`` and ``Mhz``).
"""

from __future__ import annotations


def to_small_time(seconds: float) -> str:
    """Format a duration given in seconds using the largest sensible unit."""
    if seconds is None:
        return "-"
    magnitude = abs(seconds)
    if magnitude < 1e-6:
        return f"{round(seconds * 1e9, 3):g} ns"
    if magnitude < 1e-3:
        return f"{round(seconds * 1e6, 3):g} µs"
    if magnitude < 1:
        return f"{round(seconds * 1e3, 3):g} ms"
    return f"{round(seconds, 3):g} s"


def to_large_frequency(frequency: float) -> str:
    """Format a frequency given in Hz."""
    if frequency is None:
        return "-"
    magnitude = abs(frequency)
    if magnitude >= 1e9:
        return f"{round(frequency / 1e9, 2):g} GHz"
    if magnitude >= 1e6:
        return f"{round(frequency / 1e6, 2):g} MHz"
    if magnitude >= 1e3:
        return f"{round(frequency / 1e3, 2):g} kHz"
    return f"{round(frequency, 2):g} Hz"


def to_exact_frequency(frequency: int) -> str:
    """A whole number of hertz with the largest unit that keeps every digit: ``33.333333 MHz``."""
    for scale, unit, decimals in ((1e9, "GHz", 9), (1e6, "MHz", 6), (1e3, "kHz", 3)):
        if abs(frequency) >= scale:
            return f"{frequency / scale:.{decimals}f}".rstrip("0").rstrip(".") + f" {unit}"
    return f"{int(frequency)} Hz"


_FREQUENCY_PREFIXES = {"": 1.0, "k": 1e3, "m": 1e6, "g": 1e9}


def _plain_number(text: str, whole: bool) -> str:
    """``text`` with ``.`` as its decimal point and without group separators: of both, the last one
    is the decimal point; one kind more than once separates groups, once a decimal point (in whole
    numbers, ``whole``, a group separator when three digits follow: ``1.000``)."""
    separators = [index for index, char in enumerate(text) if char in ".,"]
    if not separators:
        return text
    last = separators[-1]
    kinds = {text[index] for index in separators}
    if len(kinds) == 1 and (len(separators) > 1 or (whole and len(text) - last - 1 == 3)):
        return text.replace(".", "").replace(",", "")
    return text[:last].replace(".", "").replace(",", "") + "." + text[last + 1:]


def parse_frequency(text: str) -> float:
    """Hertz of a frequency such as ``1.5 MHz``, ``500k``, ``2,5 MHz``, ``48000`` or ``1.000.000 Hz``
    (``m`` is mega: a rate of millihertz is no rate)."""
    value = text.strip().lower().replace(" ", "").replace("\u00a0", "")
    if value.endswith("hz"):
        value = value[:-2]
    prefix = value[-1:] if value[-1:] in ("k", "m", "g") else ""
    number = _plain_number(value[: len(value) - len(prefix)], whole=not prefix)
    try:
        frequency = float(number) * _FREQUENCY_PREFIXES[prefix]
    except ValueError:
        raise ValueError(f"'{text}' is no frequency such as 1.5 MHz, 500 kHz or 48000") from None
    if frequency < 0 or frequency != frequency or frequency == float("inf"):
        raise ValueError("A frequency is a positive number")
    return frequency


def to_inferred_frequency(duration: float) -> str:
    """Frequency of a square wave whose half period lasts ``duration`` seconds."""
    if not duration or duration <= 0:
        return "-"
    return to_large_frequency(1.0 / (duration * 2))


def to_thousands(value: int) -> str:
    return f"{value:,}"


def to_bytes(count: float) -> str:
    """A size in bytes with the largest sensible binary unit, e.g. ``1.5 GB``."""
    for unit in ("B", "KB", "MB", "GB"):
        if abs(count) < 1024:
            return f"{count:.0f} {unit}" if unit == "B" else f"{count:.1f} {unit}"
        count /= 1024
    return f"{count:.1f} TB"


_TIME_UNITS = {"": 1.0, "s": 1.0, "ms": 1e-3, "us": 1e-6, "µs": 1e-6, "ns": 1e-9, "ps": 1e-12}


def parse_time(text: str) -> float:
    """Seconds of a duration such as ``1.5 µs``, ``200ns``, ``10 ms`` or ``0.002`` (seconds)."""
    value = text.strip().lower().replace(",", ".").replace(" ", "")
    for unit in sorted(_TIME_UNITS, key=len, reverse=True):
        if unit and value.endswith(unit):
            number = value[: -len(unit)]
            break
    else:
        unit, number = "", value
    try:
        seconds = float(number) * _TIME_UNITS[unit]
    except ValueError:
        raise ValueError(f"'{text}' is not a time such as 1.5 µs, 200 ns or 10 ms") from None
    if seconds < 0:
        raise ValueError("A time cannot be negative")
    return seconds
