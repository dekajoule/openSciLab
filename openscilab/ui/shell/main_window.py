# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The lab shell: activity bar, sidebar, document tabs, inspector, console, command palette.

::

    ┌ header: logo · Ctrl+K · New ▾ Open Save · Examples ▾ · Run Pause Stop · time · devices ┐
    │ activity │ sidebar  │ documents (tab groups, splitters)        │ inspector             │
    │  bar     │          ├──────────────────────────────────────────┴───────────────────────┤
    │          │          │ console: problems · execution · log · python                     │
    └ status: note · problems · run · time · unsaved ────────────────────────────────────────┘

Documents (:mod:`..documents`) bring their menus, actions and tool bar; the shell shows the menus
of the active document between *Edit* and *View*, passes *Save*, *Save as* and *Close* on and
drives what the document runs (*Run*, the run protocol of ``documents/base.py``). The rules all
parts follow are in ``docs/design.md``.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QMenu,
    QToolButton,
    QWidget,
)

from ... import __version__
from ...core import preferences, recent
from ...core.hub import Hub
from ...driver.simulated import follow_routes
from ...sigrok.provider import SigrokProvider
from .. import background, messages
from .. import sections as plugin_sections
from ..devices.hub_bridge import HubBridge
from ..documents.base import Document
from ..documents.flow import FlowDocument
from ..icons import app_icon, icon
from ..theme import apply_palette, build_stylesheet, set_role, shell_stylesheet
from .command_palette import CommandPalette
from .documents import DocumentArea
from .services import ShellServices
from .start_page import StartPage, native
from .window_build import ShellBuildMixin
from .window_common import (  # noqa: F401 - DOCK_SHARES ... : known from here before the window was split
    DOCK_SHARES,
    LOGO_SIZE,
    SHELL_LAYOUT_VERSION,
    SHELL_STATE_FILE,
    SIMULATOR_GROUP,
    drives_outputs,
)
from .window_devices import ShellDevicesMixin
from .window_documents import ShellDocumentsMixin
from .window_layout import ShellLayoutMixin
from .window_projects import ShellProjectsMixin

log = logging.getLogger(__name__)


class ShellWindow(ShellBuildMixin, ShellDocumentsMixin, ShellDevicesMixin, ShellProjectsMixin, ShellLayoutMixin,
                  QMainWindow):
    """The main window of openSciLab.

    Its parts live in modules of their own (``window_build``, ``window_documents``, ``window_devices``,
    ``window_projects``, ``window_layout``); this class holds its signals, builds it and keeps what
    concerns the window as a whole: the active document, settings, extensions, tabs, closing."""

    #: the serial ports of the computer, read in a thread (see poll_ports)
    _ports_read = Signal(object)

    def __init__(self, decoder_paths: tuple[str, ...] = (), restore_state: bool = True) -> None:
        super().__init__()
        from .. import accessibility

        accessibility.install()  # icon-only buttons get names for screen readers
        from ... import plugins

        plugins.load(ui=True)  # devices of plugins: before the device list asks for them
        #: what sections and documents of plugins may ask of the window (ui/sections.py)
        self.services = ShellServices(self)
        #: the sections of plugins in this window: key -> (spec, widget)
        self._plugin_sections: dict[str, tuple] = {}
        self.setWindowIcon(app_icon())
        self.resize(1440, 900)
        apply_palette(self)
        self.setStyleSheet(self._window_stylesheet())
        self.setDockNestingEnabled(True)
        # Sidebar and inspector reach the bottom; the console sits below the documents only.
        self.setCorner(Qt.BottomLeftCorner, Qt.LeftDockWidgetArea)
        self.setCorner(Qt.BottomRightCorner, Qt.RightDockWidgetArea)

        #: One decoder registry for every data view.
        self.provider = SigrokProvider()
        if decoder_paths:
            self.provider.registry.search_paths = list(decoder_paths) + [
                path for path in self.provider.registry.search_paths if path not in decoder_paths
            ]
        # flows decode with the same decoders (and the same decoder folders) as the data views
        from ...lab.nodes import decode as decode_nodes

        decode_nodes.use_registry(self.provider.registry)
        #: Every instrument of the lab, open at the same time.
        self.hub = Hub()
        #: folders of projects opened from a template that were not saved yet (in the temp folder)
        self.temporary_projects: set[str] = set()
        # trigger routes between simulated instruments are wired in their circuits
        self._stop_following_routes = follow_routes(self.hub)
        self.hub_bridge = HubBridge(self.hub, self)
        self._document_menu_actions: list[QAction] = []
        self._document_file_actions: list[QAction] = []
        self._palette: Optional[CommandPalette] = None

        self.area = DocumentArea(self)
        self.area.empty_action.connect(self._empty_action)
        self.area.confirm_close = self._confirm_close
        self.area.needs_confirm = self._last_of_temporary
        self.area.global_actions = lambda: [self.action_save, self.action_save_as, self.action_close,
                                            self.action_undo, self.action_redo, self.action_palette,
                                            self.action_open, self.action_new_flow, self.action_settings]
        self.setCentralWidget(self.area)

        self._build_header()
        self._build_activity_and_sidebar()
        self._build_inspector()
        self._build_console()
        self._build_status()
        self._build_menus()
        self._fill_header_tools()
        self._start_extensions()

        self.area.active_changed.connect(self._on_active_changed)
        self.area.document_updated.connect(self._sync_document_state)
        self.area.documents_changed.connect(self._on_documents_changed)
        self.area.documents_changed.connect(self.services.documents_changed)
        self.hub_bridge.changed.connect(self._on_hub_event)

        self.start_page: Optional[StartPage] = None
        self._default_state = self.saveState(SHELL_LAYOUT_VERSION)
        #: the docks take their widths when the window is shown (before, the sidebar gets the room of
        #: the hidden node palette as well); not when a layout of the user was restored
        # the sidebar starts folded away the first time (the activity bar or Ctrl+B opens it, at a
        # sensible width); afterwards as it was left
        self._sidebar_opened = False
        #: the last sizing of docks (asked widths, attempt, widths they took): what a test reports
        self._last_dock_resize: tuple = ()
        self.activity_bar.set_sidebar_visible(False)
        self._docks_sized = bool(restore_state and self._restore_state())
        self.sidebar_dock.visibilityChanged.connect(self._sidebar_shown)
        self._on_active_changed(None)
        self._update_title()

    def show_preferences(self, page: str = "") -> dict:
        """*Settings…*: change the preferences; what changed applies at once (the theme on the next start)."""
        from ..dialogs.preferences_dialog import PAGES, PreferencesDialog

        dialog = PreferencesDialog(self)
        titles = [title for title, _icon in PAGES]
        if page in titles:
            dialog.pages.setCurrentRow(titles.index(page))
        if not dialog.exec():
            return {}
        self.apply_preferences(dialog.changes)
        return dialog.changes

    @staticmethod
    def _window_stylesheet() -> str:
        """The style of the window: the parts of the shell; the common style as well only when
        the application has none (a window used on its own). The application gets it at start –
        setting it here again made every widget match all rules twice."""
        application = QApplication.instance()
        if application is not None and application.styleSheet():
            return shell_stylesheet()
        return build_stylesheet() + shell_stylesheet()

    def _fill_header_tools(self) -> None:
        """The header's entries: the logo, the search, a button for each *New* (flow, panel, data view,
        waveform, project – in the order of File → New), Open, Save and the examples; Run, Pause, Stop and the
        time of the run. It adapts to the width of the window and is customized with a right click
        (``ui/widgets/toolbar.py``)."""
        from ..widgets.toolbar import HIGH, NORMAL

        bar = self.header

        def tool(text: str, icon_name: str, tip: str, menu: Optional[QMenu] = None,
                 action: Optional[QAction] = None) -> QToolButton:
            button = QToolButton(bar)
            button.setText(text)
            button.setIcon(icon(icon_name))
            button.setToolTip(tip)
            button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            button.setAutoRaise(True)
            if menu is not None:
                button.setMenu(menu)
                button.setPopupMode(QToolButton.InstantPopup)
            elif action is not None:
                button.clicked.connect(lambda _checked=False: action.trigger())
            return button

        # New: a button for each entry of File → New, in its order (flow, panel, data view, waveform, the
        # kinds of plugins, project)
        self.new_label = QLabel("New", bar)
        set_role(self.new_label, "hint")
        self.new_tools: dict[str, QToolButton] = {}
        for action in self.new_menu.actions():
            if action.isSeparator():
                continue
            text = action.text().replace("&", "")
            title = "Project..." if action is self.action_new_project else text
            if action is self.action_new_project:
                tip = "A new project from an example"
            else:  # (a plugin's kind of document says what it is)
                tip = action.toolTip() if action.toolTip() not in (text, action.text()) else f"A new {text.lower()}"
            if not action.shortcut().isEmpty():
                tip += f" ({native(action.shortcut().toString())})"
            button = self.new_tools[title.rstrip(".")] = tool(title, "plus", tip, action=action)
            button.setIcon(action.icon())
        self.open_tool = tool("Open", "folder", f"Open a file ({native('Ctrl+O')})", action=self.action_open)
        self.save_tool = tool("Save", "save", f"Save ({native('Ctrl+S')})", action=self.action_save)
        self.examples_tool = tool("Examples", "book", "Start from an example project (each runs with simulators)",
                                  menu=self.examples_menu)
        bar.add_widget("logo", self.logo, "Logo", "logo", HIGH, pinned=True)
        # the search right after the logo: the first place to look for anything
        bar.add_widget("search", self.palette_button, "Search commands", "search", HIGH)
        bar.add_widget("new", self.new_label, "New", "new", HIGH, pinned=True)
        for index, (text, button) in enumerate(self.new_tools.items()):
            bar.add_widget(f"new-{text.lower().replace(' ', '-')}", button, f"New {text.lower()}", "new",
                           HIGH if index < 3 else NORMAL)
        bar.add_widget("open", self.open_tool, "Open a file", "file", HIGH, text=False)
        bar.add_widget("save", self.save_tool, "Save", "file", HIGH, text=False)
        bar.add_widget("examples", self.examples_tool, "Examples", "file", NORMAL)
        bar.add_stretch("stretch-left")
        bar.add_widget("run", self.run_button, "Run", "run", HIGH, pinned=True)
        bar.add_widget("pause", self.pause_button, "Pause", "run", HIGH, text=False)
        bar.add_widget("stop", self.stop_button, "Stop", "run", HIGH, text=False)
        bar.add_widget("time", self.time_switch, "Real time / virtual time", "time", NORMAL)
        bar.add_stretch("stretch-right")
        bar.finish()

    def _start_extensions(self) -> None:
        """What plugins run while the window is open (``ui/sections.py``, *extensions*: the server of
        remote devices, ...). One that cannot be built is reported like a plugin that failed."""
        from ... import plugins

        self._extensions: list = []
        for spec in plugin_sections.extensions():
            try:
                self._extensions.append(spec.factory(self.services))
            except Exception as error:  # noqa: BLE001 - a plugin must not stop the window
                log.warning("The extension %s of %s could not be started", spec.key, spec.module, exc_info=True)
                plugins.section_failed(spec.module, error)

    def _tell_extensions(self, method: str, *args) -> None:
        for extension in getattr(self, "_extensions", ()):
            function = getattr(extension, method, None)
            if not callable(function):
                continue
            try:
                function(*args)
            except Exception:  # noqa: BLE001 - one plugin must not stop the others
                log.warning("The extension %r failed in %s", extension, method, exc_info=True)

    def apply_preferences(self, changes: dict) -> None:
        self._tell_extensions("preferences_changed", changes)
        if "appearance.font_size" in changes:
            application = QApplication.instance()
            if application is not None and application.styleSheet():
                application.setStyleSheet(build_stylesheet())
            self.setStyleSheet(self._window_stylesheet())
        if "devices.refresh_s" in changes:
            self.port_timer.setInterval(int(preferences.get("devices.refresh_s") * 1000))
        if "devices.show_simulators" in changes:
            self.devices_section.refresh()
            self._refresh_device_presets()
        if changes.get("devices.high_priority"):
            self.raise_priority()
        if "appearance.theme" in changes:
            choice = messages.choose(self, "Theme", "The new theme applies when openSciLab starts again.",
                                     ["Restart now", "Later"], cancel=False)
            if choice == 0:
                self.restart()
        elif any(key in changes for key in ("circuits.kicad_symbols", "circuits.model_folders", "circuits.use_kicad",
                                            "circuits.ngspice_path", "circuits.simavr_path")):
            # (the catalog and the engine processes are built once per run)
            choice = messages.choose(self, "Circuits", "The libraries and tools of Circuits are read when openSciLab "
                                     "starts again.", ["Restart now", "Later"], cancel=False)
            if choice == 0:
                self.restart()

    def raise_priority(self, ask: bool = True) -> bool:
        """The processes that read devices with a higher priority (preference ``devices.high_priority``):
        without asking where the system allows it, else - macOS, Linux - after one question with the
        password dialog of the system. Device processes started later take it over."""
        import sys

        from ...core import priority

        if priority.raise_own() or priority.is_raised():
            self.statusBar().showMessage("Devices are read with a higher priority", 6000)
            return True
        if sys.platform == "win32" or not ask:
            return False
        linux = sys.platform.startswith("linux")
        options = ["Raise the priority", "Not now"] + (["Allow it for good (from the next login)"] if linux else [])
        choice = messages.choose(
            self, "Higher priority",
            "Read devices with a higher priority? The system asks for the administrator's password.",
            options,
            "The processes that read devices (and openSciLab) run above other programs, so a busy computer does "
            "not make a stream overflow. It applies until openSciLab ends; it asks again at the next start. "
            "Settings → Devices switches it off." + (" 'For good' adds a line to /etc/security/limits.d: "
                                                     "from the next login on openSciLab raises itself." if linux
                                                     else ""))
        if choice is None or choice == 1:
            return False
        try:
            if choice == 2:
                background.run(self, "Allowing a higher priority...", priority.allow_permanently, cancellable=False)
                messages.info(self, "Higher priority", "Allowed for good: from the next login on openSciLab raises "
                              "its priority itself.")
            raised = background.run(self, "Raising the priority...", priority.raise_with_rights, cancellable=False)
        except priority.PriorityError as error:
            self.statusBar().showMessage(f"The priority was not raised: {error}", 10000)
            return False
        self.statusBar().showMessage(f"Devices are read with a higher priority ({len(raised)} processes)", 8000)
        return True

    def restart(self) -> bool:
        """Start the application again (after asking about unsaved changes)."""
        import sys

        from PySide6.QtCore import QProcess

        if not self.confirm_quit():
            return False
        self.save_state()
        # (quitting closes the window: by then the documents are closed, and saving the state
        # again would store a session without them – the new start would open nothing)
        self._restarting = True
        self.close_all_documents(force=True)
        if getattr(sys, "frozen", False):  # the packaged application is its own program
            QProcess.startDetached(sys.executable, sys.argv[1:])
        else:
            QProcess.startDetached(sys.executable, [arg for arg in sys.argv if arg])
        QApplication.instance().quit()
        return True

    def _empty_action(self, key: str) -> None:
        {"flow": self.new_flow, "connect": self.connect_dialog, "open": self.open_file_dialog,
         "start": self.show_start_page}[key]()

    def _last_of_temporary(self, document: QWidget) -> bool:
        """``document`` is the last open document of a temporary project (closing it loses the project)."""
        root = self.project_root_of(document) if self.temporary_projects else None
        if root is None or root not in self.temporary_projects:
            return False
        return not any(other is not document and self.project_root_of(other) == root for other in self.area.documents())

    def _confirm_close(self, document: QWidget) -> bool:
        """Ask what to do with the unsaved changes of ``document``; ``True`` to close it."""
        self.area.activate(document)
        if not getattr(document, "dirty", False) and self._last_of_temporary(document):
            root = self.project_root_of(document)
            choice = messages.choose(self, "Temporary project", f"The project of {document.title} is temporary "
                                     "and was not saved. Keep it?", ["Save project...", "Discard"])
            if choice is None:
                return False
            if choice == 0:
                return self.save_project_as(root) is not None
            self.temporary_projects.discard(root)
            return True
        choice = messages.choose(
            self, "Unsaved changes", f"{document.title} has changes that were not saved.",
            ["Save", "Discard"],
        )
        if choice is None:
            return False
        if choice == 0:
            return self.save_document(document)
        return True

    def _split(self, orientation) -> None:
        document = self.active_document()
        if document is None or self.area.is_detached(document):
            return
        group = self.area.group_of(document)
        if group is not None and group.count() < 2:
            self.statusBar().showMessage("Split needs a second document in this group: open one first", 5000)
            return
        self.area.split(document, orientation)

    def _cycle_tab(self, step: int) -> None:
        """Ctrl+Tab: the next document of the group (around the end)."""
        document = self.active_document()
        group = self.area.group_of(document) if document is not None else None
        if group is None or group.count() < 2:
            return
        self.area.activate(group.widget((group.indexOf(document) + step) % group.count()))

    def _move_to_next_group(self) -> None:
        document = self.active_document()
        if document is not None:
            self.area.move_to_next_group(document)

    def _detach(self) -> None:
        document = self.active_document()
        if document is not None and not self.area.is_detached(document):
            self.area.detach(document)

    # ---------------------------------------------------- active document
    def _on_active_changed(self, document: Optional[QWidget]) -> None:
        if isinstance(document, FlowDocument):
            self._last_flow = document  # (Use in a flow puts devices there)
        menu_bar = self.menuBar()
        for action in self._document_menu_actions:
            menu_bar.removeAction(action)
        self._document_menu_actions = []
        for action in self._document_file_actions:
            self.project_menu.removeAction(action)
        self._document_file_actions = []

        if isinstance(document, Document) and not self.area.is_detached(document):
            for menu in document.document_menus():
                action = menu.menuAction()
                menu_bar.insertAction(self.view_menu.menuAction(), action)
                self._document_menu_actions.append(action)
            for action in document.document_file_actions():
                self.project_menu.insertAction(self._document_section_end, action)
                self._document_file_actions.append(action)
        self._document_section.setVisible(bool(self._document_file_actions))

        self._sync_document_state(document)
        self.services.active_document_changed.emit(document)
        self.project_section.set_data_folder(self.data_folder(document))  # also lists it again
        self._refresh_device_presets()
        registry = document.registry if isinstance(document, FlowDocument) else None
        if registry is not None and registry is not self.nodes_section.registry:
            self.nodes_section.set_registry(registry)  # the project's own nodes too

    def _sync_document_state(self, document: Optional[QWidget]) -> None:
        """What follows the state of the active document: called when another document becomes
        active and whenever the active one changes (every edit, every undo step) – so only what
        is cheap. The menus of the document are built in :meth:`_on_active_changed`."""
        self._update_nodes_dock(document)
        can_save = isinstance(document, Document) and document.can_save()
        self.action_save.setEnabled(can_save)
        self.action_save_as.setEnabled(can_save)
        self.action_close.setEnabled(document is not None)
        stack = document.undo_stack() if isinstance(document, Document) else None
        self.action_undo.setEnabled(stack is not None and stack.canUndo())
        self.action_redo.setEnabled(stack is not None and stack.canRedo())
        self._update_inspector(document)
        self._update_title()
        self._update_saved_label()
        self._update_run_buttons()
        folder = self.data_folder(document)
        if folder != self.project_section.data_folder:  # e.g. the flow was saved to another place
            self.project_section.set_data_folder(folder)
        project = getattr(document, "project", None)
        if project is not self.project_section.project:
            self.project_section.set_project(project)
            self.services.project_changed.emit(project)

    def _run_state_changed(self, state: str) -> None:
        self._update_run_buttons()
        if state in ("finished", "stopped", "error"):
            self.project_section.refresh_data()  # what the run wrote

    def _update_inspector(self, document: Optional[QWidget]) -> None:
        widget = document.inspector_widget() if isinstance(document, Document) else None
        title = f"{document.title}" if document is not None and widget is not None else "Inspector"
        empty = "No document open" if document is None else "Nothing to set here"
        self.inspector.show_widget(widget, owner=document, title=title, empty_text=empty)

    def _on_documents_changed(self) -> None:
        self.project_section.set_documents(self.area.documents(), self.active_document())
        self._update_saved_label()
        self._update_title()
        document = self.active_document()
        can_save = isinstance(document, Document) and document.can_save()
        self.action_save.setEnabled(can_save)
        self.action_save_as.setEnabled(can_save)

    def _update_saved_label(self) -> None:
        documents = self.area.documents()
        unsaved = [document for document in documents if getattr(document, "dirty", False)
                   or (self.temporary_projects and self.is_temporary(document))]
        savable = [document for document in documents if getattr(document, "can_save", lambda: False)()]
        if unsaved:
            text = f"{len(unsaved)} unsaved"
        else:
            text = "saved" if savable else ""
        self.saved_label.setText(text)
        self.saved_label.setToolTip("\n".join(document.title for document in unsaved))
        self.setWindowModified(bool(unsaved))

    def _update_title(self) -> None:
        document = self.active_document()
        name = f"openSciLab {__version__}"
        self.setWindowTitle(f"{document.title}[*] — {name}" if document is not None else f"{name}[*]")

    def _undo(self) -> None:
        document = self.active_document()
        stack = document.undo_stack() if isinstance(document, Document) else None
        if stack is not None:
            stack.undo()

    def _redo(self) -> None:
        document = self.active_document()
        stack = document.undo_stack() if isinstance(document, Document) else None
        if stack is not None:
            stack.redo()

    #: how often a dock that did not take its width is sized again, and how far apart (ms)
    DOCK_ATTEMPTS, DOCK_RETRY = 25, 40

    # ================================================================= misc
    def _rebuild_recent_menu(self) -> None:
        self.recent_menu.clear()
        projects = recent.entries("projects")
        paths = recent.entries("files")
        if projects:
            self.recent_menu.addSection("Projects")
            for root in projects:
                self.recent_menu.addAction(icon("folder"), os.path.basename(root.rstrip(os.sep)),
                                           lambda r=root: self.open_project(r)).setToolTip(root)
        if paths:
            self.recent_menu.addSection("Files")
        for path in paths:
            self.recent_menu.addAction(os.path.basename(path), lambda p=path: self.open_file(p)).setToolTip(path)
        if not paths and not projects:
            self.recent_menu.addAction("Nothing opened yet").setEnabled(False)
        else:
            self.recent_menu.addSeparator()
            self.recent_menu.addAction("Clear the list", lambda: (recent.clear("files"), recent.clear("projects")))

    def show_shortcuts(self):
        """*Help → Keyboard shortcuts*: every shortcut of the menus and the documents, and the keys
        and gestures of the flow graph, the waveform, charts and panels; searchable."""
        from ..dialogs.shortcuts_dialog import ShortcutsDialog

        rows = sorted({(command.title, command.shortcut) for command in self.commands() if command.shortcut})
        dialog = ShortcutsDialog(rows, self)
        dialog.show()
        return dialog

    def show_decoder_paths(self) -> None:
        registry = self.provider.registry
        registry.load()
        paths = "\n".join(registry.search_paths) or "(none)"
        failed = registry.load_errors
        messages.info(
            self, "Decoder search paths", f"{len(registry.decoders)} protocol decoders are loaded"
            + (f", {len(failed)} could not be." if failed else "."),
            f"Search paths:\n{paths}\n\nBy default the 'decoders' folder next to the application is used. "
            "Pass --decoders, set the OPENSCILAB_DECODERS environment variable or copy decoders into "
            "the 'decoders' folder of the settings directory to add more."
            + "".join(f"\n\n{name}: {error}" for name, error in failed.items()),
        )

    def show_crash_notice(self, line: str) -> None:
        """An error nobody caught (``core.crashes``): the status bar says so; the log has the details."""
        self.statusBar().showMessage(f"An error occurred: {line} - the details are in crash.log (Help → Open "
                                     "the log folder)", 30000)

    def open_log_folder(self) -> None:
        """*Help → Open the log folder*: the settings directory with crash.log, faults.log and
        driver_debug.log."""
        from ...core import settings

        QDesktopServices.openUrl(QUrl.fromLocalFile(settings.settings_directory()))

    def show_plugins(self) -> None:
        from ... import plugins
        from ...driver import kinds

        found = plugins.load(ui=True)
        lines = [f"{plugin.name}: {plugin.error or 'loaded'} ({plugin.source})" for plugin in found]
        added = [f"{kind.kind}: {kind.title}{' (device process)' if kind.process else ''}"
                 for kind in kinds.registered()]
        nodes = [f"{spec.type}: {spec.title} ({plugin.name})" for plugin in found for spec in plugins.nodes(plugin)]
        added_sections = [f"{spec.key}: {spec.title} ({plugin.name})" for plugin in found if plugin.module is not None
                          for spec in plugin_sections.of_module(plugin.module.__name__)[0]]
        added_types = [f"{spec.kind}: {spec.title} ({', '.join(spec.suffixes)}; {plugin.name})" for plugin in found
                       if plugin.module is not None for spec in plugin_sections.of_module(plugin.module.__name__)[1]]
        folders = "\n".join(plugins.directories())
        failed = plugins.problems()
        details = (
            "\n".join(lines or ["(no plugins)"]) + "\n\nKinds of devices they add:\n" + "\n".join(added or ["(none)"])
            + "\n\nNodes they add to the flows:\n" + "\n".join(nodes or ["(none)"])
            + "\n\nSections they add to the sidebar:\n" + "\n".join(added_sections or ["(none)"])
            + "\n\nKinds of documents they add:\n" + "\n".join(added_types or ["(none)"])
            + f"\n\nPlugins are loaded from:\n{folders}\nand from installed packages (entry points "
            f"'{plugins.GROUP}') when openSciLab starts: restart it for a new plugin. See docs/drivers.md."
            + "".join(f"\n\n{plugin.details}" for plugin in failed)
        )
        messages.info(
            self, "Plugins",
            f"{len(found) - len(failed)} plugins are loaded" + (f", {len(failed)} could not be." if failed else "."),
            details,
        )

    def close_all_documents(self, force: bool = False) -> bool:
        return self.area.close_all(force=force)

    def confirm_quit(self) -> bool:
        """Before the window closes: one question for everything unsaved (documents with changes,
        temporary projects); nothing is closed when the user cancels."""
        documents = self.area.documents()
        dirty = [document for document in documents if getattr(document, "dirty", False)]
        temporary = sorted({root for root in (self.project_root_of(document) for document in documents)
                            if root in self.temporary_projects})
        if not dirty and not temporary:
            return True
        names = [document.title for document in dirty] + [
            f"the temporary project {os.path.basename(root)}" for root in temporary
            if not any(self.project_root_of(document) == root for document in dirty)]
        if len(names) > 1:
            text, details = "Save before closing?", "\n".join(names)
        else:
            text = f"{names[0][:1].upper()}{names[0][1:]} was not saved. Save it before closing?"
            details = "Discard closes without saving." if not temporary else \
                "It is in a temporary folder: without saving, it is gone."
        choice = messages.choose(self, "Unsaved changes", text, ["Save all" if len(names) > 1 else "Save", "Discard"],
                                 details)
        if choice is None:
            return False
        if choice == 1:
            return True
        for document in dirty:
            if getattr(document, "dirty", False) and not self.save_document(document):
                return False
        for root in temporary:
            if root in self.temporary_projects and self.save_project_as(root) is None:
                return False
        return True

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if getattr(self, "_restarting", False):  # restart() saved the state and asked already
            self.hub.close_all()
            self.console.detach_logging()
            super().closeEvent(event)
            return
        self.save_state()
        if not self.confirm_quit() or not self.close_all_documents(force=True):
            event.ignore()
            return
        self.hub.close_all()
        self.console.detach_logging()
        self._tell_extensions("close")
        super().closeEvent(event)

    def force_close(self) -> None:
        """Close without asking about unsaved changes (tests, smoke test)."""
        self._tell_extensions("close")
        self.save_state()
        self.close_all_documents(force=True)
        self.hub.close_all()
        self.hub_bridge.close()
        self.console.detach_logging()
        self.hide()
        self.deleteLater()
