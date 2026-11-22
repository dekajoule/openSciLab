# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The menus of the data view (*Data*, *Analyze*, *Display*) and their icons.

A part of :class:`~.dataview.DataView` (ARCHITECTURE.md, *Known hotspots*): the data view
combines its parts, which share its state through ``self``. Signals stay declared on the data view.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence

from ..dialogs.chart_dialog import ChartDialog
from ..dialogs.compare_dialog import CompareDialog
from ..icons import icon
from ..view_model import (
    CHANNEL_HEIGHT_STEP,
    DEFAULT_CHANNEL_HEIGHT,
)
from .dataview_common import (
    _Menus,
)

log = logging.getLogger(__name__)


class DataViewMenusMixin:
    """The menus of the data view (*Data*, *Analyze*, *Display*) and their icons."""

    def _build_menu(self) -> None:
        menu = self._menus = _Menus(self)

        # ------------------------------------------------------------ File
        self.file_menu = file_menu = menu.addMenu("&File")
        self.action_new = QAction("&New capture...", self)  # Ctrl+N is the shell's New flow
        self.action_new.setStatusTip("Compose a capture from signal descriptions")
        self.action_new.triggered.connect(self.new_capture)
        file_menu.addAction(self.action_new)

        self.action_open = QAction("&Open capture...", self)
        self.action_open.setShortcut(QKeySequence.Open)
        self.action_open.triggered.connect(self.open_capture)
        file_menu.addAction(self.action_open)

        file_menu.addSeparator()
        self.action_save = QAction("&Save", self)
        self.action_save.setShortcut(QKeySequence.Save)
        self.action_save.triggered.connect(self.save_capture)
        file_menu.addAction(self.action_save)

        self.action_save_as = QAction("Save &as...", self)
        self.action_save_as.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self.action_save_as.triggered.connect(self.save_capture_as)
        file_menu.addAction(self.action_save_as)

        self.export_menu = export_menu = file_menu.addMenu("&Export")
        self.action_export_csv = QAction("Comma separated values (.csv)...", self)
        self.action_export_csv.triggered.connect(lambda: self.export_capture("csv"))
        export_menu.addAction(self.action_export_csv)
        self.action_export_vcd = QAction("Value change dump (.vcd)...", self)
        self.action_export_vcd.triggered.connect(lambda: self.export_capture("vcd"))
        export_menu.addAction(self.action_export_vcd)
        self.action_export_sr = QAction("sigrok session for PulseView (.sr)...", self)
        self.action_export_sr.triggered.connect(lambda: self.export_capture("sr"))
        export_menu.addAction(self.action_export_sr)
        export_menu.addSeparator()
        self.action_export_annotations = QAction("Decoder output (.csv, .json)...", self)
        self.action_export_annotations.triggered.connect(self.export_annotations)
        export_menu.addAction(self.action_export_annotations)


        # ------------------------------------------------------------ Data
        self.data_menu = data_menu = menu.addMenu("&Data")
        self.action_repeat = QAction("Capture &again", self)
        self.action_repeat.setShortcut(QKeySequence("Ctrl+R"))
        self.action_repeat.setStatusTip("Capture again with the device and the settings of the shown capture")
        self.action_repeat.triggered.connect(self.repeat_capture)
        data_menu.addAction(self.action_repeat)
        self.action_stop = QAction("S&top capture", self)

        self.action_stop.triggered.connect(self.abort_capture)
        data_menu.addAction(self.action_stop)
        self.action_device_card = QAction("Open the &device", self)
        self.action_device_card.setStatusTip("The device the data come from: capture settings, profiles, device functions")
        self.action_device_card.triggered.connect(lambda: self.source is not None
                                                  and self.device_card_requested.emit(self.source.instrument))
        data_menu.addAction(self.action_device_card)
        data_menu.addSeparator()
        self.action_simulation = QAction("&Simulated signals...", self)
        self.action_simulation.setStatusTip("Signals of common protocols computed on this computer")
        self.action_simulation.triggered.connect(self.simulated_capture)
        data_menu.addAction(self.action_simulation)
        self.action_align = QAction("&Align boards", self)
        self.action_align.setStatusTip(
            "Align the boards of a multi device capture using a reference line or the clock"
        )
        self.action_align.triggered.connect(self.align_boards)
        data_menu.addAction(self.action_align)

        self.action_use_names = QAction("Use these channel &names for the next capture", self)
        self.action_use_names.setStatusTip("The device names its channels as here (renamed in this data view)")
        self.action_use_names.triggered.connect(self.use_names_for_device)
        data_menu.addAction(self.action_use_names)

        # editing the samples of the selection in the ruler (also in its right-click menu)
        data_menu.addSeparator()
        self.sample_actions = []
        for text, shortcut, slot in (
            ("Cu&t samples", QKeySequence.Cut, lambda: self._on_selection(self.cut_samples)),
            ("&Copy samples", QKeySequence.Copy, lambda: self._on_selection(self.copy_samples)),
            ("&Paste samples", QKeySequence.Paste, self._paste_at_marker),
            ("De&lete samples", QKeySequence.Delete, lambda: self._on_selection(self.delete_samples)),
            ("&Insert samples...", None, lambda: self.insert_samples_dialog(self._edit_position())),
            ("S&hift channels...", None, self.shift_channels),
        ):
            action = QAction(text, self)
            if shortcut is not None:
                action.setShortcut(QKeySequence(shortcut))
                action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
                self.addAction(action)
            action.triggered.connect(lambda _checked=False, slot=slot: slot())
            data_menu.addAction(action)
            self.sample_actions.append(action)

        for action, name in (
            (self.action_new, "file-plus"),
            (self.action_open, "folder"),
            (self.action_save, "save"),
            (self.action_save_as, "save"),
            (self.action_export_csv, "export"),
            (self.action_export_vcd, "export"),
            (self.action_repeat, "repeat"),
            (self.action_stop, "stop"),
            (self.action_device_card, "devices"),
            (self.action_simulation, "wave"),
        ):
            action.setIcon(icon(name))

        # --------------------------------------------------------- Analyze
        self.analyze_menu = analyze_menu = menu.addMenu("&Analyze")
        self.action_buses = QAction("&New bus or group...", self)
        self.action_buses.setStatusTip("Show several channels as one value, with a symbol table")
        self.action_buses.triggered.connect(lambda: self.edit_bus(None))
        analyze_menu.addAction(self.action_buses)
        analyze_menu.addSeparator()
        self.action_find = QAction("&Find...", self)
        self.action_find.setShortcut(QKeySequence.Find)
        self.action_find.triggered.connect(lambda: self.show_panel(self.search_panel))
        analyze_menu.addAction(self.action_find)
        self.action_find_next = QAction("Find &next", self)
        self.action_find_next.setShortcut(QKeySequence("F3"))
        self.action_find_next.triggered.connect(lambda: self.search_panel.step(1))
        analyze_menu.addAction(self.action_find_next)
        self.action_find_previous = QAction("Find &previous", self)
        self.action_find_previous.setShortcut(QKeySequence("Shift+F3"))
        self.action_find_previous.triggered.connect(lambda: self.search_panel.step(-1))
        analyze_menu.addAction(self.action_find_previous)
        analyze_menu.addSeparator()
        self.action_measure = QAction("&Measurements", self)
        self.action_measure.setShortcut(QKeySequence("Ctrl+Shift+M"))  # Cmd+M minimises on macOS
        self.action_measure.triggered.connect(lambda: self.show_panel(self.measure_panel))
        analyze_menu.addAction(self.action_measure)
        self.action_charts = QAction("&Charts and histograms...", self)
        self.action_charts.triggered.connect(lambda: ChartDialog(self.model, self).exec())
        analyze_menu.addAction(self.action_charts)
        self.action_compare = QAction("C&ompare with a reference...", self)
        self.action_compare.triggered.connect(lambda: CompareDialog(self.model, self).exec())
        analyze_menu.addAction(self.action_compare)
        self.action_state = QAction("&State analysis (clocked)...", self)
        self.action_state.setStatusTip("Resample the capture on the edges of a clock channel")
        self.action_state.triggered.connect(self.state_analysis)
        analyze_menu.addAction(self.action_state)
        self.action_back_to_timing = QAction("&Back to the timing capture", self)
        self.action_back_to_timing.setStatusTip("Leave the state analysis for the capture it was made of")
        self.action_back_to_timing.triggered.connect(self.back_to_timing)
        analyze_menu.addAction(self.action_back_to_timing)
        analyze_menu.addSeparator()
        self.action_play = QAction("&Play as signal...", self)
        self.action_play.setStatusTip("Play channels of the capture on a generator output (between the cursors if set)")
        self.action_play.triggered.connect(self.play_as_signal)
        analyze_menu.addAction(self.action_play)
        self._capture_actions = [
            self.action_buses, self.action_find, self.action_measure, self.action_charts,
            self.action_compare, self.action_state, self.action_export_sr, self.action_export_annotations,
            self.action_play,
        ]
        for action, name in (
            (self.action_buses, "bus"), (self.action_find, "search"), (self.action_measure, "ruler"),
            (self.action_charts, "chart"), (self.action_compare, "compare"), (self.action_state, "clock"),
            (self.action_back_to_timing, "reset"),
            (self.action_export_sr, "export"), (self.action_export_annotations, "export"),
        ):
            action.setIcon(icon(name))

        # ------------------------------------------------------------ View
        self.view_menu = view_menu = menu.addMenu("&View")
        action_zoom_in = QAction("Zoom &in", self)
        # "=" is "+" without Shift on US keyboards.
        action_zoom_in.setShortcuts(
            QKeySequence.keyBindings(QKeySequence.ZoomIn) + [QKeySequence("+"), QKeySequence("=")]
        )
        action_zoom_in.triggered.connect(lambda: self.model.zoom(0.5))
        view_menu.addAction(action_zoom_in)

        action_zoom_out = QAction("Zoom &out", self)
        action_zoom_out.setShortcuts(QKeySequence.keyBindings(QKeySequence.ZoomOut) + [QKeySequence("-")])
        action_zoom_out.triggered.connect(lambda: self.model.zoom(2.0))
        view_menu.addAction(action_zoom_out)

        action_fit = QAction("Zoom to &fit", self)
        action_fit.setShortcut("Ctrl+0")
        action_fit.triggered.connect(self.model.zoom_to_fit)
        view_menu.addAction(action_fit)
        self.action_fit_selection = QAction("Zoom to the &selection", self)
        self.action_fit_selection.setShortcut("Ctrl+E")
        self.action_fit_selection.setStatusTip("Show the samples selected on the time axis")
        self.action_fit_selection.triggered.connect(self.model.zoom_to_selection)
        view_menu.addAction(self.action_fit_selection)

        action_trigger = QAction("Go to &trigger", self)
        action_trigger.setShortcut("Ctrl+T")
        action_trigger.triggered.connect(self.go_to_trigger)
        view_menu.addAction(action_trigger)

        view_menu.addSeparator()
        for title, shortcut, handler in (
            ("T&aller channels", "Ctrl+Shift+Up", lambda: self.model.zoom_channels(CHANNEL_HEIGHT_STEP)),
            ("S&horter channels", "Ctrl+Shift+Down", lambda: self.model.zoom_channels(1 / CHANNEL_HEIGHT_STEP)),
            ("&Default channel height", "Ctrl+Shift+0", lambda: self.model.set_channel_height(DEFAULT_CHANNEL_HEIGHT)),
        ):
            action = QAction(title, self)
            action.setShortcut(shortcut)
            action.triggered.connect(handler)
            view_menu.addAction(action)
        view_menu.addSeparator()

        # Keyboard navigation, as in the original: Ctrl moves by a tenth of the
        # window, Ctrl+Up/Down by a full window, Shift by a single sample.
        navigate_menu = view_menu.addMenu("&Navigate")
        for title, shortcut, step in (
            ("Scroll &left", "Ctrl+Left", -0.1),
            ("Scroll &right", "Ctrl+Right", 0.1),
            ("Previous &page", "Ctrl+Down", -1.0),
            ("Next pa&ge", "Ctrl+Up", 1.0),
            ("Previous sample", "Shift+Left", None),
            ("Next sample", "Shift+Right", None),
        ):
            action = QAction(title, self)
            if shortcut in ("Ctrl+Left", "Ctrl+Right"):
                # The plain arrow keys scroll as well; text fields keep them for editing.
                action.setShortcuts([QKeySequence(shortcut), QKeySequence(shortcut.split("+")[1])])
            else:
                action.setShortcut(shortcut)
            if step is None:
                samples = -1 if "Previous" in title else 1
                action.triggered.connect(lambda _checked=False, s=samples: self.model.scroll_by(s))
            else:
                action.triggered.connect(
                    lambda _checked=False, f=step: self.model.scroll_by(
                        int(self.model.visible_samples * f) or (1 if f > 0 else -1)
                    )
                )
            navigate_menu.addAction(action)

        view_menu.addSeparator()
        action_show_channels = QAction("Show all &channels", self)
        action_show_channels.triggered.connect(lambda: self.channel_viewer.show_all_channels())
        view_menu.addAction(action_show_channels)

        action_unpin = QAction("&Unpin all channels", self)
        action_unpin.triggered.connect(self.model.unpin_all)
        view_menu.addAction(action_unpin)

        self.action_toggle_preview = QAction("Capture &overview", self)
        self.action_toggle_preview.setCheckable(True)
        self.action_toggle_preview.setChecked(True)
        self.action_toggle_preview.triggered.connect(self.previewer.setVisible)
        view_menu.addAction(self.action_toggle_preview)
        view_menu.addSeparator()
        analysis_action = QAction("&Analysis", self)
        analysis_action.setStatusTip("Decoders, measure, search, markers and the capture, in the inspector")
        analysis_action.setShortcut(QKeySequence("Ctrl+1"))
        analysis_action.triggered.connect(lambda: self.show_panel(self.decoder_manager))
        view_menu.addAction(analysis_action)
        self.action_listing = listing_action = QAction("&Listing", self)
        listing_action.setCheckable(True)
        listing_action.setShortcut(QKeySequence("Ctrl+2"))
        listing_action.toggled.connect(self.listing_tool_button.setChecked)
        self.listing_tool_button.toggled.connect(listing_action.setChecked)
        view_menu.addAction(listing_action)

    def _apply_menu_icons(self) -> None:
        def visit(menu) -> None:
            for action in menu.actions():
                if action.isSeparator():
                    continue
                name = action.text().replace("&", "").rstrip(".").strip()
                if action.icon().isNull() and not action.isCheckable() and name in self.MENU_ICONS:
                    action.setIcon(icon(self.MENU_ICONS[name]))
                if action.menu() is not None:
                    visit(action.menu())

        for menu in self._menus.menus:
            visit(menu)
