# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Editing the samples of a data view: regions, copy, cut, delete, paste, insert, shift and the
channels shown or hidden.

A part of :class:`~.dataview.DataView` (ARCHITECTURE.md, *Known hotspots*): the data view
combines its parts, which share its state through ``self``. Signals stay declared on the data view.
"""

from __future__ import annotations

import logging

import numpy as np
from PySide6.QtCore import Qt

from ...core.formatting import to_thousands
from ...core.regions import SampleRegion
from .. import messages
from ..dialogs.annotation_list import AnnotationListWindow
from ..dialogs.create_samples_dialog import CreateSamplesDialog
from ..dialogs.measure_dialog import MeasureDialog
from ..dialogs.region_dialog import RegionDialog
from ..dialogs.shift_dialog import ShiftChannelsDialog, ShiftDirection, ShiftMode
from .dataview_common import (
    INSERT_LIMIT,
)

log = logging.getLogger(__name__)


class DataViewSamplesMixin:
    """Editing the samples of a data view: regions, copy, cut, delete, paste, insert, shift and the"""

    # ---------------------------------------------------------- sample edits
    def _selection_out_of_range(self) -> None:
        messages.warning(self, "Selection", "The selection is outside the capture.")

    def create_region(self, first_sample: int, last_sample: int) -> None:
        region = SampleRegion(first_sample=first_sample, last_sample=last_sample)
        dialog = RegionDialog(region, self)
        if dialog.exec():
            self.model.add_region(region)
        self.sample_marker.clear_selection()

    def measure_samples(self, first_sample: int, sample_count: int) -> None:
        session = self.model.session
        if session is None:
            return
        if first_sample + sample_count > self.model.sample_count:
            self._selection_out_of_range()
            return
        MeasureDialog(
            session.capture_channels, first_sample, sample_count, session.frequency, self
        ).exec()

    def show_annotation_list(self, group, annotation, segment=None) -> AnnotationListWindow:
        """Open (or bring up) the list window of an annotation row, selecting ``segment``."""
        key = (id(group.instance), annotation.name)
        window = self._annotation_lists.get(key)
        if window is None:
            window = AnnotationListWindow(self.model, group, annotation, self)
            window.setAttribute(Qt.WA_DeleteOnClose)
            window.destroyed.connect(lambda *_: self._annotation_lists.pop(key, None))
            self._annotation_lists[key] = window
        window.show()
        window.raise_()
        window.activateWindow()
        if segment is not None:
            window.select_segment(segment)
        return window

    def _on_selection(self, action) -> bool:
        """Run ``action(first, count)`` on the selection in the ruler (none: says how to make one)."""
        selection = self.sample_marker.selection
        if selection is None or selection.sample_count <= 0:
            self.notify("Select samples first: drag in the ruler above the waveform", 5000)
            return False
        result = action(selection.start, selection.sample_count)
        return result is not False

    def _edit_position(self) -> int:
        selection = self.sample_marker.selection
        if selection is not None:
            return selection.start
        marker = self.model.user_marker if hasattr(self.model, "user_marker") else None
        return marker if marker is not None else self.model.first_sample

    def _paste_at_marker(self) -> None:
        if self.clipboard_samples is None:
            self.notify("Nothing to paste: copy samples first", 5000)
            return
        self.paste_samples(self._edit_position())

    def copy_samples(self, first_sample: int, sample_count: int) -> bool:
        session = self.model.session
        if session is None:
            return False
        if first_sample < 0 or first_sample + sample_count > self.model.sample_count:
            self._selection_out_of_range()
            return False
        self.clipboard_samples = [
            channel.samples[first_sample : first_sample + sample_count].copy()
            if channel.samples is not None
            else np.zeros(sample_count, dtype=np.uint8)
            for channel in session.capture_channels
        ]
        self.sample_marker.has_clipboard = True
        self.notify(f"Copied {to_thousands(sample_count)} samples", 5000)
        return True

    def cut_samples(self, first_sample: int, sample_count: int) -> None:
        if self.copy_samples(first_sample, sample_count):
            self.delete_samples(first_sample, sample_count)

    def delete_samples(self, first_sample: int, sample_count: int) -> None:
        session = self.model.session
        if session is None or sample_count <= 0:
            return
        total = self.model.sample_count
        if not 0 <= first_sample < total:
            self._selection_out_of_range()
            return

        sample_count = min(sample_count, total - first_sample)
        last_sample = first_sample + sample_count - 1

        for channel in session.capture_channels:
            if channel.samples is None:
                continue
            channel.samples = np.concatenate(
                (channel.samples[:first_sample], channel.samples[first_sample + sample_count :])
            )

        if first_sample < session.pre_trigger_samples:
            removed_before_trigger = min(sample_count, session.pre_trigger_samples - first_sample)
            session.pre_trigger_samples -= removed_before_trigger

        # ``sample_count`` is read back from the channels, which were already
        # trimmed above.
        session.post_trigger_samples = max(
            self.model.sample_count - session.pre_trigger_samples, 0
        )
        session.loop_count = 0
        session.measure_bursts = False
        session.bursts = None

        self._loading += 1
        try:
            self._remap_regions_after_delete(first_sample, last_sample, sample_count)
        finally:
            self._loading -= 1
        self._after_samples_modified(first_sample, f"Delete {to_thousands(sample_count)} samples")
        self.notify(f"Deleted {to_thousands(sample_count)} samples", 5000)

    def _remap_regions_after_delete(
        self, first_sample: int, last_sample: int, sample_count: int
    ) -> None:
        remaining = []
        for region in self.model.regions:
            start, end = region.start, region.end
            if start >= first_sample and end <= last_sample:
                continue  # fully inside the deleted range
            if end < first_sample:
                remaining.append(region)
                continue
            if start > last_sample:
                remaining.append(region.shifted(-sample_count))
                continue
            # Partially overlapping: clip and shift what is left.
            new_start = start if start < first_sample else first_sample
            new_end = end - sample_count if end > last_sample else first_sample
            if new_end - new_start < 1:
                continue
            region.first_sample = new_start
            region.last_sample = new_end
            remaining.append(region)
        self.model.set_regions(remaining)

    def paste_samples(self, sample: int) -> None:
        if self.clipboard_samples is None:
            return
        self._insert_samples(sample, self.clipboard_samples)

    def insert_samples_dialog(self, sample: int) -> None:
        session = self.model.session
        if session is None:
            return

        # a file or test signals have no device that limits the length
        maximum = self.driver.get_limits(session.channel_numbers).max_total_samples if self.driver is not None \
            else INSERT_LIMIT
        available = max(maximum - self.model.sample_count, 1)
        dialog = CreateSamplesDialog(
            session.channel_numbers,
            [channel.channel_name for channel in session.capture_channels],
            max_samples=available,
            initial_samples=min(100, available),
            insert_mode=True,
            parent=self,
        )
        if not dialog.exec() or dialog.samples is None:
            return
        self._insert_samples(sample, dialog.samples)

    def _insert_samples(self, sample: int, new_samples: list[np.ndarray]) -> None:
        session = self.model.session
        if session is None or not new_samples:
            return

        total = self.model.sample_count
        if sample > total:
            messages.warning(self, "Insert samples", "Samples cannot be inserted beyond the end of the capture.")
            return

        count = int(new_samples[0].size)
        if self.driver is not None:
            maximum = self.driver.get_limits(session.channel_numbers).max_total_samples
            if total + count > maximum:
                messages.error(
                    self,
                    "Insert samples",
                    "The capture would become too long.",
                    f"This channel mode holds at most {to_thousands(maximum)} samples.",
                )
                return

        for index, channel in enumerate(session.capture_channels):
            addition = new_samples[index] if index < len(new_samples) else np.zeros(count, np.uint8)
            # a channel without samples is as long as the others (low), so all stay equally long
            samples = channel.samples if channel.samples is not None else np.zeros(total, np.uint8)
            channel.samples = np.concatenate((samples[:sample], addition, samples[sample:]))

        if sample <= session.pre_trigger_samples:
            session.pre_trigger_samples += count
        session.post_trigger_samples = max(
            self.model.sample_count - session.pre_trigger_samples, 0
        )
        session.loop_count = 0
        session.measure_bursts = False
        session.bursts = None

        self._loading += 1
        try:
            self.model.set_regions(
                [
                    region.shifted(count) if region.start >= sample else region
                    for region in self.model.regions
                ]
            )
        finally:
            self._loading -= 1
        self._after_samples_modified(sample, f"Insert {to_thousands(count)} samples")
        self.notify(f"Inserted {to_thousands(count)} samples", 5000)

    def shift_channels(self) -> None:
        session = self.model.session
        if session is None:
            return

        dialog = ShiftChannelsDialog(
            session.capture_channels, max(self.model.sample_count - 1, 1), self
        )
        if not dialog.exec():
            return

        for channel in dialog.shifted_channels:
            samples = channel.samples
            if samples is None or samples.size == 0:
                continue
            amount = min(dialog.shift_amount, samples.size)  # per channel: a short one limits only itself

            if dialog.direction == ShiftDirection.LEFT:
                head = samples[amount:]
                tail = self._fill_samples(dialog.mode, samples[:amount], amount)
                channel.samples = np.concatenate((head, tail))
            else:
                head = self._fill_samples(dialog.mode, samples[samples.size - amount :], amount)
                channel.samples = np.concatenate((head, samples[: samples.size - amount]))

        self._after_samples_modified(self.model.first_sample, "Shift channels")
        self.notify(f"Shifted {len(dialog.shifted_channels)} channel(s)", 5000)

    @staticmethod
    def _fill_samples(mode: ShiftMode, rotated: np.ndarray, amount: int) -> np.ndarray:
        if mode == ShiftMode.HIGH:
            return np.ones(amount, dtype=np.uint8)
        if mode == ShiftMode.LOW:
            return np.zeros(amount, dtype=np.uint8)
        return rotated.copy()

    def use_names_for_device(self) -> bool:
        """The channel names of this data view for the next capture of its device."""
        if self.source is None or self.model.session is None:
            return False
        names = {channel.channel_number: channel.channel_name for channel in self.model.session.capture_channels}
        if self.source.use_channel_names(names):
            self.notify(f"{self.source.name} names its channels like this from the next capture",
                                         6000)
            return True
        return False

    def _fill_hidden_menu(self) -> None:
        self.hidden_menu.clear()
        hidden = [channel for channel in self.model.channels if channel.hidden]
        self.hidden_menu.addAction("Show all channels", lambda: self.channel_viewer.show_all_channels())
        if hidden:
            self.hidden_menu.addSeparator()
        for channel in hidden:
            self.hidden_menu.addAction(f"Show {channel.display_name}", lambda channel=channel: self._show_channel(channel))

    def _show_channel(self, channel) -> None:
        channel.hidden = False
        self.model.notify_channels_changed()

    def _update_hidden_button(self) -> None:
        hidden = sum(1 for channel in self.model.channels if channel.hidden)
        self.show_all_button.setText(f"{hidden} hidden" if hidden else "Show all")
        self.show_all_button.setEnabled(bool(hidden))

    def _undo_moved(self, _index: int) -> None:
        if self.undo.isClean() and self._save_count and self.current_file:
            self._dirty = self._edited = False  # back to what was saved
        elif self.undo.index() or self.undo.count():
            self._dirty = True
        self.document_changed.emit()

    def _after_samples_modified(self, first_sample: int, text: str = "Edit samples") -> None:
        self._set_dirty(True)
        self.record_edit(text)
        self.model.rebuild_transitions()
        self.model.notify_capture_changed()
        self.model.set_view(first_sample, self.model.visible_samples)
        self.sample_marker.clear_selection()
        # (the decoders run again through capture_changed)
