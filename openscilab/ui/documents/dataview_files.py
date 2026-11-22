# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The file menu of a data view: new, open, save, save as and export of captures.

A part of :class:`~.dataview.DataView` (ARCHITECTURE.md, *Known hotspots*): the data view
combines its parts, which share its state through ``self``. Signals stay declared on the data view.
"""

from __future__ import annotations

import copy
import logging
import os

from PySide6.QtWidgets import (
    QFileDialog,
)

from ...core import capture_io
from ...core.formatting import to_thousands
from ...driver.base import (
    MAX_SAMPLE_BYTES,
)
from ...driver.emulated import EmulatedAnalyzerDriver
from .. import background, messages
from ..dialogs.capture_dialog import CaptureDialog
from ..dialogs.create_samples_dialog import CreateSamplesDialog
from .dataview_common import (
    CAPTURE_FILE_FILTER,
    COMPATIBLE_FILTER,
)

log = logging.getLogger(__name__)


class DataViewFilesMixin:
    """The file menu of a data view: new, open, save, save as and export of captures."""

    # ------------------------------------------------------------ file menu
    def new_capture(self) -> None:
        driver = self.driver or EmulatedAnalyzerDriver(5)
        dialog = CaptureDialog(driver, self, decoder_configuration=self.provider.to_list(), accept_text="Next")
        dialog.setWindowTitle("New capture - channels and timing")
        if not dialog.exec() or dialog.selected_settings is None:
            return

        session = dialog.selected_settings
        total = session.pre_trigger_samples + session.post_trigger_samples
        creator = CreateSamplesDialog(
            [channel.channel_number for channel in session.capture_channels],
            [channel.channel_name for channel in session.capture_channels],
            max_samples=total,
            initial_samples=total,
            parent=self,
        )
        if not creator.exec() or creator.samples is None:
            return

        for channel, samples in zip(session.capture_channels, creator.samples):
            channel.samples = samples

        self.model.clear_regions()
        self.current_file = None
        self._update_title()
        self.load_session(session)
        self._set_dirty(True)
        self.notify("Created a new capture", 5000)

    def open_capture(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open capture", "", CAPTURE_FILE_FILTER)
        if path:
            self.open_capture_file(path)

    def open_capture_file(self, path: str) -> None:
        """Load a capture file, as the *Open* menu entry and the CLI do."""
        if self._file_busy or not self.confirm_replace("Open capture"):
            return

        def load() -> capture_io.ExportedCapture:
            if path.lower().endswith(".sr"):
                from ...core import sigrok_session

                return capture_io.ExportedCapture(sigrok_session.load_session(path), [])
            return capture_io.load_capture(path)

        name = os.path.basename(path)
        self._file_busy = True
        try:
            # in the background: a large file does not freeze the window while it is read
            exported = background.run(self, f"Opening {name}...", load)
        except background.Cancelled:
            return
        except Exception as error:  # noqa: BLE001 - whatever a damaged file raises is shown, not lost
            messages.error(self, "Open capture", f"{name} could not be opened.", str(error))
            return
        finally:
            self._file_busy = False

        if self.source is not None:
            self.detach_source(self.source)  # a file now, not the device's captures

        self.current_file = path
        # Save keeps the form the file has: one of the original software stays readable for it
        # (Save as chooses the form)
        self._save_compatible = bool(getattr(exported, "compatible", False))
        self._loading += 1
        try:
            self.model.set_regions(exported.regions)
        finally:
            self._loading -= 1
        self.load_session(exported.session)
        self._loading += 1  # what the file holds is not an edit
        try:
            self.model.set_bookmarks(exported.bookmarks)
            self.model.set_pinned_numbers(exported.pinned)
        finally:
            self._loading -= 1
        self._reset_undo()  # the state edits are compared with has the markers and pins
        self._dirty = False
        self._update_title()
        self.notify(f"Opened {path}", 5000)

    def save_capture(self) -> None:
        """Save to the file the capture came from, or ask for a name."""
        if self.model.session is None:
            return
        if self.current_file and self.current_file.endswith((".lac", ".lac.gz")):
            self._write_capture(self.current_file, self._save_compatible)
        else:
            self.save_capture_as()

    def _too_large_to_copy(self, operation: str) -> bool:
        """A capture recorded to disk that does not fit into memory: exporting it as text (or
        saving it for the original software, a number per sample) would make a file of many
        gigabytes. Shows why and returns ``True`` then. (Saving as .lac packs the samples and
        reads them block by block: that works for any size.)"""
        session = self.model.session
        if session is None or not self.model.on_disk:
            return False
        size = self.model.sample_count * len(session.capture_channels)
        if size <= MAX_SAMPLE_BYTES:
            return False
        messages.warning(
            self,
            operation,
            f"This capture was recorded to disk and holds {to_thousands(size)} samples, too many "
            "to write as text.",
            f"Captures of up to {to_thousands(MAX_SAMPLE_BYTES)} samples over all channels can be "
            "exported; save it as a capture (.lac) or export a sigrok session instead.",
        )
        return True

    def save_capture_as(self) -> None:
        if self.model.session is None:
            return
        path, chosen = QFileDialog.getSaveFileName(
            self,
            "Save capture",
            self.current_file or "capture.lac",
            f"Captures (*.lac);;Compressed captures (*.lac.gz);;{COMPATIBLE_FILTER}",
        )
        if not path:
            return
        if not path.endswith((".lac", ".lac.gz")):
            # the ending of the type chosen in the dialog
            path += ".lac.gz" if chosen.startswith("Compressed") else ".lac"
        self._write_capture(path, compatible=chosen == COMPATIBLE_FILTER)

    def _write_capture(self, path: str, compatible: bool = False) -> None:
        """``compatible``: as the original LogicAnalyzer software writes its captures (a number
        per sample: large, but that software can open it)."""
        if self._file_busy or (compatible and self._too_large_to_copy("Save capture")) or \
                not self.wait_for_transfer("Save capture"):
            return
        session = self.model.session
        regions = list(self.model.regions)
        bookmarks = [(bookmark.sample, bookmark.name) for bookmark in self.model.bookmarks]
        shown_states = getattr(self, "_state_session", None)
        if shown_states is not None:
            # the states shown on their real time: the file holds the states (the capture), not
            # the picture of them
            session, regions, bookmarks = shown_states
        # What is written is the capture as it is now: the user may go on editing while it is
        # saved (edits replace the sample arrays, they never change them).
        snapshot = copy.copy(session)
        snapshot.capture_channels = [copy.copy(channel) for channel in session.capture_channels]
        snapshot.analog_channels = [copy.copy(channel) for channel in session.analog_channels]
        pinned = self.model.pinned_numbers()
        name = os.path.basename(path)
        saved_session, saved_step = self.model.session, self.undo.index()
        self._file_busy = True
        try:
            # (not to be cancelled: the file would be replaced later all the same)
            background.run(self, f"Saving {name}...",
                           lambda: capture_io.save_capture(path, snapshot, regions, bookmarks=bookmarks,
                                                           pinned=pinned, compatible=compatible),
                           cancellable=False)
        except (OSError, ValueError) as error:
            messages.error(self, "Save capture", f"{name} could not be saved.", str(error))
            return
        except background.Cancelled:
            return  # (cannot happen for a wait that cannot be cancelled; never as an error of a slot)
        finally:
            self._file_busy = False

        self.current_file = path
        self._save_compatible = compatible
        if self.model.session is not saved_session or self.undo.index() != saved_step:
            # While the file was written (the window stays alive), a capture arrived or the data
            # was edited: what is shown now is not what the file holds.
            self._update_title()
            self.notify(f"Saved {path} (as it was when saving started)", 8000)
            return
        self._dirty = False
        self._edited = False
        self._save_count += 1
        self.undo.setClean()
        self._update_title()
        self.notify(f"Saved {path}", 5000)

    def export_capture(self, kind: str) -> None:
        session = self.model.session
        # A sigrok session is written block by block, also from a capture on disk
        if session is None or (kind != "sr" and self._too_large_to_copy("Export")) or \
                not self.wait_for_transfer("Export"):
            return

        base = os.path.splitext(os.path.basename(self.current_file or "capture"))[0]
        if kind == "sr":
            from ...core import sigrok_session

            path, _ = QFileDialog.getSaveFileName(
                self, "Export as sigrok session", f"{base}.sr", "sigrok session (*.sr)"
            )
            exporter = sigrok_session.save_session
            extension = ".sr"
        elif kind == "csv":
            path, _ = QFileDialog.getSaveFileName(
                self, "Export as CSV", f"{base}.csv", "Comma separated values (*.csv)"
            )
            exporter = capture_io.export_csv
            extension = ".csv"
        else:
            path, _ = QFileDialog.getSaveFileName(
                self, "Export as VCD", f"{base}.vcd", "Value change dump (*.vcd)"
            )
            exporter = capture_io.export_vcd
            extension = ".vcd"

        if not path:
            return
        if not path.lower().endswith(extension):
            path += extension

        if self._file_busy:
            return
        self._file_busy = True
        try:
            background.run(self, f"Exporting {os.path.basename(path)}...", lambda: exporter(path, session),
                           cancellable=False)
        except (OSError, ValueError) as error:
            messages.error(self, "Export", f"{os.path.basename(path)} could not be written.", str(error))
            return
        except background.Cancelled:
            return
        finally:
            self._file_busy = False

        self.notify(f"Exported {path}", 5000)
