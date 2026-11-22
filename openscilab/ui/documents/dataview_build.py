# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Building the data view: tool bar, capture area, view bar, side panel and their controls.

A part of :class:`~.dataview.DataView` (ARCHITECTURE.md, *Known hotspots*): the data view
combines its parts, which share its state through ``self``. Signals stay declared on the data view.
"""

from __future__ import annotations

import logging
import os

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QScrollBar,
    QSizePolicy,
    QSlider,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ... import __version__
from ..dialogs.common import Banner, hint
from ..icons import icon, set_icon
from ..panels.listing_panel import ListingPanel
from ..panels.markers_panel import MarkersPanel
from ..panels.measure_panel import MeasurePanel
from ..panels.search_panel import SearchPanel
from ..shell.start_page import native
from ..theme import icon_px, set_role, set_variant
from ..view_model import (
    LARGEST_CHANNEL_HEIGHT,
    SMALLEST_CHANNEL_HEIGHT,
)
from ..widgets.analog_viewer import AnalogViewer
from ..widgets.annotation_viewer import AnnotationViewer
from ..widgets.bus_viewer import BusViewer
from ..widgets.channel_viewer import CHANNEL_COLUMN_WIDTH, ChannelViewer
from ..widgets.decoder_manager import DecoderManager
from ..widgets.sample_marker import SampleMarker
from ..widgets.sample_previewer import SamplePreviewer
from ..widgets.sample_viewer import SampleViewer
from .dataview_common import (
    PINNED_SEPARATOR,
    ZOOM_SLIDER_STEPS,
)

log = logging.getLogger(__name__)


class DataViewBuildMixin:
    """Building the data view: tool bar, capture area, view bar, side panel and their controls."""

    # ------------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_toolbar())
        central = self

        banner_holder = QWidget(central)
        banner_layout = QVBoxLayout(banner_holder)
        banner_layout.setContentsMargins(8, 8, 8, 0)
        # A progressive transfer that stopped before the capture was complete.
        self.transfer_banner = Banner("warning", banner_holder)
        self.transfer_banner.button.clicked.connect(self.resume_transfer)
        banner_layout.addWidget(self.transfer_banner)
        self.transfer_banner.setVisible(False)
        # a capture that failed or ended early: shown here, not in a dialog
        self.capture_banner = Banner("error", banner_holder)
        self.capture_banner.button.clicked.connect(self._capture_banner_action)
        banner_layout.addWidget(self.capture_banner)
        self.capture_banner.setVisible(False)
        self._banner_holder = banner_holder
        banner_holder.setVisible(False)
        layout.addWidget(banner_holder)

        # the traces, the listing below them (a pane of the splitter, Ctrl+2)
        self.body_splitter = QSplitter(Qt.Vertical, self)
        self.body_splitter.setChildrenCollapsible(False)
        self.body_splitter.addWidget(self._build_capture_area())
        self.listing_panel = ListingPanel(self.model, self.body_splitter)
        self.body_splitter.addWidget(self.listing_panel)
        self.body_splitter.setStretchFactor(0, 3)
        self.body_splitter.setStretchFactor(1, 1)
        self.listing_panel.setVisible(False)
        layout.addWidget(self.body_splitter, 1)
        # the analysis: decoders, measure, search, markers, the capture – shown in the inspector
        # (until then hidden: a child without a place in the layout would lie over the traces)
        self._build_side_panel().hide()
        self.listing_tool_button.setChecked(False)
        self.listing_tool_button.toggled.connect(self.listing_panel.setVisible)

    def _tool_button(
        self, parent: QWidget, icon_name: str, tip: str, slot=None, checkable: bool = False, text: str = ""
    ) -> QPushButton:
        """A flat icon button of the toolbar or the view bar (``text``: shown when the toolbar shows text)."""
        button = QPushButton(text, parent)
        set_variant(button, "tool")
        set_icon(button, icon_name)
        button.setToolTip(tip)
        button.setCheckable(checkable)
        button.setFocusPolicy(Qt.NoFocus)
        if slot is not None:
            button.clicked.connect(slot)
        return button

    @staticmethod
    def _toolbar_spacer(bar: QToolBar) -> QWidget:
        spacer = QWidget(bar)
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        return spacer

    def _build_toolbar(self) -> QToolBar:
        """One tool bar: capture, the device and its settings, then the panels and the file. It adapts
        to the width of the view (``ui/widgets/toolbar.py``); a right click customizes it."""
        from ..devices.capture_controls import CaptureControls
        from ..widgets.toolbar import HIGH, LOW, NORMAL, AdaptiveToolBar

        bar = AdaptiveToolBar("dataview", "Data view", self)
        self.main_toolbar = bar
        self.capture_controls = controls = CaptureControls(bar, repeat=self.repeat_capture, stop=self.abort_capture)
        self.device_combo = QComboBox(bar)
        self.device_combo.setMinimumWidth(150)
        self.device_combo.setToolTip("The device this view captures with")
        self.device_combo.activated.connect(self._device_chosen)
        # ---------------------------------------------------------- capture
        bar.add_widget("capture", controls.capture_button, "Capture", "capture", HIGH, pinned=True)
        bar.add_widget("again", controls.repeat_button, "Capture again", "capture", NORMAL)
        bar.add_widget("record", controls.record_button, "Record a stream", "capture", NORMAL)
        bar.add_widget("stop", controls.stop_button, "Stop", "capture", HIGH, text=False)
        # ---------------------------------------------------------- device
        bar.add_widget("device", self.device_combo, "Device", "device", HIGH)
        bar.add_widget("quick", controls.quick_capture, "Rate, length and trigger", "device", NORMAL,
                       overflow=lambda menu: menu.addAction(icon("gear"), "Rate, length and trigger...",
                                                            controls.capture_settings))
        bar.add_widget("settings", controls.settings_button, "Capture settings", "device", NORMAL)
        bar.add_widget("profiles", controls.profiles_button, "Profiles", "device", NORMAL)
        bar.add_widget("state", controls.state_label, "Device state", "device", LOW)
        bar.add_stretch()
        # ---------------------------------------------------------- panels
        self.search_tool_button = self._tool_button(
            bar, "search", "Search the capture (Ctrl+F)", lambda: self.show_panel(self.search_panel), text="Search")
        self.measure_tool_button = self._tool_button(
            bar, "ruler", "Cursors and measurements (Ctrl+Shift+M)", lambda: self.show_panel(self.measure_panel),
            text="Measure")
        self.listing_tool_button = self._tool_button(bar, "list", "The listing below the traces (Ctrl+2)",
                                                     checkable=True, text="Listing")
        self.decoders_tool_button = self._tool_button(
            bar, "bus", "Protocol decoders (in the inspector)", lambda: self.show_panel(self.decoder_manager),
            text="Decoders")
        bar.add_widget("decoders", self.decoders_tool_button, "Decoders", "panels", NORMAL)
        bar.add_widget("measure", self.measure_tool_button, "Measure", "panels", NORMAL)
        bar.add_widget("search", self.search_tool_button, "Search", "panels", NORMAL, text=False)
        bar.add_widget("listing", self.listing_tool_button, "Listing", "panels", LOW, text=False)
        # (Open and Save are the shell's: header, File menu, Ctrl+O, Ctrl+S)
        # (the names of the buttons the view always had)
        self.capture_button = controls.capture_button
        self.repeat_button = controls.repeat_button
        self.abort_button = controls.stop_button

        return bar.finish()

    def _fill_device_combo(self) -> None:
        """No device, the connected instruments that capture, and connecting another one."""
        combo = self.device_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(icon("folder"), "No device" if self.model.session is None or self.source is not None
                      else ("File" if self.current_file else "No device (test signals)"), None)
        for instrument in self.hub.instruments():
            if instrument.capture is not None:
                combo.addItem(icon("chip"), instrument.name, instrument)
        combo.addItem(icon("plus"), "Connect a device...", "connect")
        index = combo.findData(self.source.instrument) if self.source is not None else 0
        combo.setCurrentIndex(max(index, 0))
        combo.blockSignals(False)

    def _device_chosen(self, index: int) -> None:
        data = self.device_combo.itemData(index)
        if data == "connect":
            self._fill_device_combo()
            self.devices_requested.emit()
            return
        if data is None:
            if self.source is not None:
                self.detach_source(self.source)
            return
        self.use_instrument(data)

    def set_viewer(self, viewer: bool = True) -> None:
        """A view of data that come from elsewhere - the run data of a flow, a scope node: it shows
        them (search, measure, decode, save) and cannot capture, so the capture tool bar, the device
        and *Open* are not there (the flow would replace what they loaded)."""
        self.viewer = bool(viewer)
        self.main_toolbar.set_available(self.CAPTURE_ENTRIES, not self.viewer)
        for action in (self.action_new, self.action_open, self.action_repeat, self.action_stop,
                       self.action_device_card, self.action_simulation):
            action.setVisible(not self.viewer)
        self._update_actions()
        self.document_changed.emit()  # the header's Capture follows

    def capture(self) -> bool:
        """Capture with the device and the settings of the capture tool bar (F5)."""
        if self.viewer:
            return False
        if self.source is None:
            self.devices_requested.emit()
            self.notify("Choose a device to capture with (or connect one)", 6000)
            return False
        return self.capture_controls.start_capture()

    def _build_capture_area(self) -> QWidget:
        self.capture_stack = QStackedWidget(self)
        self.capture_stack.addWidget(self._build_empty_page())

        container = QWidget(self.capture_stack)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        #: room on the right of the rows outside the scroll area, as wide as its vertical scroll
        #: bar while it shows: every row maps the samples onto the same width (ticks and lines meet)
        self._gutters: list[QWidget] = []

        def with_gutter(widget: QWidget) -> QWidget:
            row = QWidget(container)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(0)
            row_layout.addWidget(widget, 1)
            gutter = QWidget(row)
            gutter.setFixedWidth(0)
            row_layout.addWidget(gutter)
            self._gutters.append(gutter)
            return row

        self.annotation_viewer = AnnotationViewer(self.model, container)
        self.annotation_viewer.row_clicked.connect(self.show_annotation_list)
        layout.addWidget(with_gutter(self.annotation_viewer))

        ruler_row = QWidget(container)
        ruler_layout = QHBoxLayout(ruler_row)
        ruler_layout.setContentsMargins(0, 0, 0, 0)
        ruler_layout.setSpacing(0)

        show_all_button = QPushButton("Show all", ruler_row)
        set_icon(show_all_button, "eye")
        show_all_button.setFixedWidth(CHANNEL_COLUMN_WIDTH)
        show_all_button.setToolTip("Hidden channels: show them again (all or one by one)")
        # the hidden channels, to show again one by one or all at once
        self.hidden_menu = QMenu(show_all_button)
        self.hidden_menu.aboutToShow.connect(self._fill_hidden_menu)
        show_all_button.setMenu(self.hidden_menu)
        self.show_all_button = show_all_button
        ruler_layout.addWidget(show_all_button)

        self.sample_marker = SampleMarker(self.model, ruler_row)
        self.sample_marker.setToolTip(
            "Drag to select samples, right-click for editing, measuring and regions"
        )
        ruler_layout.addWidget(self.sample_marker, 1)
        ruler_gutter = QWidget(ruler_row)
        ruler_gutter.setFixedWidth(0)
        ruler_layout.addWidget(ruler_gutter)
        self._gutters.append(ruler_gutter)
        layout.addWidget(ruler_row)

        # Buses and groups: with the channels, below the time axis
        self.bus_viewer = BusViewer(self.model, container)
        self.bus_viewer.edit_requested.connect(self.edit_bus)
        layout.addWidget(with_gutter(self.bus_viewer))

        # Pinned channels: outside the scroll area, so they stay in view.
        self.pinned_area = QWidget(container)
        pinned_layout = QHBoxLayout(self.pinned_area)
        pinned_layout.setContentsMargins(0, 0, 0, 0)
        pinned_layout.setSpacing(0)
        self.pinned_channel_viewer = ChannelViewer(self.model, self.pinned_area, section="pinned")
        pinned_layout.addWidget(self.pinned_channel_viewer)
        self.pinned_sample_viewer = SampleViewer(self.model, self.pinned_area, section="pinned")
        self.pinned_sample_viewer.setMinimumHeight(0)
        pinned_layout.addWidget(self.pinned_sample_viewer, 1)
        pinned_gutter = QWidget(self.pinned_area)
        pinned_gutter.setFixedWidth(0)
        pinned_layout.addWidget(pinned_gutter)
        self._gutters.append(pinned_gutter)
        layout.addWidget(self.pinned_area)
        self.pinned_separator = QFrame(container)
        self.pinned_separator.setFixedHeight(PINNED_SEPARATOR)
        set_role(self.pinned_separator, "accent-line")
        layout.addWidget(self.pinned_separator)
        self.pinned_area.setVisible(False)
        self.pinned_separator.setVisible(False)

        self.scroll_area = QScrollArea(container)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setFrameShape(QFrame.NoFrame)

        waveform_container = QWidget(self.scroll_area)
        container_layout = QVBoxLayout(waveform_container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)
        digital_rows = QWidget(waveform_container)
        waveform_layout = QHBoxLayout(digital_rows)
        waveform_layout.setContentsMargins(0, 0, 0, 0)
        waveform_layout.setSpacing(0)

        self.channel_viewer = ChannelViewer(self.model, digital_rows, section="scrolling")
        waveform_layout.addWidget(self.channel_viewer)

        self.sample_viewer = SampleViewer(self.model, digital_rows, section="scrolling")
        waveform_layout.addWidget(self.sample_viewer, 1)
        # the rows start at the top with their height (Taller/Shorter channels); room below stays free
        self.digital_rows = digital_rows
        container_layout.addWidget(digital_rows)

        # Analog channels: tracks below the digital ones, on the same time axis.
        self.analog_viewer = AnalogViewer(self.model, waveform_container)
        self.analog_viewer.derive_requested.connect(self.derive_digital_channel)
        container_layout.addWidget(self.analog_viewer)
        container_layout.addStretch(1)
        for signal in (self.model.capture_changed, self.model.channels_changed, self.model.channel_height_changed):
            signal.connect(self._fit_digital_rows)
        self.model.capture_changed.connect(
            lambda: digital_rows.setVisible(bool(self.model.channels) or not self.model.analog_channels))

        self.scroll_area.setWidget(waveform_container)
        layout.addWidget(self.scroll_area, 1)
        self.scroll_area.verticalScrollBar().installEventFilter(self)

        layout.addWidget(self._build_view_bar(container))

        self.previewer = SamplePreviewer(self.model, container)
        layout.addWidget(self.previewer)

        self.position_scrollbar = QScrollBar(Qt.Horizontal, container)
        self.position_scrollbar.valueChanged.connect(self._on_scrollbar_moved)
        layout.addWidget(self.position_scrollbar)

        self.sample_marker.copy_requested.connect(self.copy_samples)
        self.sample_marker.cut_requested.connect(self.cut_samples)
        self.sample_marker.paste_requested.connect(self.paste_samples)
        self.sample_marker.insert_requested.connect(self.insert_samples_dialog)
        self.sample_marker.delete_requested.connect(self.delete_samples)
        self.sample_marker.measure_requested.connect(self.measure_samples)
        self.sample_marker.shift_requested.connect(self.shift_channels)
        self.sample_marker.create_region_requested.connect(self.create_region)
        self.sample_marker.delete_region_requested.connect(self.model.remove_region)
        self.model.channels_changed.connect(self._update_pinned_area)
        self.model.capture_changed.connect(self._update_pinned_area)
        self.model.channel_height_changed.connect(self._update_pinned_area)

        self.capture_stack.addWidget(container)
        return self.capture_stack

    def _build_empty_page(self) -> QWidget:
        """Shown while there is no capture: explains the three ways to get one."""
        page = QWidget(self)
        outer = QVBoxLayout(page)
        outer.addStretch(2)

        card = QFrame(page)
        set_role(card, "card")
        card.setMaximumWidth(560)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(28, 24, 28, 24)
        card_layout.setSpacing(10)

        self.empty_title = QLabel("No data", card)
        set_role(self.empty_title, "title")
        card_layout.addWidget(self.empty_title)
        self.empty_hint = hint("", card)
        card_layout.addWidget(self.empty_hint)
        card_layout.addSpacing(6)

        # two lines of buttons: the empty page fits beside a device card
        buttons = QGridLayout()
        buttons.setSpacing(8)
        self.empty_primary_button = QPushButton("Devices...", card)
        set_variant(self.empty_primary_button, "primary")
        set_icon(self.empty_primary_button, "devices")
        self.empty_primary_button.clicked.connect(self._empty_page_primary_action)
        buttons.addWidget(self.empty_primary_button, 0, 0, 1, 2)
        open_button = QPushButton("Open capture...", card)
        set_icon(open_button, "folder")
        open_button.clicked.connect(self.open_capture)
        buttons.addWidget(open_button, 1, 0)
        simulate_button = QPushButton("Simulated signals...", card)
        set_icon(simulate_button, "wave")
        simulate_button.clicked.connect(self.simulated_capture)
        buttons.addWidget(simulate_button, 1, 1)
        card_layout.addLayout(buttons)

        card_layout.addSpacing(6)
        card_layout.addWidget(
            hint("  ·  ".join(f"{native(keys)} {what}" for keys, what in (
                ("Ctrl+O", "open"), ("Ctrl+0", "zoom to fit"), ("Ctrl+T", "go to trigger"), ("Ctrl+F", "find"))), card)
        )

        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(card, 3)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(3)
        return page

    def _build_view_bar(self, parent: QWidget) -> QToolBar:
        """Zoom and channel height, below the waveform; it adapts to the width like the tool bar."""
        from ..widgets.toolbar import HIGH, LOW, NORMAL, AdaptiveToolBar

        bar = AdaptiveToolBar("dataview-view", "View", parent)
        self.view_toolbar = bar
        bar.setIconSize(QSize(icon_px(14), icon_px(14)))
        self.fit_button = self._tool_button(bar, "fit", "Show the whole capture (Ctrl+0)", self.model.zoom_to_fit,
                                            text="Fit")
        bar.add_widget("fit", self.fit_button, "Show the whole capture", "fit", HIGH, text=False)
        self.fit_selection_button = QPushButton("Fit selection", bar)
        set_variant(self.fit_selection_button, "tool")
        set_icon(self.fit_selection_button, "fit")
        self.fit_selection_button.setToolTip("Show the samples selected on the time axis (Ctrl+E)")
        self.fit_selection_button.clicked.connect(self.model.zoom_to_selection)
        self.fit_selection_button.setEnabled(False)
        self.model.marker_changed.connect(
            lambda: self.fit_selection_button.setEnabled(self.model.selection is not None))
        bar.add_widget("fit-selection", self.fit_selection_button, "Fit selection", "fit", NORMAL)
        self.trigger_button = self._tool_button(bar, "target", "Center the view on the trigger (Ctrl+T)",
                                                self.go_to_trigger, text="Trigger")
        bar.add_widget("trigger", self.trigger_button, "Center on the trigger", "fit", NORMAL, text=False)
        # Roll mode of a live capture: the view runs with the newest samples, or holds still.
        self.follow_button = QPushButton("Roll", bar)
        set_variant(self.follow_button, "tool")
        self.follow_button.setCheckable(True)
        self.follow_button.setChecked(True)
        set_icon(self.follow_button, "play")
        self.follow_button.setToolTip("Live: the view follows the newest samples; off holds the view still "
                                      "while the capture goes on")
        self.follow_button.toggled.connect(self._follow_toggled)
        bar.add_widget("roll", self.follow_button, "Roll (live)", "fit", HIGH)
        # State captures with times: one column per state, or the states on their real time.
        self.state_time_button = QPushButton("Real time", bar)
        set_variant(self.state_time_button, "tool")
        self.state_time_button.setCheckable(True)
        set_icon(self.state_time_button, "clock")
        self.state_time_button.setToolTip("State capture: show the states on their real time instead of "
                                          "one per state")
        self.state_time_button.toggled.connect(self.set_state_real_time)
        bar.add_widget("real-time", self.state_time_button, "States on their real time", "fit", HIGH)
        for key in ("roll", "real-time"):  # (only while a capture streams, for a state capture with times)
            bar.item(key).available = False

        zoom_out = self._tool_button(bar, "zoom-out", "Zoom out (-)", lambda: self.model.zoom(2.0), text="Zoom out")
        bar.add_widget("zoom-out", zoom_out, "Zoom out", "zoom", HIGH, text=False)
        self.zoom_slider = QSlider(Qt.Horizontal, bar)
        self.zoom_slider.setRange(0, ZOOM_SLIDER_STEPS)
        self.zoom_slider.setMinimumWidth(80)
        self.zoom_slider.setMaximumWidth(180)
        self.zoom_slider.setToolTip("Samples shown on screen")
        self.zoom_slider.valueChanged.connect(self._on_zoom_slider)
        bar.add_widget("zoom-slider", self.zoom_slider, "Zoom slider", "zoom", NORMAL,
                       overflow=lambda menu: None)
        zoom_in = self._tool_button(bar, "zoom-in", "Zoom in (+)", lambda: self.model.zoom(0.5), text="Zoom in")
        bar.add_widget("zoom-in", zoom_in, "Zoom in", "zoom", HIGH, text=False)
        self.visible_samples_label = QLabel("-", bar)
        set_role(self.visible_samples_label, "hint")
        bar.add_widget("samples", self.visible_samples_label, "Samples on screen", "zoom", LOW)
        # what goes on now: a transfer, a stream, waiting for the trigger (no status bar of its own)
        self.status_label = QLabel("", bar)
        set_role(self.status_label, "hint")
        self.status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        bar.add_widget("status", self.status_label, "What goes on now", "zoom", LOW)
        bar.add_stretch()

        rows = QLabel(bar)
        rows.setPixmap(icon("rows").pixmap(14, 14))
        rows.setToolTip("Height of the channels")
        self.channel_height_slider = QSlider(Qt.Horizontal, bar)
        self.channel_height_slider.setRange(SMALLEST_CHANNEL_HEIGHT, LARGEST_CHANNEL_HEIGHT)
        self.channel_height_slider.setValue(self.model.channel_height)
        self.channel_height_slider.setMinimumWidth(60)
        self.channel_height_slider.setMaximumWidth(120)
        self.channel_height_slider.setToolTip(
            "Height of the channels: lower channels fit more of them on the screen "
            "(Alt + mouse wheel, Ctrl+Shift+Up/Down)"
        )
        self.channel_height_slider.valueChanged.connect(self.model.set_channel_height)
        self.channel_height_label = QLabel(f"{self.model.channel_height} px", bar)
        self.channel_height_label.setMinimumWidth(self.channel_height_label.fontMetrics().horizontalAdvance("000 px"))
        set_role(self.channel_height_label, "hint")
        height_box = QWidget(bar)
        height_layout = QHBoxLayout(height_box)
        height_layout.setContentsMargins(0, 0, 0, 0)
        height_layout.setSpacing(4)
        for widget in (rows, self.channel_height_slider, self.channel_height_label):
            height_layout.addWidget(widget)
        bar.add_widget("height", height_box, "Height of the channels", "height", LOW, overflow=self._height_menu)
        self.model.channel_height_changed.connect(self._sync_channel_height)
        return bar.finish()

    def _height_menu(self, menu: QMenu) -> None:
        """The heights of the channels, in the *more* menu of the view bar."""
        sub = menu.addMenu(icon("rows"), "Height of the channels")
        for height in sorted({SMALLEST_CHANNEL_HEIGHT, 16, 24, 32, 48, 64, 96, LARGEST_CHANNEL_HEIGHT}):
            if not SMALLEST_CHANNEL_HEIGHT <= height <= LARGEST_CHANNEL_HEIGHT:
                continue
            action = sub.addAction(f"{height} px")
            action.setCheckable(True)
            action.setChecked(height == self.model.channel_height)
            action.triggered.connect(lambda _checked=False, height=height: self.model.set_channel_height(height))

    def set_state_real_time(self, checked: bool) -> None:
        """A state capture with times: show the states on their real time (a timing view of them)
        or one per state."""
        from ...core.state_mode import states_to_timing

        if checked:
            session = self.model.session
            if session is None or session.state_times is None or len(session.state_times) < 2:
                self.state_time_button.setChecked(False)
                return
            # Another view of the same capture, not other data: its regions and markers (at
            # state numbers, which are no positions in time) wait for the way back, and the file
            # still holds the states.
            self._state_session = (session, list(self.model.regions),
                                   [(bookmark.sample, bookmark.name) for bookmark in self.model.bookmarks])
            self._loading += 1
            try:
                self.model.set_regions([])
                self.model.set_session(states_to_timing(session))
            finally:
                self._loading -= 1
            self.model.zoom_to_fit()
            self.notify("States on their real time", 5000)
        else:
            stored = getattr(self, "_state_session", None)
            self._state_session = None
            if stored is not None:
                self._loading += 1
                try:
                    self.model.set_session(stored[0])
                    self.model.set_regions(stored[1])
                    self.model.set_bookmarks(stored[2])
                finally:
                    self._loading -= 1
                self._reset_undo()
                self.model.zoom_to_fit()
        self._sync_view_controls()
        self._update_info_panel()

    def _follow_toggled(self, checked: bool) -> None:
        set_icon(self.follow_button, "play" if checked else "pause")
        if checked and self.model.is_live:
            count = self.model.sample_count
            self.model.set_view(count - self._live_window, self._live_window)

    def _fit_digital_rows(self) -> None:
        """The scrolling rows as tall as their channels (they do not stretch to fill the view)."""
        count = len(self.model.section_channels("scrolling"))
        self.digital_rows.setFixedHeight(max(count, 1) * self.model.channel_height)

    def _update_pinned_area(self) -> None:
        count = len(self.model.section_channels("pinned"))
        self.pinned_area.setFixedHeight(count * self.model.channel_height)
        self.pinned_area.setVisible(count > 0)
        self.pinned_separator.setVisible(count > 0)
        self.pinned_sample_viewer.update()

    def _sync_channel_height(self) -> None:
        height = self.model.channel_height
        if self.channel_height_slider.value() != height:
            self.channel_height_slider.blockSignals(True)
            self.channel_height_slider.setValue(height)
            self.channel_height_slider.blockSignals(False)
        self.channel_height_label.setText(f"{height} px")

    def _build_side_panel(self) -> QWidget:
        self.side_tabs = QTabWidget(self)
        self.side_tabs.setObjectName("side-tabs")
        self.side_tabs.setDocumentMode(True)
        # five tabs in the width of the inspector: none may be cut off and lost
        self.side_tabs.setUsesScrollButtons(True)
        self.side_tabs.setElideMode(Qt.ElideNone)
        self.side_tabs.tabBar().setExpanding(False)
        self.side_tabs.setMinimumWidth(240)

        def page(widget: QWidget) -> QScrollArea:
            holder = QScrollArea(self.side_tabs)
            holder.setWidgetResizable(True)
            holder.setFrameShape(QFrame.NoFrame)
            inner = QWidget(holder)
            inner_layout = QVBoxLayout(inner)
            inner_layout.setContentsMargins(8, 8, 8, 8)
            widget.setParent(inner)
            inner_layout.addWidget(widget, 1)
            holder.setWidget(inner)
            return holder

        self.decoder_manager = DecoderManager(self.model, self.provider, self.side_tabs)
        self.side_tabs.addTab(page(self.decoder_manager), "Decoders")
        self.measure_panel = MeasurePanel(self.model, self.side_tabs)
        self.side_tabs.addTab(page(self.measure_panel), "Measure")
        self.search_panel = SearchPanel(self.model, self.side_tabs)
        self.side_tabs.addTab(page(self.search_panel), "Search")
        self.markers_panel = MarkersPanel(self.model, self.side_tabs)
        self.side_tabs.addTab(page(self.markers_panel), "Markers")

        panel = QWidget(self.side_tabs)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        info_box = QFrame(panel)
        set_role(info_box, "card")
        info_layout = QVBoxLayout(info_box)
        info_layout.setContentsMargins(10, 8, 10, 8)
        info_title = QLabel("Capture", info_box)
        set_role(info_title, "heading")
        info_layout.addWidget(info_title)
        self.info_label = QLabel("No capture loaded", info_box)
        self.info_label.setTextFormat(Qt.RichText)
        self.info_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        info_layout.addWidget(self.info_label)
        layout.addWidget(info_box)
        next_box = QFrame(panel)
        set_role(next_box, "card")
        next_layout = QVBoxLayout(next_box)
        next_layout.setContentsMargins(10, 8, 10, 8)
        next_title = QLabel("Next capture", next_box)
        set_role(next_title, "heading")
        next_layout.addWidget(next_title)
        next_layout.addWidget(self.capture_controls.summary_label)
        next_layout.addWidget(self.capture_controls.channel_table, 1)
        layout.addWidget(next_box, 1)

        self.side_tabs.addTab(page(panel), "Capture")
        return self.side_tabs

    def _update_title(self) -> None:
        name = f"openSciLab {__version__}"
        if self.current_file:
            self.setWindowTitle(f"{os.path.basename(self.current_file)} — {name}")
        else:
            self.setWindowTitle(name)
        self.document_changed.emit()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt naming
        if watched is self.scroll_area.verticalScrollBar() and event.type() in (
                QEvent.Show, QEvent.Hide, QEvent.Resize):
            self._sync_gutters()
        return super().eventFilter(watched, event)

    def _sync_gutters(self) -> None:
        """The rows above the tracks leave room for the tracks' scroll bar (same sample width)."""
        bar = self.scroll_area.verticalScrollBar()
        width = bar.width() if bar.isVisible() else 0
        for gutter in self._gutters:
            if gutter.width() != width:
                gutter.setFixedWidth(width)
