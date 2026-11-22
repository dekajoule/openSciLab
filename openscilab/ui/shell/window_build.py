# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Building the window: header, activity bar and sidebar, docks, console, status bar and menus.

A part of :class:`~.main_window.ShellWindow` (ARCHITECTURE.md, *Known hotspots*): the window
combines its parts, which share its state through ``self``. Signals stay declared on the window.
"""

from __future__ import annotations

import logging
import threading
import webbrowser
from typing import Callable, Optional

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStatusBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import openscilab

from ... import __version__
from ...core import preferences
from .. import sections as plugin_sections
from ..devices.simulated import fill_simulation_menu
from ..dialogs.device_dialogs import AboutDialog
from ..documents.flow import FlowDocument
from ..flow.palette import NodePalette
from ..icons import icon, known, logo_pixmap, set_icon
from ..theme import icon_px, set_role, set_variant
from .activity_bar import ActivityBar
from .console import Console
from .devices_section import DevicesSection
from .inspector import Inspector
from .sidebar import ProjectSection, Sidebar
from .start_page import native
from .window_common import (
    DOCUMENTATION_URL,
    LOGO_SIZE,
)

log = logging.getLogger(__name__)


class ShellBuildMixin:
    """Building the window: header, activity bar and sidebar, docks, console, status bar and menus."""

    # ================================================================ build
    def _build_header(self) -> None:
        """The header: its widgets now, its entries once the menus exist (:meth:`_fill_header_tools`)."""
        from ..widgets.toolbar import AdaptiveToolBar

        bar = AdaptiveToolBar("header", "Header", self)
        bar.setObjectName("header-bar")
        bar.setIconSize(QSize(icon_px(14), icon_px(14)))
        self.logo = self._logo(bar)
        self.palette_button = QPushButton(f"Search commands...   {native('Ctrl+K')}", bar)
        self.palette_button.setObjectName("search-field")
        set_icon(self.palette_button, "search")
        self.palette_button.setToolTip(f"Every command, open document and recent file, by name ({native('Ctrl+K')})")
        self.palette_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.palette_button.setMaximumWidth(360)
        self.palette_button.clicked.connect(self.show_command_palette)
        # Run, Pause and Stop of the active document - a flow, a panel, a capture, a waveform
        # (docs/design.md, rule 3: no document has run buttons of its own)
        self.run_button = QPushButton("Run", bar)
        set_variant(self.run_button, "primary")
        set_icon(self.run_button, "play")
        self.pause_button = QPushButton("Pause", bar)
        set_variant(self.pause_button, "tool")
        set_icon(self.pause_button, "pause")
        self.pause_button.setToolTip(f"Pause the run ({native('F6')})")
        self.stop_button = QPushButton("Stop", bar)
        set_variant(self.stop_button, "danger")
        set_icon(self.stop_button, "stop")
        self.stop_button.setToolTip(f"Stop ({native('Shift+F5')})")
        for button in (self.run_button, self.pause_button, self.stop_button):
            button.setEnabled(False)
        self.run_button.clicked.connect(lambda: self._run_command("start_run"))
        self.pause_button.clicked.connect(lambda: self._run_command("pause_run"))
        self.stop_button.clicked.connect(lambda: self._run_command("stop_run"))
        # how the time of the active flow or panel passes: two halves of one switch
        self.time_switch = QWidget(bar)
        self.time_switch.setObjectName("segmented")
        self.time_switch.setAttribute(Qt.WA_StyledBackground)  # (its track)
        time_layout = QHBoxLayout(self.time_switch)
        time_layout.setContentsMargins(2, 2, 2, 2)
        time_layout.setSpacing(0)
        self.real_time_button = QToolButton(self.time_switch)
        self.real_time_button.setText("Real time")
        self.real_time_button.setToolTip("The flow runs with the clock: devices as they are")
        self.virtual_time_button = QToolButton(self.time_switch)
        self.virtual_time_button.setText("Virtual time")
        self.virtual_time_button.setToolTip("The flow runs as fast as it can in a time of its own, with the same "
                                            "result every run (simulators only)")
        for position, button in (("first", self.real_time_button), ("last", self.virtual_time_button)):
            button.setCheckable(True)
            button.setProperty("segment", position)
            button.setAutoRaise(False)
            time_layout.addWidget(button)
        self.real_time_button.clicked.connect(lambda: self.set_virtual_time(False))
        self.virtual_time_button.clicked.connect(lambda: self.set_virtual_time(True))
        self.header = bar
        self.addToolBar(Qt.TopToolBarArea, bar)

    def _logo(self, parent: QWidget) -> QWidget:
        """The mark of openSciLab (its name in the tooltip: the mark stands for itself)."""
        widget = QWidget(parent)
        widget.setObjectName("logo")
        widget.setToolTip(f"openSciLab {__version__}")
        widget.setAccessibleName("openSciLab")
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(4, 0, 6, 0)
        mark = QLabel(widget)
        mark.setPixmap(logo_pixmap(LOGO_SIZE, self.devicePixelRatioF() or 1.0))
        layout.addWidget(mark)
        return widget

    def _build_activity_and_sidebar(self) -> None:
        self.activity_bar = ActivityBar(self)
        self.activity_bar.add_section("project", "Project", "folder", "Ctrl+Shift+E")
        self.activity_bar.add_section("devices", "Devices", "devices", "Ctrl+Shift+D")
        self.action_start_page = QAction(icon("home"), "Start", self)
        self.action_start_page.setToolTip("The start page: simulators, devices, examples, recent files")
        self.action_start_page.triggered.connect(self.show_start_page)
        self.activity_bar.add_command("start", self.action_start_page)
        self.action_activity_settings = QAction(icon("gear"), "Settings", self)
        self.action_activity_settings.setToolTip("Settings")
        self.action_activity_settings.triggered.connect(lambda: self.show_preferences())
        self.activity_bar.add_command("settings", self.action_activity_settings)
        self.addToolBar(Qt.LeftToolBarArea, self.activity_bar)

        self.sidebar = Sidebar(self)
        self.project_section = ProjectSection(self.sidebar)
        self.project_section.document_activated.connect(self.area.activate)
        self.project_section.file_requested.connect(self.open_file)
        self.project_section.compare_requested.connect(self.compare_captures)
        self.project_section.export_requested.connect(self.export_capture_csv)
        self.sidebar.add_section("project", "Project", self.project_section)
        self.devices_section = DevicesSection(self.hub, self.sidebar)
        #: serial ports seen last: unplugged devices are marked, new ones listed
        self._ports: Optional[frozenset] = None
        self.port_timer = QTimer(self)
        self.port_timer.setInterval(int(preferences.get("devices.refresh_s") * 1000))
        self.port_timer.timeout.connect(self.poll_ports)
        self._ports_read.connect(self.watch_ports)
        self._ports_thread: Optional[threading.Thread] = None
        self.port_timer.start()
        self.devices_section.instrument_activated.connect(self.open_device_card)
        self.devices_section.entry_activated.connect(self.connect_entry)
        self.devices_section.data_requested.connect(lambda instrument: self.show_data(instrument, setup=True))
        self.devices_section.disconnect_requested.connect(self.disconnect_instrument)
        self.devices_section.disconnect_all_requested.connect(self.disconnect_all)
        self.sidebar.add_section("devices", "Devices", self.devices_section)
        self._add_plugin_sections()
        # the node palette: beside the flow that is edited (see _update_nodes_dock), not a section
        self.nodes_section = NodePalette(parent=self)
        self.nodes_section.node_requested.connect(self._add_node_to_active_flow)
        self.devices_section.flow_node_requested.connect(self.add_instrument_to_flow)
        self.devices_section.firmware_requested.connect(self.show_hardware)
        self.devices_section.hardware_requested.connect(self.show_hardware)
        self._refresh_device_presets()

        self.sidebar_dock = QDockWidget("Sidebar", self)
        self.sidebar_dock.setObjectName("sidebar-dock")
        self.sidebar_dock.setWidget(self.sidebar)
        self.sidebar_dock.setTitleBarWidget(QWidget(self.sidebar_dock))
        self.sidebar_dock.setFeatures(QDockWidget.DockWidgetClosable)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.sidebar_dock)

        self.activity_bar.section_selected.connect(self._show_section)
        self.activity_bar.sidebar_toggled.connect(self.sidebar_dock.setVisible)
        self.activity_bar.select("project")

        self.nodes_dock = QDockWidget("Nodes", self)
        self.nodes_dock.setObjectName("nodes-dock")
        nodes_page = QWidget(self.nodes_dock)
        nodes_layout = QVBoxLayout(nodes_page)
        nodes_layout.setContentsMargins(0, 0, 0, 0)
        nodes_layout.setSpacing(0)
        nodes_title = QLabel("NODES", nodes_page)
        set_role(nodes_title, "sidebar-title")
        nodes_title.setToolTip("Drag a node onto the flow, or double-click it (Ctrl+Shift+N shows or hides the palette)")
        nodes_layout.addWidget(nodes_title)
        self.nodes_section.setParent(nodes_page)
        nodes_layout.addWidget(self.nodes_section, 1)
        self.nodes_dock.setWidget(nodes_page)
        self.nodes_dock.setTitleBarWidget(QWidget(self.nodes_dock))
        self.nodes_dock.setFeatures(QDockWidget.NoDockWidgetFeatures)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.nodes_dock)
        self.splitDockWidget(self.sidebar_dock, self.nodes_dock, Qt.Horizontal)
        self.nodes_dock.setVisible(False)
        self.action_nodes = QAction(icon("nodes"), "Node palette", self)
        self.action_nodes.setCheckable(True)
        self.action_nodes.setChecked(bool(preferences.get("flow.show_nodes")))
        self.action_nodes.setShortcut("Ctrl+Shift+N")
        self.action_nodes.setToolTip("The palette of nodes beside a flow that is edited (Ctrl+Shift+N)")
        self.action_nodes.toggled.connect(self._nodes_palette_toggled)
        self.addAction(self.action_nodes)

    def _nodes_palette_toggled(self, shown: bool) -> None:
        preferences.update({"flow.show_nodes": bool(shown)})
        self._update_nodes_dock(self.active_document())

    def _update_nodes_dock(self, document: Optional[QWidget]) -> None:
        """The node palette shows beside a flow that is edited: its graph in front, in this window."""
        editing = isinstance(document, FlowDocument) and document.current_view() == "Graph" \
            and not self.area.is_detached(document)
        wanted = editing and self.action_nodes.isChecked()
        if self.nodes_dock.isVisible() != wanted:
            sidebar = self.sidebar_dock.width()
            self.nodes_dock.setVisible(wanted)
            if wanted and self.sidebar_dock.isVisible():  # (beside the sidebar, which keeps its width)
                QTimer.singleShot(0, self, lambda: self._resize_docks_to(
                    [self.sidebar_dock, self.nodes_dock], [sidebar, self.dock_width("node-palette")]))

    def _add_plugin_sections(self) -> None:
        """The sections plugins added (``ui/sections.py``), after Project and Devices; one that cannot
        be built is reported like a plugin that failed, the window opens."""
        from ... import plugins

        for spec in plugin_sections.sections():
            try:
                widget = spec.factory(self.services)
            except Exception as error:  # noqa: BLE001 - a plugin must not stop the window
                log.warning("The section %s of %s could not be built", spec.key, spec.module, exc_info=True)
                plugins.section_failed(spec.module, error)
                continue
            self.activity_bar.add_section(spec.key, spec.title, known(spec.icon, "layers"), spec.shortcut)
            self.sidebar.add_section(spec.key, spec.title, widget)
            self._plugin_sections[spec.key] = (spec, widget)

    def plugin_section(self, key: str) -> Optional[QWidget]:
        """The widget of the section ``key`` of a plugin in this window."""
        entry = self._plugin_sections.get(key)
        return entry[1] if entry else None

    def _show_section(self, key: str) -> None:
        self.sidebar.show_section(key)
        if key == "devices":
            self.devices_section.refresh()
        elif key == "project":
            self.project_section.refresh_recent()
        elif key in self._plugin_sections:
            spec, widget = self._plugin_sections[key]
            if spec.on_show is not None:
                try:
                    spec.on_show(widget)
                except Exception:  # noqa: BLE001 - reported, the section stays
                    log.warning("The section %s could not be refreshed", key, exc_info=True)

    def _build_inspector(self) -> None:
        self.inspector = Inspector(self)
        self.inspector_dock = QDockWidget("Inspector", self)
        self.inspector_dock.setObjectName("inspector-dock")
        self.inspector_dock.setWidget(self.inspector)
        self.inspector_dock.setTitleBarWidget(QWidget(self.inspector_dock))
        self.addDockWidget(Qt.RightDockWidgetArea, self.inspector_dock)

    def _build_console(self) -> None:
        namespace = {
            "shell": self,
            "openscilab": openscilab,
            "documents": lambda: self.area.documents(),
            "active": lambda: self.active_document(),
        }
        self.console = Console(namespace, self)
        self.console_dock = QDockWidget("Console", self)
        self.console_dock.setObjectName("console-dock")
        self.console_dock.setWidget(self.console)
        # like the sidebar and the inspector: no title bar of its own (its tabs say what it is, Ctrl+J
        # and View → Console show and hide it)
        self.console_dock.setTitleBarWidget(QWidget(self.console_dock))
        self.console_dock.setFeatures(QDockWidget.DockWidgetClosable)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.console_dock)
        self.resizeDocks([self.console_dock], [170], Qt.Vertical)
        # The console starts closed; Ctrl+J opens it.
        self.console_dock.setVisible(False)
        self.console.problems.changed.connect(self._update_problem_count)
        self.console.problems.problem_activated.connect(self.show_problem)

    def _build_status(self) -> None:
        status = QStatusBar(self)
        # errors and warnings of the open flows; a click shows them
        self.problems_button = QToolButton(status)
        self.problems_button.setObjectName("status-problems")
        self.problems_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.problems_button.setAutoRaise(True)
        self.problems_button.setToolTip("Problems of the open flows (click to show them)")
        self.problems_button.clicked.connect(lambda: self.show_console("Problems"))
        self.problems_button.setVisible(False)
        status.addPermanentWidget(self.problems_button)
        self.mode_label = QLabel("Real time", status)
        status.addPermanentWidget(self.mode_label)
        # the connected devices; a click shows the device list
        self.devices_label = QToolButton(status)
        self.devices_label.setObjectName("status-devices")
        self.devices_label.setText("No devices")
        self.devices_label.setIcon(icon("devices"))
        self.devices_label.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.devices_label.setAutoRaise(True)
        self.devices_label.setToolTip("The connected devices (click to show the device list)")
        self.devices_label.clicked.connect(lambda: self.activity_bar.select("devices"))
        status.addPermanentWidget(self.devices_label)
        self.saved_label = QLabel("", status)
        status.addPermanentWidget(self.saved_label)
        self.setStatusBar(status)
        status.messageChanged.connect(lambda text: text and self.console.message(text))

    def _action(self, text: str, slot: Callable, shortcut=None, icon_name: Optional[str] = None,
                checkable: bool = False) -> QAction:
        action = QAction(text, self)
        if shortcut is not None:
            if isinstance(shortcut, (list, tuple)):
                action.setShortcuts([QKeySequence(item) for item in shortcut])
            else:
                action.setShortcut(QKeySequence(shortcut))
        if icon_name:
            action.setIcon(icon(icon_name))
        action.setCheckable(checkable)
        action.triggered.connect(lambda _checked=False: slot())
        return action

    def _build_menus(self) -> None:
        menu_bar = self.menuBar()

        # ------------------------------------------------------------- File
        self.project_menu = menu_bar.addMenu("&File")
        # (one order everywhere: this menu, the header's New, the start page)
        self.new_menu = self.project_menu.addMenu(icon("file-plus"), "&New")
        self.action_new_flow = self._action("&Flow", self.new_flow, QKeySequence.New, icon_name="nodes")
        self.new_menu.addAction(self.action_new_flow)
        self.action_new_panel = self._action("&Panel", self.new_panel, icon_name="panel")
        self.new_menu.addAction(self.action_new_panel)
        self.action_new_data_view = self._action("&Data view", self.new_data_view, icon_name="channels")
        self.new_menu.addAction(self.action_new_data_view)
        self.action_new_waveform = self._action("&Waveform", self.new_waveform, icon_name="wave")
        self.new_menu.addAction(self.action_new_waveform)
        #: File → New entries of the kinds of documents of plugins: kind -> action
        self.plugin_new_actions: dict[str, QAction] = {}
        for spec in plugin_sections.document_types():
            if spec.new is None:
                continue
            action = self._action(f"&{spec.title}", lambda kind=spec.kind: self.new_document(kind),
                                  icon_name=known(spec.icon, "file-plus"))
            if spec.description:
                action.setToolTip(spec.description)
            self.new_menu.addAction(action)
            self.plugin_new_actions[spec.kind] = action
        self.new_menu.addSeparator()
        self.action_new_project = self._action("Pro&ject from an example...", self.new_project_dialog,
                                               icon_name="layers")
        self.new_menu.addAction(self.action_new_project)
        self.action_open = self._action("&Open file...", self.open_file_dialog, QKeySequence.Open, "folder")
        self.project_menu.addAction(self.action_open)
        self.action_open_project = self._action("Open pro&ject...", self.open_project_dialog, "Ctrl+Shift+O",
                                                "folder")
        self.project_menu.addAction(self.action_open_project)
        self.recent_menu = self.project_menu.addMenu("Open &recent")
        self.recent_menu.aboutToShow.connect(self._rebuild_recent_menu)
        self._rebuild_recent_menu()
        self.project_menu.addSeparator()
        self.action_save = self._action("&Save", self.save_active, QKeySequence.Save, "save")
        self.project_menu.addAction(self.action_save)
        self.action_save_as = self._action("Save &as...", self.save_active_as, "Ctrl+Shift+S", "save")
        self.project_menu.addAction(self.action_save_as)
        self.action_save_project = self._action("Save &project as...", lambda: self.save_project_as(), icon_name="layers")
        self.project_menu.addAction(self.action_save_project)
        self.action_save_template = self._action("Save project as &template...", self.save_as_template,
                                                 icon_name="bookmark")
        self.action_save_template.setToolTip("Keep the project as a template of your own: the start page and "
                                             "Examples offer it first")
        self.project_menu.addAction(self.action_save_template)
        self.action_close = self._action("&Close document", self.close_active, QKeySequence("Ctrl+W"), "close")
        self.project_menu.addAction(self.action_close)
        self._document_section = self.project_menu.addSeparator()
        self._document_section_end = self.project_menu.addSeparator()
        self.action_settings = self._action("Se&ttings...", self.show_preferences, QKeySequence.Preferences, "gear")
        self.action_settings.setMenuRole(QAction.PreferencesRole)  # macOS: openSciLab → Settings…
        if self.action_settings.shortcut().isEmpty():
            self.action_settings.setShortcut(QKeySequence("Ctrl+,"))
        self.project_menu.addAction(self.action_settings)
        self.action_exit = self._action("E&xit", self.close, QKeySequence.Quit, "exit")
        self.project_menu.addAction(self.action_exit)

        # ------------------------------------------------------------- Edit
        self.edit_menu = menu_bar.addMenu("&Edit")
        self.action_undo = self._action("&Undo", self._undo, QKeySequence.Undo)
        self.action_redo = self._action("&Redo", self._redo, QKeySequence.Redo)
        self.edit_menu.addAction(self.action_undo)
        self.edit_menu.addAction(self.action_redo)
        self.edit_menu.addSeparator()
        self.action_palette = self._action("&Command palette...", self.show_command_palette,
                                           ["Ctrl+K", "Ctrl+Shift+P"], "command")
        self.edit_menu.addAction(self.action_palette)

        # ------------------------------------------------------------- View
        self.view_menu = menu_bar.addMenu("&View")
        self.action_sidebar = self._action("&Sidebar", self.toggle_sidebar, "Ctrl+B", checkable=True)
        self.action_sidebar.setChecked(True)
        self.view_menu.addAction(self.action_sidebar)
        self.action_inspector = self._action("&Inspector", self.toggle_inspector, "Ctrl+Alt+I", "inspector", True)
        self.action_inspector.setChecked(True)
        self.view_menu.addAction(self.action_inspector)
        self.action_console = self._action("C&onsole", self.toggle_console, "Ctrl+J", "terminal", True)
        self.view_menu.addAction(self.action_console)
        self.sidebar_dock.visibilityChanged.connect(lambda _v: self.action_sidebar.setChecked(self.sidebar_dock.isVisible()))
        self.inspector_dock.visibilityChanged.connect(lambda _v: self.action_inspector.setChecked(self.inspector_dock.isVisible()))
        self.console_dock.visibilityChanged.connect(lambda _v: self.action_console.setChecked(self.console_dock.isVisible()))
        sections = self.view_menu.addMenu("Side&bar section")
        for key in self.activity_bar.keys():
            sections.addAction(self.activity_bar.section_action(key))
        self.view_menu.addAction(self.action_nodes)
        self.view_menu.addSeparator()
        self.view_menu.addAction(self._action("Customize the &header...", lambda: self.header.customize(),
                                              icon_name="gear"))
        self.view_menu.addAction(self._action(
            "Customize the &activity bar...",
            lambda: self.activity_bar.context_menu().exec(self.activity_bar.mapToGlobal(self.activity_bar.rect().center())),
            icon_name="gear"))
        self.view_menu.addSeparator()
        self.action_next_tab = self._action("Next &tab", lambda: self._cycle_tab(1), "Ctrl+Tab")
        self.action_previous_tab = self._action("Pre&vious tab", lambda: self._cycle_tab(-1), "Ctrl+Shift+Tab")
        self.view_menu.addAction(self.action_next_tab)
        self.view_menu.addAction(self.action_previous_tab)
        self.action_split_right = self._action("Split &right", lambda: self._split(Qt.Horizontal), "Ctrl+\\", "split")
        self.action_split_down = self._action("Split &down", lambda: self._split(Qt.Vertical))
        self.action_next_group = self._action("Move to the next &group", self._move_to_next_group)
        self.action_detach = self._action("Open in a new &window", self._detach, icon_name="detach")
        for action in (self.action_split_right, self.action_split_down, self.action_next_group, self.action_detach):
            self.view_menu.addAction(action)
        self.view_menu.addSeparator()
        self.layout_menu = self.view_menu.addMenu(icon("layout"), "&Layout")
        self.layout_menu.aboutToShow.connect(self._rebuild_layout_menu)
        self._rebuild_layout_menu()
        self.view_menu.addAction(self.action_start_page)

        # -------------------------------------------------------------- Run
        # what the active document runs: a flow, a panel, a capture, a waveform
        self.run_menu = menu_bar.addMenu("&Run")
        self.action_run = self._action("&Run", lambda: self._run_command("start_run"), "F5", "play")
        self.action_pause = self._action("&Pause", lambda: self._run_command("pause_run"), "F6", "pause")
        self.action_step = self._action("S&tep", lambda: self._run_command("step_run"), "F10", "arrow-right")
        self.action_stop = self._action("&Stop", lambda: self._run_command("stop_run"), "Shift+F5", "stop")
        for action in (self.action_run, self.action_pause, self.action_step, self.action_stop):
            self.run_menu.addAction(action)
        self.run_menu.addSeparator()
        self.action_virtual_time = self._action("&Virtual time", lambda: self.set_virtual_time(
            self.action_virtual_time.isChecked()), checkable=True)
        self.action_virtual_time.setToolTip("Run the flow as fast as it can in a time of its own (simulators only)")
        self.run_menu.addAction(self.action_virtual_time)
        self.action_simulate = self._action("Use &simulators for every device", lambda: self.set_simulated(
            self.action_simulate.isChecked()), checkable=True)
        self.action_simulate.setToolTip("Run the flow with a simulator in place of each of its devices")
        self.run_menu.addAction(self.action_simulate)

        # ---------------------------------------------------------- Devices
        self.devices_menu = menu_bar.addMenu("De&vices")
        self.action_connect = self._action("&Connect...", self.connect_dialog, "Ctrl+Shift+K", icon_name="plug")
        self.devices_menu.addAction(self.action_connect)
        self.devices_menu.addAction(self._action("Show the &device list", lambda: self.activity_bar.select("devices"),
                                                 icon_name="devices"))
        self.devices_menu.addSeparator()
        # the open devices: their document, their data view, disconnecting them (rebuilt when the menu opens)
        self.card_menu = self.devices_menu.addMenu(icon("devices"), "&Open device")
        self.data_menu_devices = self.devices_menu.addMenu(icon("channels"), "&Data view of")
        self.disconnect_menu = self.devices_menu.addMenu(icon("unplug"), "D&isconnect")
        self.action_disconnect_all = self._action("Disconnect &all devices...", self.disconnect_all,
                                                  icon_name="unplug")
        self.devices_menu.addAction(self.action_disconnect_all)
        self.devices_menu.aboutToShow.connect(self._fill_device_menus)
        self._fill_device_menus()
        self.devices_menu.addSeparator()
        self.action_hardware = self._action("&Boards and firmware", self.show_hardware, icon_name="chip")
        self.action_hardware.setToolTip("Every board on this computer with its firmware: install or update it")
        self.devices_menu.addAction(self.action_hardware)
        self.simulation_menu = self.devices_menu.addMenu(icon("warning"), "Simulated &faults")
        self.simulation_menu.aboutToShow.connect(lambda: fill_simulation_menu(self.simulation_menu, self.hub))
        fill_simulation_menu(self.simulation_menu, self.hub)
        self.devices_menu.addAction(self._action("&Forget sets of several Picos...", self.forget_known_devices,
                                                 icon_name="trash"))

        # --------------------------------------------------------- Examples
        # every project of the library, a sub menu per category; one opens as a copy to start from
        self.examples_menu = menu_bar.addMenu("E&xamples")
        self._build_examples_menu()

        # ------------------------------------------------------------- Help
        self.help_menu = menu_bar.addMenu("&Help")
        self.help_menu.addAction(self._action("&Keyboard shortcuts...", self.show_shortcuts, icon_name="keyboard"))
        self.help_menu.addAction(self._action("Online &documentation",
                                              lambda: webbrowser.open(DOCUMENTATION_URL), icon_name="book"))
        self.help_menu.addSeparator()
        self.help_menu.addAction(self._action("Protocol &decoders...", self.show_decoder_paths, icon_name="list"))
        self.help_menu.addAction(self._action("P&lugins...", self.show_plugins, icon_name="layers"))
        self.help_menu.addAction(self._action("Open the &log folder", self.open_log_folder, icon_name="folder"))
        self.help_menu.addSeparator()
        self.help_menu.addAction(self._action("&About openSciLab", lambda: AboutDialog(self).exec(), icon_name="info"))
