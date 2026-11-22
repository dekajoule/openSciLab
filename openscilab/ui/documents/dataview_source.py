# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Where the samples of a data view come from (a device, a stream, a file) and captures that
arrive progressively.

A part of :class:`~.dataview.DataView` (ARCHITECTURE.md, *Known hotspots*): the data view
combines its parts, which share its state through ``self``. Signals stay declared on the data view.
"""

from __future__ import annotations

import html
import logging
from typing import Optional

import numpy as np
from PySide6.QtCore import QEventLoop, Qt, QTimer
from PySide6.QtWidgets import (
    QInputDialog,
    QProgressDialog,
)

from ...core.hub import EVENT_REMOVED
from ...core.instrument import Instrument
from ...driver.base import (
    AnalyzerDriverBase,
)
from .. import messages
from ..widgets.analog_viewer import derive_digital
from .dataview_common import (
    TRANSFER_CHECK_MS,
)

log = logging.getLogger(__name__)


class DataViewSourceMixin:
    """Where the samples of a data view come from (a device, a stream, a file) and captures that"""

    # ---------------------------------------------------------------- source
    @property
    def driver(self) -> Optional[AnalyzerDriverBase]:
        """The driver of the instrument the data come from (``None`` for files and test signals)."""
        return self.source.driver if self.source is not None else None

    @property
    def instrument(self) -> Optional[Instrument]:
        return self.source.instrument if self.source is not None else None

    def use_instrument(self, instrument: Instrument) -> None:
        """Show the captures of ``instrument`` of the hub (its device card starts them)."""
        from ..devices.capture import capture_controller

        controller = capture_controller(instrument)
        if controller is None:
            messages.warning(self, "Data", f"{instrument.name} cannot capture.")
            return
        self.attach_source(controller)

    def attach_source(self, controller) -> None:
        """Captures of ``controller`` go to this view; one view per instrument at a time."""
        if controller is self.source:
            return
        if self.viewer:
            self.set_viewer(False)  # it captures from now on
        if self.source is not None:
            self.detach_source(self.source)
        previous = controller.view
        if previous is not None and previous is not self:
            previous.detach_source(controller)
        controller.view = self
        self.source = controller
        controller.instrument.owner = self
        pending, controller.pending_decoders = controller.pending_decoders, None
        if pending is not None:  # (of a profile loaded while no view captured for the device)
            self.decoder_manager.load_configuration(pending)
        controller.changed.connect(self._update_actions)
        self.capture_controls.bind(controller, self.hub)
        self._update_actions()
        self._fill_device_combo()
        self.document_changed.emit()  # the header's Capture follows

    def detach_source(self, controller) -> None:
        if controller is not self.source:
            return
        try:
            controller.changed.disconnect(self._update_actions)
        except (RuntimeError, TypeError):
            pass
        if controller.view is self:
            controller.view = None
        if controller.instrument.owner is self:
            controller.instrument.owner = None
        self.source = None
        self._running_capture = None
        self.capture_controls.bind(None)
        self._update_actions()
        self._fill_device_combo()
        self.document_changed.emit()

    def _update_source_label(self) -> None:
        """The device chooser follows the source (and the instruments of the hub)."""
        self._fill_device_combo()

    def _on_hub_event(self, event) -> None:
        self._fill_device_combo()
        if event.kind == EVENT_REMOVED and self.source is not None and event.name == self.source.name \
                and self.source.instrument not in self.hub:
            source = self.source
            self.detach_source(source)
            source.close()

    # ------------------------------------------------------------ progressive
    def _on_tile(self, args) -> None:
        """Parts of a progressively transferred capture arrived."""
        if args.session is not self.model.session:
            return
        progressive = args.session.progressive
        if args.error:
            self.show_status(f"Transfer interrupted: {args.error}")
            self._set_transfer_notice(True)
            return
        self.model.tiles_loaded()
        if progressive is not None and progressive.complete:
            self.show_status("")
            self.notify("Transfer complete", 5000)
            self._set_transfer_notice(False)
            self._update_info_panel()
            self._update_actions()
        elif progressive is not None:
            self.show_status(f"Transferring the capture: {progressive.fraction * 100:.0f} %")

    def _prioritize_visible(self) -> None:
        session = self.model.session
        if session is None or self.model.progressive is None or self.driver is None:
            return
        self.driver.prioritize(session, self.model.first_sample, self.model.first_sample + self.model.visible_samples)

    def _set_transfer_notice(self, interrupted: bool) -> None:
        self.transfer_banner.set_message(
            "The transfer of the capture stopped before all of it arrived." if interrupted else "",
            "Resume transfer" if interrupted else None)
        self._update_banners()

    def _update_banners(self) -> None:
        self._banner_holder.setVisible(not self.transfer_banner.isHidden() or not self.capture_banner.isHidden())

    def show_capture_problem(self, text: str, details: str = "", kind: str = "error") -> None:
        """A problem of the last capture above the waveform (``""`` hides it)."""
        from ..theme import set_role

        set_role(self.capture_banner, f"banner-{kind}")
        message = html.escape(text) + (f"<br><span style='opacity:0.8'>{html.escape(details)}</span>" if details else "")
        self.capture_banner.set_message(message if text else "", "Open the device" if self.source is not None else
                                        ("Dismiss" if text else None))
        self._update_banners()

    def _capture_banner_action(self) -> None:
        if self.source is not None:
            self.device_card_requested.emit(self.source.instrument)
        else:
            self.show_capture_problem("")

    def resume_transfer(self) -> bool:
        session = self.model.session
        if session is None or self.driver is None or not self.driver.resume_transfer(session):
            messages.warning(self, "Transfer", "The transfer cannot be resumed.",
                             "Capture again to get the samples.")
            return False
        self._set_transfer_notice(False)
        self.show_status("Transfer resumed")
        return True

    def wait_for_transfer(self, operation: str) -> bool:
        """``operation`` needs the whole capture: wait (with progress) until it arrived."""
        progressive = self.model.progressive
        if progressive is None:
            return True
        dialog = QProgressDialog(f"{operation}: waiting for the rest of the capture...", "Cancel", 0, 100, self)
        dialog.setWindowTitle(operation)
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setMinimumDuration(0)
        # an event loop that ends when the capture is there (or the transfer stopped, or the user
        # cancels), looked at a few times a second – not a loop that spins the application
        loop = QEventLoop()
        timer = QTimer()
        timer.setInterval(TRANSFER_CHECK_MS)

        def check() -> None:
            if self.model.progressive is None or progressive.interrupted:
                loop.quit()
            else:
                dialog.setValue(int(progressive.fraction * 100))

        timer.timeout.connect(check)
        dialog.canceled.connect(loop.quit)
        dialog.setValue(int(progressive.fraction * 100))
        if self.model.progressive is not None and not progressive.interrupted:
            timer.start()
            loop.exec()
            timer.stop()
        cancelled = dialog.wasCanceled()
        waiting = self.model.progressive is not None
        dialog.close()
        dialog.deleteLater()
        if cancelled:
            return False
        if waiting and progressive.interrupted:
            messages.warning(self, operation, "The transfer stopped before the capture was complete.",
                             "Resume the transfer first.")
            return False
        return True

    def derive_digital_channel(self, channel) -> Optional[object]:
        """A digital channel from an analog one: above a threshold, with hysteresis."""
        low, high = self.model.analog_range(channel)
        threshold, accepted = QInputDialog.getDouble(
            self, "Digital channel", f"Threshold of {channel.display_name} ({channel.unit}):",
            (low + high) / 2, low - abs(high - low), high + abs(high - low), 3)
        if not accepted:
            return None
        hysteresis, accepted = QInputDialog.getDouble(
            self, "Digital channel", f"Hysteresis ({channel.unit}):", abs(high - low) * 0.05, 0.0,
            abs(high - low), 3)
        if not accepted:
            return None
        return self.add_derived_channel(channel, threshold, hysteresis)

    def add_derived_channel(self, channel, threshold: float, hysteresis: float):
        session = self.model.session
        if session is None or not self.wait_for_transfer("Digital channel"):
            return None
        number = max([c.channel_number for c in session.capture_channels] + [-1]) + 1
        derived = derive_digital(channel, threshold, hysteresis, number)
        count = session.sample_count() if session.capture_channels else len(derived.samples)
        derived.samples = derived.samples[:count] if len(derived.samples) >= count else np.concatenate(
            [derived.samples, np.zeros(count - len(derived.samples), np.uint8)])
        session.capture_channels.append(derived)
        self._loading += 1
        try:
            self.model.rebuild_transitions()
            self.model.notify_channels_changed()
            self.model.notify_capture_changed()
        finally:
            self._loading -= 1
        self._set_dirty(True)
        self.record_edit(f"Add {derived.channel_name}")
        self.notify(f"Added {derived.channel_name}", 5000)
        return derived

    def _has_real_device(self) -> bool:
        return self.driver is not None and self.driver.is_hardware
