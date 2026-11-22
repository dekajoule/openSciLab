# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Projects and their data: the project of a document, temporary projects, saving, comparing and
exporting captures.

A part of :class:`~.main_window.ShellWindow` (ARCHITECTURE.md, *Known hotspots*): the window
combines its parts, which share its state through ``self``. Signals stay declared on the window.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from PySide6.QtWidgets import (
    QFileDialog,
)

from ...core import preferences, recent
from .. import background, messages
from ..documents.dataview import DataView

log = logging.getLogger(__name__)


class ShellProjectsMixin:
    """Projects and their data: the project of a document, temporary projects, saving, comparing and"""

    # ----------------------------------------------------------------- data
    @staticmethod
    def data_folder(document) -> Optional[str]:
        """The ``data/`` folder of the project of ``document`` (or next to its file, or the scratch
        folder of an unsaved flow)."""
        project = getattr(document, "project", None)
        if project is not None:
            return project.data_dir
        path = getattr(document, "path", None)
        if path is None and getattr(document, "document_kind", "") == "flow":
            return document.data_dir
        if path:
            candidate = os.path.join(os.path.dirname(os.path.abspath(path)), "data")
            if os.path.isdir(candidate):
                return candidate
        return None

    def compare_captures(self, capture: str, reference: str):
        """Open ``capture`` and compare it with ``reference`` (the compare dialog of the analyzer)."""
        from ..dialogs.compare_dialog import CompareDialog

        document = self.open_file(capture)
        if not isinstance(document, DataView):
            return None
        dialog = CompareDialog(document.model, document)
        dialog.set_reference_file(reference)
        dialog.compare()
        dialog.show()
        return dialog

    def export_capture_csv(self, path: str, target: Optional[str] = None) -> Optional[str]:
        from ...core import capture_io

        if target is None:
            target, _ = QFileDialog.getSaveFileName(self, "Export as CSV", os.path.splitext(path)[0] + ".csv",
                                                    "CSV files (*.csv)")
            if not target:
                return None
        def export() -> None:
            if path.lower().endswith(".sr"):
                from ...core import sigrok_session

                session = sigrok_session.load_session(path)
            else:
                session = capture_io.load_capture(path).session
            capture_io.export_csv(target, session, include_time=True)

        try:
            background.run(self, f"Exporting {os.path.basename(path)}...", export, cancellable=False)
        except Exception as error:  # noqa: BLE001 - whatever a damaged file raises is shown
            messages.error(self, "Export as CSV", f"{os.path.basename(path)} could not be exported.", str(error))
            return None
        self.project_section.refresh_data()
        return target

    # ------------------------------------------------------------ projects
    def project_root_of(self, document) -> Optional[str]:
        """The project folder of ``document`` (``None`` outside a project)."""
        from ...lab.project import Project

        project = getattr(document, "project", None)
        if project is not None:
            return os.path.abspath(project.root)
        path = getattr(document, "path", None)
        root = Project.find(path) if path else None
        return os.path.abspath(root) if root else None

    def in_temporary_project(self, path: str) -> bool:
        absolute = os.path.abspath(path)
        return any(absolute == root or absolute.startswith(root + os.sep) for root in self.temporary_projects)

    def _remember(self, path: str) -> None:
        """Put ``path`` in the recent files (not files of temporary projects: they go away)."""
        if not self.in_temporary_project(path):
            recent.add(path)
        self.project_section.refresh_recent()

    def is_temporary(self, document) -> bool:
        return self.project_root_of(document) in self.temporary_projects

    def save_project_as(self, root: Optional[str] = None, parent_folder: Optional[str] = None) -> Optional[str]:
        """Copy the project ``root`` (default: the active document's) to a folder of its own; open
        documents of it move along. Returns the new folder."""
        import shutil

        from ...lab import templates
        from ...lab.project import Project

        root = root or self.project_root_of(self.active_document())
        if root is None:
            messages.info(self, "Save project", "The active document is not part of a project.")
            return None
        if parent_folder is None:
            # the user names the project folder (default: the project's name)
            try:
                name = Project.open(root).name or os.path.basename(root.rstrip(os.sep))
            except (OSError, ValueError):
                name = os.path.basename(root.rstrip(os.sep))
            folder = preferences.default_folder(create=True)
            path, _ = QFileDialog.getSaveFileName(self, "Save project as", os.path.join(folder, name),
                                                  "Project folder (*)")
            if not path:
                return None
            parent_folder, name = os.path.dirname(path), os.path.basename(path.rstrip(os.sep))
            target = templates.unique_folder(parent_folder, name)
        else:
            target = templates.unique_folder(parent_folder, os.path.basename(root.rstrip(os.sep)))
        try:
            shutil.copytree(root, target)
        except OSError as error:
            messages.error(self, "Save project", "The project could not be copied.", str(error))
            return None
        project = Project.open(target)
        for document in self.area.documents():
            path = getattr(document, "path", None)
            moved = os.path.join(target, os.path.relpath(path, root)) if path and \
                os.path.abspath(path).startswith(root + os.sep) else None
            if moved is None and self.project_root_of(document) != root:
                continue
            if moved is not None and hasattr(document, "_path"):
                document._path = moved
            if moved is not None and hasattr(document, "current_file"):
                document.current_file = moved
            if hasattr(document, "project"):
                document.project = project
            changed = getattr(document, "document_changed", None)
            if changed is not None:
                changed.emit()
        self.temporary_projects.discard(root)
        recent.add(target, "projects")
        self.statusBar().showMessage(f"Project saved in {target}", 8000)
        self._on_active_changed(self.active_document())
        return target

    def save_document(self, document, as_new: bool = False) -> bool:
        """Save ``document``; a document of a temporary project first gets the project a folder."""
        if self.is_temporary(document) and self.save_project_as(self.project_root_of(document)) is None:
            return False
        saved = document.save_as() if as_new else document.save()
        if saved and document.path:
            self._remember(document.path)
        return bool(saved)

    def save_active(self) -> bool:
        document = self.active_document()
        if document is None or not getattr(document, "can_save", lambda: False)():
            return False
        return self.save_document(document)

    def save_active_as(self) -> bool:
        document = self.active_document()
        if document is None or not getattr(document, "can_save", lambda: False)():
            return False
        return self.save_document(document, as_new=True)

    def close_active(self) -> bool:
        document = self.active_document()
        return self.area.close_document(document) if document is not None else False
