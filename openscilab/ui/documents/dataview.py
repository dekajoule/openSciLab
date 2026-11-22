# Copyright (C) 2026 Julian Decker
# Copyright (C) Agustín Giménez Bernad (gusmanb), original LogicAnalyzer
#
# Part of openSciLab, a port and extension of his software;
# the changes are described in docs/improvements.md and CHANGELOG.md.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The data view: captures shown, analysed, decoded and edited (port of ``MainWindow.axaml(.cs)``,
the main window of the logic analyzer it grew out of).

It is a document of the shell like the others (docs/design.md, rule 7): its tool bar on top (capture,
the device and its rate, length and trigger, the panels), the traces with the overview, the listing
below them (*Listing*, Ctrl+2), and its analysis – decoders, measure, search, markers, the capture –
in the shell's inspector. Its menus (*Data*, *Analyze*, *Display*) join the shell's; *Open*, *Save*
and the status bar are the shell's (:attr:`message`); what goes on now (a transfer, a stream) is
written in the bar below the traces.

The captures of a device arrive here – live, progressively or when they complete – from its
:class:`~..devices.capture.CaptureController`; *Run* in the header (F5) captures. Files and test
signals are shown the same way.
"""

from __future__ import annotations

import html
import logging
import math
import os
from typing import Optional

import numpy as np
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction, QUndoStack
from PySide6.QtWidgets import (  # noqa: F401 - QFileDialog: patched by tests
    QFileDialog,
    QToolBar,
    QWidget,
)

from ...core import settings
from ...core.formatting import to_large_frequency, to_small_time, to_thousands
from ...core.hub import Hub
from ...driver.base import (
    CaptureProgressArgs,
)
from ...driver.models import CaptureSession, TriggerType
from ...sigrok.provider import SigrokProvider
from .. import messages
from ..devices.capture import CaptureBridge
from ..devices.hub_bridge import HubBridge
from ..dialogs.annotation_list import AnnotationListWindow
from ..icons import set_icon
from ..theme import TEXT_MUTED
from ..view_model import (
    CaptureViewModel,
)
from .base import Document
from .dataview_build import DataViewBuildMixin
from .dataview_capture import DataViewCaptureMixin
from .dataview_common import (  # noqa: F401 - COMPATIBLE_FILTER: known from here before the split
    COMPATIBLE_FILTER,
    WINDOW_STATE_FILE,
    ZOOM_SLIDER_STEPS,
)
from .dataview_files import DataViewFilesMixin
from .dataview_menus import DataViewMenusMixin
from .dataview_samples import DataViewSamplesMixin
from .dataview_source import DataViewSourceMixin

log = logging.getLogger(__name__)


class DataView(DataViewBuildMixin, DataViewMenusMixin, DataViewSourceMixin, DataViewCaptureMixin,
               DataViewFilesMixin, DataViewSamplesMixin, QWidget, Document):
    """The data view: captures of a device or a file, their analysis and editing.

    Its parts live in modules of their own (``dataview_build``, ``dataview_menus``, ``dataview_source``,
    ``dataview_capture``, ``dataview_files``, ``dataview_samples``); this class holds its signals,
    builds it and keeps its document contract (``documents/base.py``), its view state and run state."""

    document_kind = "data"
    #: Title, file or unsaved state changed (see :class:`~.base.Document`).
    document_changed = Signal()
    #: a waveform made of the capture, to play on a generator output (``Play as signal``)
    play_requested = Signal(object)
    #: the device card of the instrument the data come from
    device_card_requested = Signal(object)
    #: the device list (to open a device and capture with it)
    devices_requested = Signal()
    #: a short note for the status bar of the shell
    message = Signal(str)
    #: a capture started or ended (the header's Run, Stop)
    run_state_changed = Signal(str)
    #: show the inspector (the analysis of the data view is there)
    inspector_requested = Signal()

    def __init__(
        self,
        decoder_paths: tuple[str, ...] = (),
        provider: Optional[SigrokProvider] = None,
        hub: Optional[Hub] = None,
    ) -> None:
        super().__init__()
        #: The device hub of the lab
        self.hub = hub if hub is not None else Hub()
        #: The capture controller of the instrument whose captures this view shows (``None`` for
        #: files and test signals); the device card starts the captures.
        self.source = None
        #: Title of a capture that is not a file (e.g. shown by a scope node of a flow)
        self.display_name: Optional[str] = None
        #: the data come from elsewhere (the run data of a flow, a scope node): the view shows them
        #: and does not capture (see set_viewer)
        self.viewer = False
        #: the last note for the status bar (or of what goes on now, in the bar below the traces)
        self._last_message = ""
        self._dirty = False
        #: changed by the user since it was loaded, captured or saved (a new capture then opens
        #: in another data view instead of replacing the changes)
        self._edited = False
        #: loading data: the model's change signals are not edits then
        self._loading = 0
        #: a file is being read or written in the background (no second one meanwhile)
        self._file_busy = False
        #: the open file is written as the original software does (chosen in *Save as*)
        self._save_compatible = False
        #: Number of successful saves (tells save()/save_as() whether the user saved).
        self._save_count = 0

        self.model = CaptureViewModel(self)
        for signal in (self.model.channels_changed, self.model.regions_changed, self.model.bookmarks_changed,
                       self.model.buses_changed):
            signal.connect(self._model_edited)
        # other data in the model (from anywhere): what it announces right then is no edit
        self._data_session = None
        self.model.capture_changed.connect(self._data_replaced)
        self.model.channels_changed.connect(lambda: hasattr(self, "show_all_button") and self._update_hidden_button())
        #: edits of the data, undone with Ctrl+Z (see data_edits.py)
        self.undo = QUndoStack(self)
        self._state = None
        self.undo.indexChanged.connect(self._undo_moved)
        #: Open annotation list windows by (decoder instance, row name).
        self._annotation_lists: dict[tuple[int, str], AnnotationListWindow] = {}
        self.provider = provider if provider is not None else SigrokProvider()
        if decoder_paths and provider is None:
            self.provider.registry.search_paths = list(decoder_paths) + [
                path for path in self.provider.registry.search_paths if path not in decoder_paths
            ]
        #: Settings of the simulated capture in progress (decoders to add).
        self._pending_simulation: Optional[dict] = None
        self.clipboard_samples: Optional[list[np.ndarray]] = None
        self.current_file: Optional[str] = None
        self._updating_view_controls = False
        #: (session, samples before any alignment by channel id) to align again with another method.
        self._unaligned: Optional[tuple[CaptureSession, dict[int, np.ndarray]]] = None
        #: (session, how its boards were aligned) for the capture information.
        self._alignment_report: Optional[tuple[CaptureSession, str]] = None
        #: (session, regions, file) the shown state analysis was made of
        self._timing_capture: Optional[tuple] = None

        self.hub_bridge = HubBridge(self.hub, self)
        self.hub_bridge.changed.connect(self._on_hub_event)
        # test signals computed on this computer complete through it (captures of instruments
        # arrive through their capture controller)
        self.bridge = CaptureBridge(self)
        self.bridge.completed.connect(self._on_capture_completed)
        #: the display asks a progressive transfer for the visible range (after scrolling settles)
        self._priority_timer = QTimer(self)
        self._priority_timer.setSingleShot(True)
        self._priority_timer.setInterval(80)
        self._priority_timer.timeout.connect(self._prioritize_visible)
        #: The newest progress event not shown yet: a slow display skips the older ones
        self._pending_progress: Optional[CaptureProgressArgs] = None
        #: The capture started from this window until its completion was handled.
        self._running_capture: Optional[CaptureSession] = None
        #: The capture streaming in (live display): shown session and the one the driver fills.
        self._live_session: Optional[CaptureSession] = None
        self._live_source: Optional[CaptureSession] = None
        #: Samples the live display shows, and what it last set (to notice the user zooming).
        self._live_window = 0
        self._live_visible = 0
        #: stream position of the first sample shown (an endless stream drops the oldest ones)
        self._live_first = 0
        #: actions on devices while a capture runs: (stream sample or None, monotonic time, text,
        #: the marker shown live or None)
        self._action_marks: list[tuple] = []
        self._capture_started = 0.0
        self._live_arrival = 0.0
        #: a monitor recorded into this document (see :meth:`record_monitor`)
        self.recording = None
        self._recording_session = None
        self._recording_marks: list[tuple[int, str]] = []
        self._recording_stop = None
        self._recording_timer = QTimer(self)
        self._recording_timer.setInterval(200)
        self._recording_timer.timeout.connect(self._show_recording)

        self._build_ui()
        self._build_menu()
        self._apply_menu_icons()

        self.model.view_changed.connect(self._sync_view_controls)
        self.model.view_changed.connect(lambda: self.model.progressive is not None and self._priority_timer.start())
        self.model.capture_changed.connect(self._on_capture_changed)
        self.model.samples_appended.connect(self._sync_view_controls)

        self._update_title()
        self._update_actions()
        self._update_info_panel()
        self._sync_view_controls()
        self._restore_window_state()
        self._embed()

    # ---------------------------------------------------------- dock layout
    def _restore_window_state(self) -> None:
        """Restore the dock layout, overview and channel height of the last document."""
        state = settings.get_settings(WINDOW_STATE_FILE)
        if not isinstance(state, dict):
            return
        try:
            if state.get("listing"):
                self.listing_panel.setVisible(True)
            if "preview" in state:
                # The pinned state of the preview is persisted since V6_5.
                visible = bool(state["preview"])
                self.action_toggle_preview.setChecked(visible)
                self.previewer.setVisible(visible)
            if "channel_height" in state:
                self.model.set_channel_height(int(state["channel_height"]))
        except (TypeError, ValueError):
            return

    def _save_window_state(self) -> None:
        settings.persist_settings(
            WINDOW_STATE_FILE,
            {
                "listing": not self.listing_panel.isHidden(),
                "preview": self.action_toggle_preview.isChecked(),
                "channel_height": self.model.channel_height,
            },
        )

    #: entries of the tool bar for capturing: not in a view of data that come from elsewhere
    CAPTURE_ENTRIES = ("capture", "again", "record", "stop", "device", "quick", "settings", "profiles", "state")

    #: Icons of the menu entries that do not set their own (by their text without "&" and "...")
    MENU_ICONS = {
        "Align boards": "align",
        "Find next": "down",
        "Find previous": "up",
        "Zoom in": "zoom-in",
        "Zoom out": "zoom-out",
        "Zoom to fit": "fit",
        "Go to trigger": "target",
        "Taller channels": "rows",
        "Shorter channels": "rows",
        "Default channel height": "reset",
        "Navigate": "arrow-right",
        "Show all channels": "eye",
        "Unpin all channels": "pin",
        "Export": "export",
    }

    # ------------------------------------------------------------- document
    def _embed(self) -> None:
        """A document tab: the shell shows the menus and owns Open and Save."""
        # The shell has these shortcuts and passes them on to the active document.
        for action in (self.action_open, self.action_save, self.action_save_as):
            action.setShortcuts([])
        self.view_menu.setTitle("Di&splay")  # "Waveform" is a kind of document of its own
        # keys without Ctrl/Cmd (arrows, +, -) only act while the data view has the focus: they
        # must not scroll the capture while typing in the sidebar or a tree
        for action in self.findChildren(QAction):
            if any(sequence.count() and not (sequence[0].keyboardModifiers() & (
                    Qt.ControlModifier | Qt.MetaModifier | Qt.AltModifier))
                   and not sequence.toString().startswith(("F", "Shift+F", "Del", "Esc"))
                   for sequence in action.shortcuts()):
                action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
                self.addAction(action)

    def _set_dirty(self, dirty: bool) -> None:
        self._edited = dirty
        if dirty != self._dirty:
            self._dirty = dirty
            self.document_changed.emit()

    def _data_replaced(self) -> None:
        if self.model.session is self._data_session:
            return  # the same data changed (an edit), not other data
        self._data_session = self.model.session
        # a selection in the ruler belongs to the data before: cutting or deleting must not act
        # on those positions in the new data
        self.sample_marker.clear_selection()
        self._loading += 1
        QTimer.singleShot(0, self._data_settled)
        self._edited = False
        self._reset_undo()

    def _reset_undo(self) -> None:
        """New data: nothing to undo, the state to compare edits with is this one."""
        from .data_edits import take, undo_limit

        self.undo.clear()
        self.undo.setUndoLimit(undo_limit(self.model.sample_count, len(self.model.channels)))
        self._state = take(self)

    def record_edit(self, text: str) -> None:
        """An edit happened: one undo step from the state before it to the state now."""
        from .data_edits import DataEdit, take

        after = take(self)
        if after is None:
            return
        before = self._state if self._state is not None else after
        self._state = after
        self.undo.push(DataEdit(self, text, before, after))

    def _data_settled(self) -> None:
        self._loading = max(self._loading - 1, 0)

    def _model_edited(self) -> None:
        """Names, colours, regions, markers or buses changed: saved in the file, so unsaved now."""
        if not self._loading and self.model.session is not None:
            self._set_dirty(True)
            self.record_edit("Change channels, markers or regions")

    def undo_stack(self):
        return self.undo

    @property
    def has_edits(self) -> bool:
        """The user changed the data since it was loaded, captured or saved."""
        return self._edited and self.model.session is not None

    def confirm_replace(self, title: str) -> bool:
        """Before other data replace unsaved data: save, discard or cancel."""
        if not self._dirty or self.model.session is None:
            return True
        choice = messages.choose(self, title, f"{self.title} has unsaved data. Save it first?",
                                 ["Save", "Discard"])
        if choice == 0:
            saves = self._save_count
            self.save_capture()
            return self._save_count > saves
        return choice == 1

    @property
    def title(self) -> str:
        if self.current_file:
            return os.path.basename(self.current_file)
        if self.display_name:
            return self.display_name
        if self.source is not None:
            return f"Data · {self.source.instrument.name}"  # the device these data come from
        return "Capture" if self.model.session is not None else "Data view"

    @property
    def path(self) -> Optional[str]:
        return self.current_file

    @property
    def dirty(self) -> bool:
        return self._dirty

    def document_menus(self) -> list:
        return [self.data_menu, self.analyze_menu, self.view_menu]

    def document_file_actions(self) -> list:
        return [self.action_new, self.export_menu.menuAction()]

    def toolbar(self) -> Optional[QToolBar]:
        return self.main_toolbar

    def views(self) -> list[str]:
        return ["Analyzer"]

    def inspector_widget(self, selection=None) -> Optional[QWidget]:
        """The analysis of the data view: decoders, measure, search, markers, the capture."""
        return self.side_tabs

    def _update_inspector(self) -> None:
        """(The capture tab of the analysis shows the file, the device and the figures itself.)"""

    # ---------------------------------------------------------- messages
    def notify(self, text: str, timeout: int = 0) -> None:
        """A note for the status bar of the shell (saved, exported, ...)."""
        self._last_message = text
        if text:
            self.message.emit(text)

    def show_status(self, text: str) -> None:
        """What goes on now (a transfer, a stream, waiting for the trigger), in the bar below the traces."""
        self._last_message = text
        self.status_label.setText(text)
        self.status_label.setToolTip(text)

    @property
    def last_message(self) -> str:
        """The last note or state the view gave (for the tests and the console)."""
        return self._last_message

    def can_save(self) -> bool:
        return self.model.session is not None and self.model.sample_count > 0

    def save(self) -> bool:
        count = self._save_count
        self.save_capture()
        return self._save_count > count

    def save_as(self) -> bool:
        count = self._save_count
        self.save_capture_as()
        return self._save_count > count

    def shutdown(self) -> None:
        self.decoder_manager.shutdown()
        self.stop_recording()
        self._save_window_state()
        if self.source is not None:
            self.detach_source(self.source)  # the device stays open in the hub
        self.hub_bridge.close()

    # ------------------------------------------------------------ view state
    def _on_capture_changed(self) -> None:
        self._sync_view_controls()
        self._update_info_panel()
        self._update_actions()

    def _sync_view_controls(self) -> None:
        if self._updating_view_controls:
            return
        from .. import accessibility

        model = self.model
        last = model.first_sample + model.visible_samples
        accessibility.describe(
            self.sample_viewer, "Waveform",
            f"{len(model.visible_channels)} channels; samples {model.first_sample:,} to {last:,} of "
            f"{model.sample_count:,}" if model.session is not None else "No data")
        self._updating_view_controls = True
        try:
            total = self.model.sample_count
            visible = self.model.visible_samples

            self.position_scrollbar.setRange(0, max(total - visible, 0))
            self.position_scrollbar.setPageStep(max(visible, 1))
            self.position_scrollbar.setSingleStep(max(visible // 10, 1))
            self.position_scrollbar.setValue(self.model.first_sample)

            self.zoom_slider.setValue(self._zoom_to_slider(visible))

            if total:
                duration = visible / max(self.model.frequency, 1)
                self.visible_samples_label.setText(
                    f"{to_thousands(visible)} samples on screen ({to_small_time(duration)})"
                )
            else:
                self.visible_samples_label.setText("No capture")
        finally:
            self._updating_view_controls = False

    def _zoom_to_slider(self, visible: int) -> int:
        maximum = max(self.model.max_visible_samples(), 4)
        if maximum <= 4:
            return 0
        ratio = math.log(max(visible, 4) / 4) / math.log(maximum / 4)
        return int(round(ratio * ZOOM_SLIDER_STEPS))

    def _slider_to_zoom(self, value: int) -> int:
        maximum = max(self.model.max_visible_samples(), 4)
        return int(round(4 * (maximum / 4) ** (value / ZOOM_SLIDER_STEPS)))

    def _on_zoom_slider(self, value: int) -> None:
        if self._updating_view_controls:
            return
        self.model.set_view(self.model.first_sample, self._slider_to_zoom(value))

    def _on_scrollbar_moved(self, value: int) -> None:
        if self._updating_view_controls:
            return
        self.model.scroll_to(value)

    def go_to_trigger(self) -> None:
        session = self.model.session
        if session is None:
            return
        self.model.center_on(session.pre_trigger_samples)

    # ------------------------------------------- the header's Start/Stop
    can_pause = False
    run_label = "Capture"
    run_tooltip = "Capture with the device and the settings of the capture tool bar (F5)"

    @property
    def runnable(self) -> bool:
        return self.source is not None and not self.viewer

    @property
    def can_start_run(self) -> bool:
        return self.source is not None

    @property
    def run_state(self) -> str:
        return "running" if self.is_capturing else "idle"

    def start_run(self) -> bool:
        return self.capture()

    def pause_run(self) -> None:
        pass

    def stop_run(self) -> None:
        self.abort_capture()

    # ------------------------------------------------------------------ misc
    def _update_actions(self) -> None:
        has_capture = self.model.session is not None and self.model.sample_count > 0
        source_capturing = self.source is not None and self.source.is_capturing
        capturing = source_capturing or self.recording is not None or self._running_capture is not None
        for action in getattr(self, "sample_actions", []):
            action.setEnabled(has_capture and not capturing)
        if hasattr(self, "action_use_names"):
            self.action_use_names.setEnabled(has_capture and self.source is not None)

        self.capture_controls.update()  # (Again needs a capture done before)
        self.abort_button.setEnabled(capturing)  # (a capture, or a recording of the monitor)
        self.action_repeat.setEnabled(self.source is not None and not capturing)
        self.action_stop.setEnabled(capturing)
        self.action_device_card.setEnabled(self.source is not None)
        self.action_open.setEnabled(not capturing)
        for button in (self.search_tool_button, self.measure_tool_button):
            button.setEnabled(has_capture)

        self.action_save.setEnabled(has_capture)
        self.action_save_as.setEnabled(has_capture)
        for action in self._capture_actions:
            action.setEnabled(has_capture and not capturing)
        self.action_back_to_timing.setEnabled(self._timing_capture is not None and not capturing)
        self.action_align.setEnabled(has_capture and not capturing)
        self.action_export_csv.setEnabled(has_capture)
        self.action_export_vcd.setEnabled(has_capture)
        self.action_simulation.setEnabled(not capturing)
        self.fit_button.setEnabled(has_capture)
        self.trigger_button.setEnabled(has_capture)

        state_capture = self.model.session is not None and self.model.session.state_times is not None
        self.view_toolbar.set_available("real-time", state_capture or getattr(self, "_state_session", None) is not None)
        if not self.model.is_live:
            self.view_toolbar.set_available("roll", False)
        self.empty_primary_button.setEnabled(not capturing)
        # with a device: its capture setup, else the device list
        if self.source is not None:
            self.empty_title.setText("Nothing captured yet")
            self.empty_hint.setText(f"Set up the capture with {self.source.instrument.name} (channels, rate, "
                                    "trigger) and capture, or Run in the header captures with the current setup.")
            self.empty_primary_button.setText("Capture setup...")
            set_icon(self.empty_primary_button, "gear")
        else:
            self.empty_title.setText("No data")
            self.empty_hint.setText("A data view shows captures: capture with a device (Run in the header, or a "
                                    "flow), open a saved capture, or compute simulated signals.")
            self.empty_primary_button.setText("Devices...")
            set_icon(self.empty_primary_button, "devices")
        self.capture_stack.setCurrentIndex(1 if self.model.session is not None else 0)
        self._update_source_label()

    def _empty_page_primary_action(self) -> None:
        if self.source is not None:
            self.capture_controls.capture_settings()
        else:
            self.devices_requested.emit()

    def _update_info_panel(self) -> None:
        self._fill_info_panel()
        self._update_inspector()

    def _fill_info_panel(self) -> None:
        session = self.model.session
        if session is None:
            self.info_label.setText(f"<span style='color:{TEXT_MUTED}'>No capture loaded</span>")
            return

        trigger = session.trigger_type.label
        device = self.driver.device_version if self._has_real_device() else "–"
        rows = [
            ("File", html.escape(os.path.basename(self.current_file)) if self.current_file else "not saved"),
            ("Device", html.escape(str(device))),
            ("Rate", to_large_frequency(session.frequency)),
            ("Total samples", to_thousands(self.model.sample_count)),
            ("Pre-trigger", to_thousands(session.pre_trigger_samples)),
            ("Post-trigger", to_thousands(session.post_trigger_samples)),
            ("Duration", to_small_time(self.model.sample_count / max(session.frequency, 1))),
            ("Channels", str(len(session.capture_channels))),
            (
                "Trigger",
                trigger
                if session.trigger_type == TriggerType.SIMULATION
                else f"{trigger}, channel {session.trigger_channel + 1}",
            ),
            ("Value", session.trigger_description()),
        ]
        if session.bursts:
            rows.append(("Bursts", str(len(session.bursts))))
        if self._alignment_report is not None and self._alignment_report[0] is session:
            rows.append(("Alignment", self._alignment_report[1]))

        self.info_label.setText(
            "<table width='100%' cellspacing='0' cellpadding='1'>"
            + "".join(
                f"<tr><td style='color:{TEXT_MUTED}'>{name}</td>"
                f"<td align='right'>{html.escape(str(value))}</td></tr>"
                for name, value in rows
            )
            + "</table>"
        )

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self.shutdown()
        super().closeEvent(event)
