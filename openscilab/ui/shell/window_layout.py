# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The layout of the window: docks shown and sized, saved layouts, the state kept between runs, the
command palette.

A part of :class:`~.main_window.ShellWindow` (ARCHITECTURE.md, *Known hotspots*): the window
combines its parts, which share its state through ``self``. Signals stay declared on the window.
"""

from __future__ import annotations

import logging
import os

from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtWidgets import (
    QDockWidget,
    QInputDialog,
)

from ...core import preferences, recent, settings
from ..documents.base import Document
from ..theme import size
from .command_palette import (
    Command,
    CommandPalette,
    commands_from_actions,
    commands_from_menus,
    unique,
)
from .window_common import (
    DOCK_SHARES,
    LAYOUTS_FILE,
    SHELL_LAYOUT_VERSION,
    SHELL_STATE_FILE,
    _on_a_screen,
)

log = logging.getLogger(__name__)


class ShellLayoutMixin:
    """The layout of the window: docks shown and sized, saved layouts, the state kept between runs, the"""

    # ======================================================== view toggles
    def toggle_sidebar(self) -> None:
        self.activity_bar.set_sidebar_visible(not self.sidebar_dock.isVisible())

    def toggle_inspector(self) -> None:
        self.inspector_dock.setVisible(not self.inspector_dock.isVisible())

    def toggle_console(self) -> None:
        self.console_dock.setVisible(not self.console_dock.isVisible())

    # ===================================================== command palette
    def commands(self) -> list[Command]:
        """Every command of the shell menus and the active document."""
        document = self.active_document()
        menus = [self.project_menu, self.edit_menu]
        if isinstance(document, Document) and not self.area.is_detached(document):
            menus += document.document_menus()
        menus += [self.view_menu, self.run_menu, self.devices_menu, self.examples_menu, self.help_menu]
        result = commands_from_menus(menus)
        if isinstance(document, Document):
            result += commands_from_actions(document.document_actions(), document.title)
        for open_document in self.area.documents():
            result.append(Command(f"Go to › {open_document.title}",
                                  lambda d=open_document: self.area.activate(d), key=f"go:{id(open_document)}"))
        for path in recent.entries("files"):
            result.append(Command(f"Open recent › {os.path.basename(path)}", lambda p=path: self.open_file(p),
                                  key=f"recent:{path}"))
        return unique(command for command in result if command.title != "Edit › Command palette")

    def show_command_palette(self) -> CommandPalette:
        if self._palette is not None:
            self._palette.close()
            self._palette.deleteLater()  # one palette at a time, not a hidden one per use
        self._palette = CommandPalette(self.commands(), self)
        self._palette.popup()
        return self._palette


    # ============================================================== layouts
    def _layouts(self) -> dict:
        data = settings.get_settings(LAYOUTS_FILE)
        return data if isinstance(data, dict) else {}

    def _rebuild_layout_menu(self) -> None:
        self.layout_menu.clear()
        self.layout_menu.addAction("Save layout as...", self.save_layout_dialog)
        names = sorted(self._layouts())
        if names:
            self.layout_menu.addSeparator()
            for name in names:
                self.layout_menu.addAction(name, lambda n=name: self.restore_layout(n))
            remove = self.layout_menu.addMenu("Delete layout")
            for name in names:
                remove.addAction(name, lambda n=name: self.delete_layout(n))
        self.layout_menu.addSeparator()
        self.layout_menu.addAction("Reset layout", self.reset_layout)

    def save_layout_dialog(self) -> None:
        name, accepted = QInputDialog.getText(self, "Save layout", "Name of the layout:")
        if accepted and name.strip():
            self.save_layout(name.strip())

    def save_layout(self, name: str) -> None:
        layouts = self._layouts()
        layouts[name] = bytes(self.saveState(SHELL_LAYOUT_VERSION).toBase64()).decode("ascii")
        settings.persist_settings(LAYOUTS_FILE, layouts)

    def restore_layout(self, name: str) -> bool:
        state = self._layouts().get(name)
        if not isinstance(state, str):
            return False
        return self.restoreState(QByteArray.fromBase64(state.encode("ascii")), SHELL_LAYOUT_VERSION)

    def delete_layout(self, name: str) -> None:
        layouts = self._layouts()
        layouts.pop(name, None)
        settings.persist_settings(LAYOUTS_FILE, layouts)

    def reset_layout(self) -> None:
        self.restoreState(self._default_state, SHELL_LAYOUT_VERSION)
        self.sidebar_dock.setVisible(True)
        self.inspector_dock.setVisible(True)
        self.console_dock.setVisible(False)
        QTimer.singleShot(0, self, self.size_docks)  # (once the restored layout is laid out)

    def dock_width(self, name: str) -> int:
        """The width the dock ``name`` (``sidebar``, ``inspector``, ``node-palette``) is sized to: its width
        of the theme (larger with the text), at most its share of the window (:data:`DOCK_SHARES`), at
        least what its contents need."""
        dock = {"sidebar": self.sidebar_dock, "inspector": self.inspector_dock, "node-palette": self.nodes_dock}[name]
        wanted = min(size(name), round(DOCK_SHARES[name] * self.width()))
        return max(wanted, dock.minimumSizeHint().width(), dock.minimumWidth())

    def size_docks(self) -> None:
        """The sidebar and the inspector at their widths of a new layout (:meth:`dock_width`)."""
        self.resizeDocks([self.sidebar_dock, self.inspector_dock],
                         [self.dock_width("sidebar"), self.dock_width("inspector")], Qt.Horizontal)

    def _sidebar_shown(self, visible: bool) -> None:
        """The sidebar opened: at its width the first time of a session, or when it takes more than a
        third of the window (a layout of a smaller window, a dock dragged wide)."""
        if not visible or not self.sidebar_dock.isVisible():
            return
        if not self._sidebar_opened or self.sidebar_dock.width() > self.width() / 3 \
                or (self.nodes_dock.isVisible() and self.nodes_dock.width() < self.dock_width("node-palette") * 0.6):
            self._sidebar_opened = True
            # (the node palette beside it keeps its width: it was squeezed to a strip before)
            QTimer.singleShot(0, self, lambda: self._resize_docks_to(
                [self.sidebar_dock, self.nodes_dock] if self.nodes_dock.isVisible() else [self.sidebar_dock],
                [self.dock_width("sidebar"), self.dock_width("node-palette")] if self.nodes_dock.isVisible()
                else [self.dock_width("sidebar")]))

    def _resize_docks_to(self, docks: list[QDockWidget], widths: list[int], attempt: int = 0) -> None:
        """``docks`` at ``widths``; sized again a moment later while they are not (a dock just shown is
        laid out by an event of its own: on a busy computer that can come after the resize, which is lost)."""
        if not all(dock.isVisible() for dock in docks):
            self._last_dock_resize = (widths, attempt, "hidden")
            return
        self.resizeDocks(docks, widths, Qt.Horizontal)
        taken = [dock.width() for dock in docks]
        self._last_dock_resize = (widths, attempt, taken)
        if attempt + 1 < self.DOCK_ATTEMPTS and taken != widths:
            QTimer.singleShot(self.DOCK_RETRY, self, lambda: self._resize_docks_to(docks, widths, attempt + 1))

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        if not self._docks_sized:
            self._docks_sized = True
            QTimer.singleShot(0, self, self.size_docks)  # (once the window has its size)

    def _restore_state(self) -> bool:
        """The window as it was; whether its layout was restored."""
        state = settings.get_settings(SHELL_STATE_FILE)
        if not isinstance(state, dict):
            return False
        restored = False
        try:
            width, height = int(state.get("width", 0)), int(state.get("height", 0))
            if width > 400 and height > 300:
                self.resize(width, height)
            if "x" in state and "y" in state and _on_a_screen(int(state["x"]), int(state["y"]),
                                                              self.width(), self.height()):
                self.move(int(state["x"]), int(state["y"]))  # not where no screen is any more
            layout = state.get("layout")
            if isinstance(layout, str):
                restored = self.restoreState(QByteArray.fromBase64(layout.encode("ascii")), SHELL_LAYOUT_VERSION)
            section = state.get("section")
            if section in self.activity_bar.keys():
                self.activity_bar.select(section)
            self.activity_bar.set_sidebar_visible(state.get("sidebar") is True)
            if state.get("maximized"):
                QTimer.singleShot(0, self, self.showMaximized)
        except (TypeError, ValueError):
            return restored
        return restored

    def restore_session(self) -> bool:
        """Open the documents (and connect the devices) of the last session; whether any opened."""
        from . import session

        state = settings.get_settings(SHELL_STATE_FILE)
        if not isinstance(state, dict):
            return False
        return session.restore(self, state.get("session"), preferences.get("startup.reconnect_devices"))

    def save_state(self) -> None:
        from . import session

        geometry = self.normalGeometry()
        settings.persist_settings(SHELL_STATE_FILE, {
            "session": session.session_state(self),
            "x": geometry.x(), "y": geometry.y(), "width": geometry.width(), "height": geometry.height(),
            "maximized": self.isMaximized(),
            "layout": bytes(self.saveState(SHELL_LAYOUT_VERSION).toBase64()).decode("ascii"),
            "section": self.activity_bar.current,
            "sidebar": self.sidebar_dock.isVisible(),
        })
