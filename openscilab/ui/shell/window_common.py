# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""What the parts of the main window share: names of files and folders, sizes, small helpers
(``main_window.py`` and its ``window_*.py`` parts)."""

from __future__ import annotations

import logging

from PySide6.QtWidgets import QApplication

from ...core.instrument import Instrument

#: the size of the mark at the start of the header (logical pixels)
LOGO_SIZE = 34

log = logging.getLogger(__name__)

DOCUMENTATION_URL = "https://github.com/deckerjulian/openSciLab/wiki"

SHELL_STATE_FILE = "shell-state.json"
LAYOUTS_FILE = "shell-layouts.json"
#: Version of the dock layout stored by the shell (a newer layout ignores older ones).
SHELL_LAYOUT_VERSION = 3  # 2: the node palette is a dock of its own; 3: the docks sized when shown
#: the share of the window a dock takes at most when it is sized: on a small screen the docks are
#: narrower than their widths of the theme (``size("sidebar")`` ...) and the document keeps half
DOCK_SHARES = {"sidebar": 0.22, "inspector": 0.22, "node-palette": 0.18}
CAPTURE_EXTENSIONS = (".lac", ".lac.gz", ".sr")
FLOW_EXTENSIONS = (".flow.yaml", ".flow.yml")
OPEN_FILE_FILTER = ("openSciLab files (*.lac *.lac.gz *.sr *.flow.yaml *.py *.panel.yaml *.wave.yaml *.sdl);;"
                    "Captures (*.lac *.lac.gz *.sr);;Flows (*.flow.yaml *.py);;Panels (*.panel.yaml);;"
                    "Waveforms (*.wave.yaml *.sdl);;All files (*)")
PANEL_EXTENSIONS = (".panel.yaml",)


SIMULATOR_GROUP = "simulators"


def simulator_label(label: str) -> str:
    """The name of a simulator in the group of the simulators (*Arduino Uno*, not *Simulated
    Arduino Uno*: the group says it)."""
    return label.removeprefix("Simulated ").replace(
        " (firmware protocol)", ", firmware protocol")


def drives_outputs(instrument: Instrument) -> bool:
    """Whether a pin of ``instrument`` is an output or PWM now."""
    from ...core.instrument import MODE_OUTPUT, MODE_PWM, InstrumentError

    gpio = instrument.gpio
    if gpio is None:
        return False
    try:
        return any(gpio.mode(pin.name) in (MODE_OUTPUT, MODE_PWM) for pin in gpio.pins())
    except InstrumentError:
        return False


def device_node_id(instrument: Instrument) -> str:
    """A node id for an instrument (``uno`` for *Simulated Arduino Uno* at ``sim:uno``)."""
    import re

    base = instrument.uri.split(":", 1)[-1].split("/")[-1] if instrument.uri else instrument.name
    return re.sub(r"[^A-Za-z0-9_]", "_", base).strip("_") or "device"




def _is_flow_script(path: str) -> bool:
    """A Python file that defines a flow with the DSL."""
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read(20000)
    except OSError:
        return False
    return "openscilab.lab" in text and "flow(" in text


_TITLES: dict[tuple, list] = {}


def _simulator_titles(names: tuple, load_profile) -> list[tuple[str, str]]:
    """``(name, title)`` of the simulator profiles; read once per set of profiles."""
    if names not in _TITLES:
        titles = []
        for name in names:
            try:
                titles.append((name, load_profile(name)["title"]))
            except (OSError, ValueError, KeyError):
                continue
        _TITLES[names] = titles
    return _TITLES[names]


def _on_a_screen(x: int, y: int, width: int, height: int) -> bool:
    """A window at ``x``, ``y`` would have its title bar on one of the screens."""
    from PySide6.QtCore import QRect

    title = QRect(x, y, max(width, 200), 40)
    screens = QApplication.screens()
    return not screens or any(screen.availableGeometry().intersects(title) for screen in screens)
