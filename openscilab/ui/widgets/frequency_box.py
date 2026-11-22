# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""A sample rate typed with its unit, and a slider for it within the rates the device has."""

from __future__ import annotations

import math
import re
from typing import Optional

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QValidator
from PySide6.QtWidgets import QAbstractSpinBox, QComboBox, QSlider, QSpinBox, QWidget

from ...core.formatting import parse_frequency, to_exact_frequency
from .capture_timeline import DRAG_SERIES, nice_count, step_count

#: What may still become a frequency while it is typed
_PARTIAL = re.compile(r"[\d\s.,]*(e[+-]?\d*)?\s*[kmg]?\s*(h|hz)?", re.IGNORECASE)


class FrequencyBox(QSpinBox):
    """A rate in hertz, shown with its unit (``1.5 MHz``) and typed with one or without
    (``500k``, ``2,5 MHz``, ``48000``); the arrows step through 1, 2, 5 × 10ⁿ."""

    #: The least or the most rate changed
    range_changed = Signal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        # the rate applies when it is typed (Enter, leaving the field), not at every digit
        self.setKeyboardTracking(False)
        self.setCorrectionMode(QAbstractSpinBox.CorrectToNearestValue)
        self.setToolTip("The sample rate, with a unit or in Hz: 1.5 MHz, 500k, 48000")

    def textFromValue(self, value: int) -> str:  # noqa: N802 - Qt override
        return to_exact_frequency(value)

    def valueFromText(self, text: str) -> int:  # noqa: N802 - Qt override
        try:
            return min(max(int(round(parse_frequency(text))), self.minimum()), self.maximum())
        except ValueError:
            return self.value()

    def validate(self, text: str, position: int):  # noqa: D401 - Qt override
        try:
            value = int(round(parse_frequency(text)))
        except ValueError:
            state = QValidator.Intermediate if _PARTIAL.fullmatch(text.strip()) else QValidator.Invalid
            return state, text, position
        state = QValidator.Acceptable if self.minimum() <= value <= self.maximum() else QValidator.Intermediate
        return state, text, position

    def fixup(self, text: str) -> str:
        return self.textFromValue(self.valueFromText(text))

    def stepBy(self, steps: int) -> None:  # noqa: N802 - Qt override
        self.setValue(min(max(step_count(self.value(), steps), self.minimum()), self.maximum()))
        self.selectAll()

    def setRange(self, minimum: int, maximum: int) -> None:  # noqa: N802 - Qt naming
        super().setRange(minimum, maximum)
        self.range_changed.emit()

    def setMinimum(self, minimum: int) -> None:  # noqa: N802 - Qt naming
        super().setMinimum(minimum)
        self.range_changed.emit()

    def setMaximum(self, maximum: int) -> None:  # noqa: N802 - Qt naming
        super().setMaximum(maximum)
        self.range_changed.emit()


class RateSlider(QSlider):
    """The rate of a :class:`FrequencyBox` on a logarithmic scale between its limits, in round
    numbers (the ends: the limits); or, for a device with a list of rates (``rate_list``), one of
    them. Enabled with what it sets."""

    #: Positions of the logarithmic scale
    STEPS = 1000

    def __init__(self, box: FrequencyBox, rate_list: Optional[QComboBox] = None,
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(Qt.Horizontal, parent)
        self.box = box
        self.rate_list = rate_list
        self.setAccessibleName("Sample rate")
        box.valueChanged.connect(self.follow)
        box.range_changed.connect(self.follow)
        if rate_list is not None:
            rate_list.model().rowsInserted.connect(self.follow)
            rate_list.model().rowsRemoved.connect(self.follow)
            rate_list.currentIndexChanged.connect(self.follow)
        self.valueChanged.connect(self._moved)
        self._source().installEventFilter(self)
        self.follow()

    def _source(self) -> QWidget:
        return self.rate_list if self.rate_list is not None else self.box

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt naming
        if event.type() == QEvent.EnabledChange:
            self.follow()
        return False

    def follow(self, *_args) -> None:
        """Shows the rate (and the limits) of the box or the list."""
        self.blockSignals(True)
        if self.rate_list is not None:
            count = self.rate_list.count()
            self.setRange(0, max(count - 1, 0))
            self.setValue(max(self.rate_list.currentIndex(), 0))
            self.setEnabled(self.rate_list.isEnabled() and count > 1)
        else:
            low, high = max(self.box.minimum(), 1), self.box.maximum()
            self.setRange(0, self.STEPS)
            if high > low:
                share = math.log(max(self.box.value(), low) / low) / math.log(high / low)
                self.setValue(int(round(min(max(share, 0.0), 1.0) * self.STEPS)))
            self.setEnabled(self.box.isEnabled() and high > low)
        if self.rate_list is not None and self.rate_list.count():
            low, high = self.rate_list.itemData(0), self.rate_list.itemData(self.rate_list.count() - 1)
        else:
            low, high = self.box.minimum(), self.box.maximum()
        self.setToolTip(f"Sample rate: {to_exact_frequency(self.box.value())} "
                        f"(the device: {to_exact_frequency(low)} to {to_exact_frequency(high)})")
        self.blockSignals(False)

    def rate_at(self, position: int) -> int:
        """The rate of a position of the logarithmic scale."""
        low, high = max(self.box.minimum(), 1), self.box.maximum()
        if position <= 0 or high <= low:
            return low
        if position >= self.STEPS:
            return high
        return min(max(nice_count(low * (high / low) ** (position / self.STEPS), DRAG_SERIES), low), high)

    def _moved(self, position: int) -> None:
        if self.rate_list is not None:
            self.rate_list.setCurrentIndex(position)
        else:
            self.box.setValue(self.rate_at(position))
