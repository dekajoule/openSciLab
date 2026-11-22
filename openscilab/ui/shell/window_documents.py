# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The documents of the window: opening, creating, running and showing flows, panels, data views,
waveforms, examples and templates.

A part of :class:`~.main_window.ShellWindow` (ARCHITECTURE.md, *Known hotspots*): the window
combines its parts, which share its state through ``self``. Signals stay declared on the window.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from PySide6.QtCore import QUrl
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QWidget,
)

from ...core import preferences, recent
from ...core.instrument import Instrument
from .. import messages
from .. import sections as plugin_sections
from ..documents.base import Document
from ..documents.chart import CHART_KINDS, VALUE_KINDS, ViewsDocument
from ..documents.dataview import DataView
from ..documents.flow import FlowDocument, open_flow_file
from ..documents.panel import PanelDocument, open_panel_file
from ..documents.waveform import WAVE_EXTENSIONS, WaveformDocument, open_waveform_file
from ..icons import icon
from ..theme import token as theme_token
from .console import Problem as ConsoleProblem
from .sidebar import data_kind
from .start_page import StartPage, native
from .window_common import (
    CAPTURE_EXTENSIONS,
    FLOW_EXTENSIONS,
    OPEN_FILE_FILTER,
    PANEL_EXTENSIONS,
    _is_flow_script,
    _simulator_titles,
    device_node_id,
)

log = logging.getLogger(__name__)


class ShellDocumentsMixin:
    """The documents of the window: opening, creating, running and showing flows, panels, data views,"""

    # ============================================================ documents
    def documents(self) -> list[QWidget]:
        return self.area.documents()

    def active_document(self) -> Optional[QWidget]:
        return self.area.active_document()

    def add_document(self, document: QWidget, activate: bool = True) -> QWidget:
        # what every document tells the shell: notes for the status bar, the state of its run
        message = getattr(document, "message", None)
        if message is not None:
            message.connect(self.notify)
        run_state = getattr(document, "run_state_changed", None)
        if run_state is not None:
            run_state.connect(self._run_state_changed)
        for switch in (getattr(document, "fast_box", None), getattr(document, "sim_box", None)):
            if switch is not None:
                switch.toggled.connect(lambda _checked: self._update_run_buttons())
        if isinstance(document, FlowDocument):
            self._wire_flow_document(document)
        elif isinstance(document, DataView):
            document.play_requested.connect(self.new_waveform)
            document.inspector_requested.connect(lambda: self.inspector_dock.setVisible(True))
            document.device_card_requested.connect(self.open_device_card)
            document.devices_requested.connect(lambda: self.activity_bar.select("devices"))
        elif isinstance(document, PanelDocument):
            document.flow_provider = self.open_flow_document
            document.flow_requested.connect(self.show_document_or_file)
            document.execution_line.connect(self.console.execution.add)
        self.area.add_document(document, activate=activate)
        return document

    def notify(self, text: str, timeout: int = 6000) -> None:
        """A short note in the status bar (and the log of the console): what was done, what passed."""
        self.statusBar().showMessage(text, timeout)

    def decoders(self):
        """The decoders of a new data view: its own list, the decoder registry shared by all."""
        from ...sigrok.provider import SigrokProvider

        return SigrokProvider(self.provider.registry)

    def show_document_or_file(self, target) -> Optional[QWidget]:
        """Show ``target``: an open document, or a file (opened unless it is open already)."""
        if isinstance(target, str):
            return self.open_file(target)
        if target in self.area.documents():
            self.area.activate(target)
        else:
            self.add_document(target)
        return target

    def open_flow_document(self, path: str) -> Optional[FlowDocument]:
        """The open (top level) flow document of the file ``path``, if any."""
        absolute = os.path.realpath(path)
        for document in self.area.documents():
            if isinstance(document, FlowDocument) and document.parent_document is None and document.path \
                    and os.path.realpath(document.path) == absolute:
                return document
        return None

    def new_document(self, kind: str) -> Optional[QWidget]:
        """A new document of a kind a plugin added (*File → New*)."""
        spec = plugin_sections.document_type_of_kind(kind)
        if spec is None or spec.new is None:
            return None
        document = spec.new(self.services)
        self.add_document(document)
        return document

    def new_flow(self, flow=None, activate: bool = True) -> FlowDocument:
        """A new flow document (an empty flow, or ``flow``); the node palette shows beside it."""
        document = FlowDocument(flow, hub=self.hub)
        self.add_document(document, activate=activate)
        return document

    def _wire_flow_document(self, document: FlowDocument) -> None:
        def problems(found) -> None:
            self.console.problems.set_problems(
                str(id(document)), [ConsoleProblem(problem.severity, str(problem), document.title,
                                                   problem.node, document) for problem in found])

        document.problems_changed.connect(problems)
        document.console_requested.connect(self.show_console)
        document.execution_line.connect(self.console.execution.add)
        document.view_requested.connect(lambda node, kind, value, options, doc=document: self.show_view(
            doc, node, kind, value, options))
        document.open_requested.connect(lambda child: self.area.activate(child) if child in self.area.documents()
                                        else self.add_document(child))
        document.panel_requested.connect(lambda doc=document: self.new_panel(flow_document=doc))
        document.open_panels = lambda: [doc for doc in self.area.documents() if isinstance(doc, PanelDocument)]
        document.panel_open_requested.connect(self.show_document_or_file)
        document.data_requested.connect(lambda key, title, session, front, doc=document: self.show_run_data(
            doc, key, title, session, front))
        document.destroyed.connect(lambda *_args, key=str(id(document)): self.console.problems.set_problems(key, []))
        problems(document.problems())

    def _flow_to_edit(self) -> FlowDocument:
        """The flow new nodes go into: the active flow, else the flow last worked on, else a new one."""
        document = self.active_document()
        if isinstance(document, FlowDocument):
            return document
        last = getattr(self, "_last_flow", None)
        if last is not None and last in self.area.documents():
            self.area.activate(last)
            return last
        return self.new_flow()

    def _add_node_to_active_flow(self, text: str) -> Optional[str]:
        """Add a node (a type, or a preset of :func:`~..flow.canvas.encode_node`) to the flow to edit."""
        from ..flow.canvas import decode_node

        type_name, node_id, params = decode_node(text)
        document = self._flow_to_edit()
        return document.add_node(type_name, document._center(), node_id, **params)

    def add_instrument_to_flow(self, instrument: Instrument) -> Optional[str]:
        """*Use in a flow*: a device node for an instrument in the flow to edit (the active one, the
        one last worked on, or a new flow); the flow comes to the front."""
        from ...lab.model import DEVICE_NODE
        from ..flow.canvas import encode_node

        address = instrument.uri
        document = self._flow_to_edit()
        existing = next((node_id for node_id, node in document.flow.nodes.items()
                         if node.type == DEVICE_NODE and node.params.get("address") == address), None)
        if existing is not None:  # (already in it: shown instead of a second one)
            document.scene.select_nodes([existing])
            self.area.activate(document)
            return existing
        node = self._add_node_to_active_flow(encode_node(DEVICE_NODE, device_node_id(instrument),
                                                         {"address": address}))
        self.area.activate(document)
        if node:
            self.notify(f"{instrument.name} is the node '{node}' of {document.title}: wire its 'device' output to "
                        "the nodes that use it")
        return node

    def device_presets(self) -> list:
        """The devices the node palette offers: open instruments, the project's devices, simulators."""
        from ...driver.simulated.profiles import available_profiles, load_profile
        from ..flow.palette import DevicePreset

        presets = [DevicePreset(f"{instrument.name} (open)", device_node_id(instrument), instrument.uri, "open")
                   for instrument in self.hub.instruments() if instrument.uri]
        project = getattr(self.active_document(), "project", None)
        if project is not None:
            presets += [DevicePreset(f"{name} (project: {address})", name, "", "project")
                        for name, address in project.devices.items()]
        if preferences.get("devices.show_simulators"):
            for name, title in _simulator_titles(tuple(available_profiles()), load_profile):
                presets.append(DevicePreset(title, name, f"sim:{name}", "simulator"))
        return presets

    def _refresh_device_presets(self) -> None:
        self.nodes_section.set_device_presets(self.device_presets())

    def _update_problem_count(self) -> None:
        if not hasattr(self, "problems_button"):
            return
        errors, warnings = self.console.problems.counts()
        if errors or warnings:
            self.problems_button.setIcon(icon("error" if errors else "warning",
                                              theme_token("run.error" if errors else "status.warning")))
        words = ([f"{errors} error{'s' if errors != 1 else ''}"] if errors else []) + (
            [f"{warnings} warning{'s' if warnings != 1 else ''}"] if warnings else [])
        self.problems_button.setText(", ".join(words))
        self.problems_button.setVisible(bool(words))

    def show_problem(self, problem) -> None:
        """Where a problem is: its flow document, the node selected and in view."""
        document = getattr(problem, "owner", None)
        if document is None or document not in self.area.documents():
            return
        self.area.activate(document)
        node = getattr(problem, "node", "")
        if node and isinstance(document, FlowDocument) and node in document.scene.nodes:
            document.set_view("Graph")
            document.scene.select_nodes([node])
            document.view.centerOn(document.scene.nodes[node])

    def show_console(self, tab: str = "Problems") -> None:
        self.console_dock.setVisible(True)
        self.console.show_tab(tab)

    def _runnable(self) -> Optional[Document]:
        """The active document when it runs something (the run protocol of ``documents/base.py``)."""
        document = self.active_document()
        return document if isinstance(document, Document) and document.runnable else None

    def _run_command(self, name: str) -> None:
        """Run, Pause, Step, Stop (header, Run menu, F5 …) for the active document: run a flow or a
        panel, capture with a device or in a data view, play a waveform."""
        document = self._runnable()
        if document is not None:
            if name == "step_run" and not document.can_step:
                return
            getattr(document, name)()
            if isinstance(document, (FlowDocument, PanelDocument)) and name == "start_run":
                self.show_console("Execution")
        self._update_run_buttons()

    def set_virtual_time(self, virtual: bool) -> None:
        """The active flow or panel runs in virtual time (``True``) or in real time."""
        document = self._runnable()
        if document is not None and document.time_modes:
            document.set_virtual_time(virtual)
        self._update_run_buttons()

    def set_simulated(self, simulated: bool) -> None:
        """The active flow runs with simulators in place of its devices."""
        document = self._runnable()
        if document is not None and document.simulates_devices:
            document.set_simulated(simulated)
        self._update_run_buttons()

    def _update_run_buttons(self) -> None:
        document = self._runnable()
        state = document.run_state if document is not None else "idle"
        running = state not in ("idle", "finished", "stopped", "error")
        paused = state == "paused"
        can_start = document is not None and document.can_start_run and (not running or paused)
        label = document.run_label if document is not None else "Run"
        self.run_button.setEnabled(can_start)
        self.run_button.setText("Resume" if paused else label)
        self.run_button.setToolTip(("Resume the paused run" if paused else (
            document.run_tooltip if document is not None and document.run_tooltip else
            "Run what the active document runs: a flow, a panel, a capture, a waveform")) + f" ({native('F5')})")
        self.header.schedule()  # (the text changed: the header lays itself out again)
        can_pause = document is not None and running and not paused and document.can_pause
        self.pause_button.setEnabled(can_pause)
        self.stop_button.setEnabled(document is not None and running)
        self.action_run.setEnabled(can_start)
        self.action_run.setText("&Resume" if paused else f"&{label}")
        self.action_pause.setEnabled(can_pause)
        self.action_step.setEnabled(document is not None and document.can_step)
        self.action_stop.setEnabled(document is not None and running)
        # the time the active flow or panel runs in
        timed = document is not None and document.time_modes
        virtual = timed and document.virtual_time()
        self.header.set_available("time", timed)
        self.time_switch.setEnabled(timed and not running)
        self.real_time_button.setChecked(timed and not virtual)
        self.virtual_time_button.setChecked(virtual)
        self.action_virtual_time.setEnabled(timed and not running)
        self.action_virtual_time.setChecked(virtual)
        simulates = document is not None and document.simulates_devices
        self.action_simulate.setEnabled(simulates and not running)
        self.action_simulate.setChecked(simulates and document.simulated())
        self.mode_label.setVisible(timed or running)
        words = {"running": "Running", "paused": "Paused"}
        parts = ([words[state]] if state in words else []) + (
            ["Virtual time" if virtual else "Real time"] if timed else [])
        self.mode_label.setText(" · ".join(parts))

    def show_view(self, source: QWidget, node: str, kind: str, value, options: dict) -> Optional[QWidget]:
        """Show what a view node of a running flow sends: the scope in a data view, charts and
        values as tiles of the flow's views document (updated while the flow runs)."""
        key = (id(source), node)
        documents = getattr(self, "_view_documents", {})
        self._view_documents = documents
        document = documents.get(key)
        if document is not None and document not in self.area.documents():
            document = None
        if kind == "scope":
            created = document is None
            if created:
                document = DataView(provider=self.decoders(), hub=self.hub)
                document.set_viewer()  # the flow sends the data: nothing to capture here
                documents[key] = document
                self.add_document(document, activate=False)
            title = options.get("title") or node
            renamed, document.display_name = document.display_name != title, title
            # more of the same signal: the part the user zoomed to stays where it is
            document.load_session(value.to_session(), reset_view=created)
            if created or renamed:
                document._update_title()
            return document
        # the other views: tiles of one document per flow (not a tab per node)
        views = documents.get((id(source), None))
        if views is None or views not in self.area.documents():
            if kind not in CHART_KINDS + VALUE_KINDS:
                return None  # checks and report sections have no view
            views = ViewsDocument(getattr(source, "title", "flow"))
            documents[(id(source), None)] = views
            self.add_document(views, activate=False)
        views.set_flow_title(getattr(source, "title", views.flow_title))
        shown = views.show_value(node, kind, value, options)
        if options.get("front") and shown is not None:  # (asked for: the data of a node)
            self.area.activate(views)
            views.scroll.ensureWidgetVisible(views.tiles[node])
        return shown

    def show_run_data(self, source: FlowDocument, key: str, title: str, session, front: bool = True) -> DataView:
        """A data view with run data of ``source`` (one per ``key``: the whole run, a node); it is
        updated in place – the part the user zoomed to stays – while it is open."""
        documents = getattr(self, "_run_data_documents", {})
        self._run_data_documents = documents
        document = documents.get((id(source), key))
        created = document is None or document not in self.area.documents()
        if created:
            document = DataView(provider=self.decoders(), hub=self.hub)
            document.set_viewer()  # the run data of the flow: nothing to capture here
            documents[(id(source), key)] = document
            document.destroyed.connect(lambda *_args, doc=source, key=key: self._run_data_closed(doc, key))
            self.add_document(document)
        document.display_name = title
        document.load_session(session, reset_view=created)
        if created:  # the whole run at first
            document.model.set_view(0, document.model.sample_count)
            document._sync_view_controls()
        document._update_title()
        if front and not created:
            self.area.activate(document)
        return document

    def _run_data_closed(self, source: FlowDocument, key: str) -> None:
        self._run_data_documents.pop((id(source), key), None)
        try:
            source.data_view_closed(key)
        except RuntimeError:  # the flow document is gone too
            pass

    def new_data_view(self) -> DataView:
        """A new, empty data view."""
        return self.add_document(DataView(provider=self.decoders(), hub=self.hub))

    def new_panel(self, panel=None, flow_path: Optional[str] = None, flow_document=None) -> PanelDocument:
        """A new panel for the flow in ``flow_path``, of ``flow_document`` or of the active flow
        document – saved or not. A panel made for a flow starts with the widgets it suggests."""
        from ...lab import panel_model
        from ...lab.panel_model import Panel

        active = self.active_document()
        if flow_path is None and flow_document is None and isinstance(active, FlowDocument):
            flow_document = active
        if flow_path is None and flow_document is None and panel is None:
            # a panel works with a flow: which one (an open flow, or a new one)
            flows = [document for document in self.area.documents()
                     if isinstance(document, FlowDocument) and document.parent_document is None]
            if len(flows) == 1:
                flow_document = flows[0]
            else:
                options = [document.title for document in flows] + ["A new flow"]
                choice = messages.choose(self, "New panel", "Which flow does the panel operate?", options,
                                         "A panel's switches, sliders and buttons send into the flow, its numbers, "
                                         "LEDs and charts show what the flow sends.")
                if choice is None:
                    return None
                flow_document = flows[choice] if choice < len(flows) else self.new_flow()
        while flow_document is not None and flow_document.parent_document is not None:
            flow_document = flow_document.parent_document  # a subflow: the flow it belongs to
        if flow_path is None and flow_document is not None and flow_document.path \
                and not flow_document.path.endswith(".py"):
            flow_path = flow_document.path
        if panel is None:
            name = flow_document.flow.name if flow_document is not None and flow_document.flow.name != "Flow" else "Panel"
            panel = Panel(name=name, flow=os.path.abspath(flow_path) if flow_path else "")
            if flow_document is not None:
                panel.widgets = panel_model.suggest(flow_document.flow, flow_document.registry, panel.width)
                panel.grow_to_fit()
        document = self.add_document(PanelDocument(panel, hub=self.hub, flow_document=flow_document))
        if flow_document is not None:
            self.statusBar().showMessage(
                f"A panel for {flow_document.title}" + (f" with {len(panel.widgets)} suggested widgets"
                                                        if panel.widgets else ""), 6000)
        return document

    def new_waveform(self, waveform=None) -> WaveformDocument:
        """A waveform document (a sine, or ``waveform``, e.g. a capture to play)."""
        return self.add_document(WaveformDocument(self.hub, waveform))

    def show_start_page(self) -> StartPage:
        if self.start_page is not None and self.start_page in self.area.documents():
            self.area.activate(self.start_page)
            return self.start_page
        page = StartPage()
        page.example_requested.connect(self.open_example)
        page.file_requested.connect(self.open_file)
        page.project_requested.connect(self.open_project)
        page.open_file_requested.connect(self.open_file_dialog)
        page.simulator_requested.connect(self.try_simulator)
        page.connect_requested.connect(self.connect_dialog)
        page.new_flow_requested.connect(self.new_flow)
        page.open_project_requested.connect(self.open_project_dialog)
        page.template_delete_requested.connect(self.delete_template)
        page.destroyed.connect(lambda: setattr(self, "start_page", None))
        self.start_page = page
        self.add_document(page)
        return page

    def open_data_view_of_device(self) -> "DataView":
        """A data view that captures with the connected device (asked which when there are several;
        without one the device list shows) - the ``data`` of a project's ``open``."""
        document = self.new_data_view()
        capturing = [instrument for instrument in self.hub.instruments() if instrument.capture is not None]
        if not capturing:
            self.activity_bar.select("devices")
            self.notify("Connect a device in the device list (or try a simulator): the data view captures with it",
                        10000)
            return document
        chosen = capturing[0] if len(capturing) == 1 else self.choose_capture_device(capturing)
        if chosen is not None:
            document.use_instrument(chosen)
        return document

    def _build_examples_menu(self) -> None:
        from ...lab import examples

        self.examples_menu.clear()
        self.example_actions: dict[str, QAction] = {}
        catalog = examples.catalog()
        if not catalog:
            self.examples_menu.addAction("No examples found").setEnabled(False)
            return
        self.examples_menu.addAction(icon("home"), "All examples (start page)", self.show_start_page)
        self.examples_menu.addSeparator()
        for category in catalog:
            submenu = self.examples_menu.addMenu(category.title)
            submenu.setToolTipsVisible(True)
            submenu.setToolTip(category.description)
            for example in category.examples:
                action = submenu.addAction(example.title)
                action.setToolTip(example.description)
                action.setStatusTip(example.description)
                action.triggered.connect(lambda _checked=False, key=example.key: self.open_example(key))
                self.example_actions[example.key] = action
            if category.key == examples.USER_CATEGORY:  # (the user's own: they can be deleted)
                submenu.addSeparator()
                delete_menu = submenu.addMenu(icon("trash"), "Delete")
                for example in category.examples:
                    delete_menu.addAction(example.title).triggered.connect(
                        lambda _checked=False, key=example.key: self.delete_template(key))
        self.examples_menu.setToolTipsVisible(True)

    def save_as_template(self) -> Optional[str]:
        """*Save project as template…*: the project of the active document becomes a template of the
        user (``lab/examples.save_template``), listed first under *Templates*; returns its key."""
        from ...lab import examples
        from ...lab.project import Project
        from ..dialogs.template_dialog import TemplateDialog

        title = "Save project as template"
        root = self.project_root_of(self.active_document())
        if root is None:
            messages.info(self, title, "The active document is not part of a project.",
                          "Open a project, or start one from an example, and save it as a template of your own.")
            return None
        documents = [document for document in self.area.documents() if self.project_root_of(document) == root]
        changed = [document for document in documents if document.dirty and document.path]
        if changed and not messages.confirm(
                self, title, f"{len(changed)} document(s) of the project have changes that are not saved.",
                "Save and continue", "A template is made of the files of the project: the changes are saved first."):
            return None
        if not all(document.save() for document in changed):
            return None
        try:
            project = Project.open(root)
        except (OSError, ValueError) as error:
            messages.error(self, title, f"{root} could not be read.", str(error))
            return None
        dialog = TemplateDialog(project.name, str(project.extra.get("description") or ""), self)
        if not dialog.exec():
            return None
        try:
            template = examples.save_template(root, dialog.title, dialog.description,
                                              [document.path for document in documents if document.path])
        except (OSError, ValueError) as error:
            messages.error(self, title, "The template could not be saved.", str(error))
            return None
        self._templates_changed()
        self.notify(f'Template "{template.title}" saved: Examples → Your templates', 8000)
        return template.key

    def delete_template(self, key: str) -> bool:
        """Delete a template of the user (after asking)."""
        from ...lab import examples

        example = examples.find(key)
        if example is None or not example.user:
            return False
        if not messages.confirm(self, "Delete template", f'Delete the template "{example.title}"?', "Delete",
                                "Projects made from it stay as they are. This cannot be undone.", destructive=True):
            return False
        try:
            examples.delete_template(key)
        except (OSError, ValueError) as error:
            messages.error(self, "Delete template", "The template could not be deleted.", str(error))
            return False
        self._templates_changed()
        return True

    def _templates_changed(self) -> None:
        self._build_examples_menu()
        if self.start_page is not None:
            self.start_page.load_library()

    def open_example(self, key: str, folder: Optional[str] = None) -> Optional[QWidget]:
        """Open the example ``key`` (``<category>/<example>``) as a copy: a temporary project
        unless ``folder`` is given; the example in the library stays as it is."""
        from ...lab import examples

        example = examples.find(key)
        if example is None:
            messages.warning(self, "Examples", f"The example '{key}' is not available.")
            return None
        return self._open_project_copy(example.path, example.name, folder, example.title)

    def _open_project_copy(self, source: str, name: str, folder: Optional[str], what: str) -> Optional[QWidget]:
        """Copy the project ``source`` (of the library) and open its files and views."""
        from ...lab import templates

        temporary = folder is None
        if temporary:
            # A temporary project at first: Save asks where to keep it (Project → Save project as).
            import tempfile

            folder = templates.unique_folder(tempfile.mkdtemp(prefix="openscilab-"), name)
        try:
            root = templates.copy_project(source, folder)
        except OSError as error:
            messages.error(self, what, "The project could not be created.", str(error))
            return None
        if temporary:
            self.temporary_projects.add(os.path.abspath(root))
            self.notify(f"{what}: a copy of the example in a temporary folder – Save asks where to keep it", 10000)
        else:
            recent.add(root, "projects")
        opened = None
        for path in templates.files_to_open(root):
            document = self.open_file(path)
            opened = opened or document
        for view in templates.views_to_open(root):
            if view == "data":
                opened = opened or self.open_data_view_of_device()
        if opened is not None:
            self.area.activate(opened)
        return opened

    def choose_capture_device(self, instruments: list) -> Optional[Instrument]:
        """Which connected device a new data view captures with (``None``: without a device)."""
        from PySide6.QtWidgets import QInputDialog

        none = "No device (open a capture file)"
        names = [instrument.name for instrument in instruments] + [none]
        name, accepted = QInputDialog.getItem(self, "Data view", "Capture with:", names, 0, False)
        if not accepted or name == none:
            return None
        return instruments[names.index(name)]

    def open_project(self, root: str) -> Optional[QWidget]:
        """Open the files of a project (those its ``project.yaml`` lists in ``open``, else its panels
        and flows)."""
        from ...lab import templates
        from ...lab.project import Project

        paths = templates.files_to_open(root)
        if not paths:
            try:
                project = Project.open(root)
            except (OSError, ValueError) as error:
                messages.error(self, "Open project", f"{root} could not be opened.", str(error))
                return None
            paths = project.panels() or project.flows()[:3]
        opened = None
        for path in paths:
            opened = opened or self.open_file(path)
        if opened is None:
            messages.info(self, "Open project", f"{os.path.basename(root.rstrip(os.sep))} has no flow or panel to open.",
                          "Add one with File → New, then save it into the project's flows or panels folder.")
            return None
        recent.add(root, "projects")
        return opened

    def new_project_dialog(self) -> Optional[QWidget]:
        """*File → New → Project from an example…*: the start page, with the examples to start from."""
        page = self.show_start_page()
        page.filter_edit.setFocus()
        return page

    def open_project_dialog(self) -> Optional[QWidget]:
        """*Project → Open project…*: a folder with a ``project.yaml``."""
        root = QFileDialog.getExistingDirectory(self, "Open project", preferences.default_folder())
        if not root:
            return None
        if not os.path.exists(os.path.join(root, "project.yaml")):
            messages.warning(self, "Open project", f"{os.path.basename(root)} is not an openSciLab project.",
                             "A project folder has a project.yaml; File → New → Project from an example creates one.")
            return None
        return self.open_project(root)

    def open_file_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open file", "", plugin_sections.open_dialog_filter(OPEN_FILE_FILTER))
        if path:
            self.open_file(path)

    def open_file(self, path: str) -> Optional[QWidget]:
        """Open ``path`` in the document that suits it; an open document is shown instead."""
        absolute = os.path.abspath(path)
        for document in self.area.documents():
            if document.path and os.path.abspath(document.path) == absolute:
                self.area.activate(document)
                return document
        lowered = path.lower()
        if lowered.endswith(FLOW_EXTENSIONS) or (lowered.endswith(".py") and _is_flow_script(path)):
            from ...lab.model import FlowError
            from ...lab.project import Project

            root = Project.find(path)
            project = Project.open(root) if root else None
            try:
                document = open_flow_file(path, registry=project.registry() if project else None, hub=self.hub,
                                          project=project)
            except (FlowError, OSError, SyntaxError) as error:
                messages.error(self, "Open flow", f"{os.path.basename(path)} could not be opened.", str(error))
                return None
            self.add_document(document)
            self._remember(path)
            return document
        if lowered.endswith(PANEL_EXTENSIONS):
            from ...lab.panel_model import PanelError

            try:
                document = open_panel_file(path, self.hub)
            except (PanelError, OSError) as error:
                messages.error(self, "Open panel", f"{os.path.basename(path)} could not be opened.", str(error))
                return None
            self.add_document(document)
            self._remember(path)
            return document
        if lowered.endswith(WAVE_EXTENSIONS):
            from ...core.waveform import WaveformError

            try:
                document = open_waveform_file(path, self.hub)
            except (WaveformError, OSError, ValueError) as error:
                messages.error(self, "Open waveform", f"{os.path.basename(path)} could not be opened.", str(error))
                return None
            self.add_document(document)
            self._remember(path)
            return document
        document_type = plugin_sections.document_type_for(path)
        if document_type is not None:
            try:
                document = document_type.open(path, self.services)
            except document_type.errors as error:
                messages.error(self, f"Open {document_type.title.lower()}",
                               f"{os.path.basename(path)} could not be opened.", str(error))
                return None
            self.add_document(document)
            self._remember(path)
            return document
        if lowered.endswith(CAPTURE_EXTENSIONS):
            active = self.active_document()
            if isinstance(active, DataView) and active.model.session is None and not active.dirty:
                document = active
            else:
                document = self.new_data_view()
            document.open_capture_file(path)
            if document.path is None:  # could not be opened (reported by the document)
                if document is not active and document.model.session is None:
                    self.area.close_document(document, force=True)
                return None
            self._remember(path)
            return document
        if data_kind(path) is not None:
            # tables, logs and reports open in the application the system has for them
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.abspath(path)))
            return None
        added = "".join(f", {spec.title.lower()}s ({', '.join(spec.suffixes)})"
                        for spec in plugin_sections.document_types())
        messages.warning(self, "Open file", f"{os.path.basename(path)} is not a file openSciLab can open.",
                         "Supported: captures (.lac, .lac.gz, .sr), flows (.flow.yaml, Python scripts with a flow), "
                         f"panels (.panel.yaml), waveforms (.wave.yaml, .sdl){added}.")
        return None
