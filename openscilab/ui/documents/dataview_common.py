# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""What the parts of the data view share: names of files, sizes, filters, small helpers
(``dataview.py`` and its ``dataview_*.py`` parts)."""

from __future__ import annotations

import logging

from PySide6.QtWidgets import (
    QMenu,
    QWidget,
)

from ...core.formatting import to_small_time

log = logging.getLogger(__name__)

WINDOW_STATE_FILE = "analyzer-state.json"
#: Pixels of the name of the source device in the toolbar (longer names are shortened)
DEVICE_LABEL_WIDTH = 230
#: the line under the pinned channels
PINNED_SEPARATOR = 3
ZOOM_SLIDER_STEPS = 1000
#: A streaming capture shows its last 1/10 s until the user zooms
LIVE_WINDOW_DIVISOR = 10
CAPTURE_FILE_FILTER = "Captures (*.lac *.lac.gz *.sr);;All files (*)"
#: *Save as*: a number per sample, as the original software writes (and reads) its captures
COMPATIBLE_FILTER = "Captures for the original LogicAnalyzer (*.lac)"
#: milliseconds between two looks at a transfer that an operation waits for
TRANSFER_CHECK_MS = 50


def long_duration(seconds: float) -> str:
    """``to_small_time`` below a minute, else minutes and hours (a long stream)."""
    if seconds < 60:
        return to_small_time(seconds)
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min" if hours else f"{minutes} min {secs:02d} s"


#: samples a capture without a device may grow to by inserting
INSERT_LIMIT = 100_000_000

class _Menus:
    """Where the menus of the data view are made (they join the shell's menu bar)."""

    def __init__(self, parent: QWidget) -> None:
        self.parent = parent
        self.menus: list[QMenu] = []

    def addMenu(self, title: str) -> QMenu:  # noqa: N802 - the name of QMenuBar's
        menu = QMenu(title, self.parent)
        self.menus.append(menu)
        return menu

