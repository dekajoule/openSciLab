# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Capturing in a data view: simulated signals, capture start, progress and completion,
state analysis, action markers and the monitor recorded.

A part of :class:`~.dataview.DataView` (ARCHITECTURE.md, *Known hotspots*): the data view
combines its parts, which share its state through ``self``. Signals stay declared on the data view.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QFileDialog,
    QWidget,
)

from ...core import alignment
from ...core import simulation as simulation_signals
from ...core.formatting import to_large_frequency, to_small_time, to_thousands
from ...core.instrument import Instrument
from ...driver.base import (
    CaptureCompletedArgs,
    CaptureError,
    CaptureProgressArgs,
)
from ...driver.emulated import EmulatedAnalyzerDriver
from ...driver.models import CaptureSession, TriggerType
from .. import messages
from ..dialogs.align_dialog import AlignDialog
from ..dialogs.bus_dialog import BusDialog
from ..dialogs.simulation_dialog import SimulationDialog
from .dataview_common import (
    LIVE_WINDOW_DIVISOR,
    long_duration,
)

log = logging.getLogger(__name__)


class DataViewCaptureMixin:
    """Capturing in a data view: simulated signals, capture start, progress and completion,"""

    # ------------------------------------------------------------ test signals
    def expect_simulation(self, dialog) -> None:
        """The next capture holds test signals of ``dialog`` (its decoders are added with it)."""
        self._pending_simulation = {
            "pattern": dialog.pattern,
            "channels": dialog.channel_count,
            "frequency": dialog.frequency,
            "add_decoders": dialog.add_decoders,
        }

    def simulated_capture(self) -> None:
        """Test signals computed on this computer (the device card has those of a board)."""
        if self.is_capturing or not self.confirm_replace("Simulated signals"):
            return
        driver = EmulatedAnalyzerDriver(1)
        dialog = SimulationDialog(driver, on_board=False, parent=self)
        if not dialog.exec() or dialog.session is None:
            return
        if self.source is not None:
            self.detach_source(self.source)
        self.expect_simulation(dialog)
        error = driver.start_capture(dialog.session, self.bridge.completed.emit)
        if error != CaptureError.NONE:
            self._pending_simulation = None
            messages.error(self, "Simulated signals", "The simulated signals could not be computed.", error.message)
            return
        self.show_status("Computing the simulated capture...")

    def _apply_simulation_decoders(self, session: CaptureSession) -> None:
        pending, self._pending_simulation = self._pending_simulation, None
        if pending is None or session.trigger_type != TriggerType.SIMULATION or not pending["add_decoders"]:
            return
        configuration = simulation_signals.decoder_configuration(
            pending["pattern"], pending["channels"], pending["frequency"], self.provider.registry
        )
        if configuration:
            self.provider.load_configuration(configuration)
            self.decoder_manager.refresh()

    # --------------------------------------------------------------- capture
    def show_panel(self, panel: QWidget) -> None:
        """Brings a tab of the analysis to the front (in the inspector of the shell)."""
        self.inspector_requested.emit()
        for index in range(self.side_tabs.count()):
            holder = self.side_tabs.widget(index)
            if holder.isAncestorOf(panel):
                self.side_tabs.setCurrentIndex(index)
                break

    def edit_bus(self, bus) -> None:
        """Defines a new bus (``bus=None``) or changes one."""
        if self.model.session is None:
            return
        dialog = BusDialog(self.model.channels, bus, self)
        if not dialog.exec():
            return
        buses = self.model.buses
        if bus is None:
            buses.append(dialog.bus)
        else:
            buses[next(index for index, item in enumerate(buses) if item is bus)] = dialog.bus
        self.model.set_buses(buses)

    def export_annotations(self) -> None:
        from ...core.annotation_export import export_annotations

        groups = [group for group in self.model.annotation_groups if not group.error]
        if self.model.session is None or not groups:
            messages.info(self, "Export decoder output", "There is no decoder output to export.", "Add a decoder first.")
            return
        base = os.path.splitext(os.path.basename(self.current_file or "capture"))[0]
        path, chosen = QFileDialog.getSaveFileName(
            self, "Export decoder output", f"{base}-decoded.csv", "CSV (*.csv);;JSON (*.json)"
        )
        if not path:
            return
        if not path.lower().endswith((".csv", ".json")):
            path += ".json" if "JSON" in chosen else ".csv"
        try:
            export_annotations(path, groups, self.model.session)
        except (OSError, ValueError) as error:
            messages.error(self, "Export decoder output", f"{os.path.basename(path)} could not be written.", str(error))
            return
        self.notify(f"Exported {path}", 5000)

    @property
    def captured_session(self) -> Optional[CaptureSession]:
        """The capture as the device made it: while a state analysis is shown, the timing capture
        its states were made of (the settings of a repeated capture)."""
        if self._timing_capture is not None:
            return self._timing_capture[0]
        return self.model.session

    def back_to_timing(self) -> None:
        """Returns from a state analysis to the capture it was made of."""
        if self._timing_capture is None:
            return
        session, regions, path = self._timing_capture
        self.current_file = path
        self.model.set_regions(regions)
        self.load_session(session)
        self._update_title()
        self.notify("Back to the timing capture", 5000)

    def state_analysis(self) -> None:
        """Resamples the loaded capture on the edges of a clock channel (a new capture of states)."""
        from ..dialogs.state_dialog import StateDialog

        session = self.model.session
        if session is None:
            return
        dialog = StateDialog(session, self)
        if not dialog.exec() or dialog.result_capture is None:
            return
        state = dialog.result_capture
        # The states keep their clock (decoders that wait for its edges get it drawn in, as in a
        # state capture of a device); a repeated capture uses the timing capture (captured_session).
        state.session.clock_name = next((channel.channel_name for channel in session.capture_channels
                                         if channel.channel_number == dialog.clock_channel), "")
        # Kept until another capture is loaded, for Back to the timing capture
        timing = (session, self.model.regions, self.current_file)
        self.current_file = None
        self._loading += 1
        try:
            self.model.set_regions([])  # they mark samples of the timing capture, not states
        finally:
            self._loading -= 1
        self.load_session(state.session)
        self._timing_capture = timing
        self._update_title()
        self._update_actions()
        self.notify(
            f"{to_thousands(state.state_count)} states clocked by channel {dialog.clock_channel + 1}", 8000
        )

    def repeat_capture(self) -> bool:
        """Capture again with the device the shown data came from."""
        if self.source is None:
            messages.warning(self, "Capture again", "These data do not come from a device.",
                             "Choose a device in the capture tool bar.")
            return False
        return self.source.repeat_capture()

    def capture_as_waveform(self, channels: Optional[list[int]] = None, analog: Optional[int] = None):
        """The shown capture as a waveform: digital ``channels`` (indexes, default all) as a pattern,
        or the analog channel ``analog`` as arbitrary points; between the cursors when both are set."""
        from ...core import waveform as waves

        session = self.model.session
        if session is None:
            return None
        first, last = 0, self.model.sample_count
        a, b = self.model.cursor("A"), self.model.cursor("B")
        if a is not None and b is not None and a != b:
            first, last = min(a, b), max(a, b) + 1
        if analog is not None:
            channel = session.analog_channels[analog]
            values = channel.volts(first, last)
            rate = channel.rate or session.frequency
            waveform = waves.from_points(values, rate=rate, repeat=1)
            waveform.name = channel.display_name
            return waveform
        chosen = [session.capture_channels[index] for index in (channels if channels is not None
                                                                else range(len(session.capture_channels)))]
        tracks = {channel.display_name: np.asarray(channel.samples[first:last], dtype=np.uint8)
                  for channel in chosen if channel.samples is not None}
        return waves.pattern(tracks, session.frequency, repeat=1, name=os.path.splitext(self.title)[0])

    def play_as_signal(self) -> None:
        """Ask what to play, then hand the waveform to a waveform document."""
        session = self.model.session
        if session is None:
            return
        options = ["Digital channels (pattern)"] + [f"Analog: {channel.display_name}"
                                                    for channel in session.analog_channels]
        choice = 0
        if len(options) > 1:
            choice = messages.choose(self, "Play as signal", "What should be played?", options)
            if choice is None:
                return
        waveform = self.capture_as_waveform(analog=None if choice == 0 else choice - 1)
        if waveform is not None:
            self.play_requested.emit(waveform)

    def _begin_capture(self, session: CaptureSession) -> bool:
        """Capture ``session`` with the source instrument (the device card does this as well)."""
        return self.source is not None and self.source.capture(session)

    def capture_started(self, session: CaptureSession) -> None:
        """The source started ``session``: its progress and result come here."""
        self._running_capture = session
        self._action_marks = []
        self._capture_started = time.monotonic()
        self._update_actions()
        self.run_state_changed.emit("running")
        self.show_status("Capturing, waiting for the trigger...")

    def capture_aborted(self) -> None:
        """The source stopped a capture that delivers nothing (a buffer capture): not capturing
        any more; what the view shows stays."""
        if self._running_capture is None:
            return
        self._running_capture = None
        self._live_session = self._live_source = None
        self._action_marks = []
        self._update_actions()
        self.run_state_changed.emit("stopped")
        self.notify("Capture aborted", 5000)
        self.document_changed.emit()

    def abort_capture(self) -> None:
        if self.recording is not None:
            self.stop_recording()
            return
        if self.source is not None:
            self.source.abort()
        self._update_actions()

    def _queue_capture_progress(self, args: CaptureProgressArgs) -> None:
        if self._pending_progress is None:
            QTimer.singleShot(0, self._show_pending_progress)
        self._pending_progress = args

    def _show_pending_progress(self) -> None:
        args, self._pending_progress = self._pending_progress, None
        if args is not None:
            self._on_capture_progress(args)

    def _on_capture_progress(self, args: CaptureProgressArgs) -> None:
        """Shows a streaming capture while it runs, following its end unless scrolled back."""
        if args.session is not self._running_capture or args.sample_count == 0:
            return  # queued behind the completion
        model = self.model
        live = self._live_session
        if live is None or self._live_source is not args.session:
            live = args.session.clone_settings()
            for channel in live.capture_channels:
                channel.samples = args.samples.get(channel.channel_number)
            self._apply_live_extras(live, args)
            self._live_session, self._live_source = live, args.session
            self._live_window = max(live.frequency // LIVE_WINDOW_DIVISOR, 1_000)
            self._live_first = args.first_sample
            model.set_session(live, live=True, first_sample=args.first_sample)
            follow = True
        else:
            if model.visible_samples != self._live_visible:
                self._live_window = model.visible_samples  # zoomed while streaming
            follow = model.first_sample + model.visible_samples >= model.sample_count - 1
            for channel in live.capture_channels:
                channel.samples = args.samples.get(channel.channel_number, channel.samples)
            self._apply_live_extras(live, args)
            model.extend_live(args.first_sample)
            if live.analog_channels:
                model.notify_analog_changed()
            if not self.follow_button.isChecked():
                follow = False
        dropped = args.first_sample - self._live_first
        self._live_first = args.first_sample
        self._live_arrival = time.monotonic()

        count = model.sample_count
        if follow:
            model.set_view(count - self._live_window, self._live_window)
        elif dropped:
            # Scrolled back: keep the samples on screen until they drop out of the window.
            model.set_view(model.first_sample - dropped, model.visible_samples)
        self._live_visible = model.visible_samples
        rate = max(live.frequency, 1)
        lost = f", {to_thousands(args.lost)} samples lost" if args.lost else ""
        if args.session.continuous:
            streamed = args.first_sample + count
            text = (f"Streaming until stopped: {long_duration(streamed / rate)}, "
                    f"keeping the last {to_thousands(count)} samples ({to_small_time(count / rate)})")
        else:
            text = (f"Streaming: {to_thousands(count)} of {to_thousands(args.session.total_samples)} "
                    f"samples ({to_small_time(count / rate)})")
        if live.state_times is not None:
            text = f"Live state: {to_thousands(count)} states"
        self.show_status(text + lost)
        self.view_toolbar.set_available("roll", True)

    # ------------------------------------------------------- action markers
    @property
    def is_capturing(self) -> bool:
        return self._running_capture is not None or self.recording is not None

    def mark_action(self, text: str, device_time: Optional[float] = None) -> bool:
        """A marker for something set on a device while this document captures.

        In a live display it appears at once at the newest sample; otherwise it is placed when the
        capture completes, at the time since the start (marked with ``≈``: the start of a buffered
        capture is only known to the computer's clock). ``device_time``: the time of the action on
        the recorded instrument (exact for a monitor recording).
        """
        if self.recording is not None:
            sample = self.recording.sample_at(device_time) if device_time is not None else self.recording.count - 1
            self._recording_marks.append((max(sample, 0), text))
            if self._recording_session is not None:
                self.model.add_bookmark(max(sample, 0), text)
            return True
        if self._running_capture is None:
            return False
        stream_sample = shown = None
        if self._live_session is not None and self.model.is_live:
            # the newest block arrived a moment ago: the stream went on since
            count = self.model.sample_count
            ahead = int((time.monotonic() - self._live_arrival) * self._live_session.frequency)
            stream_sample = self._live_first + max(count - 1, 0) + max(ahead, 0)
            shown = self.model.add_bookmark(max(count - 1, 0), text)
        self._action_marks.append((stream_sample, time.monotonic(), text, shown))
        return True

    def _place_action_marks(self, session: CaptureSession, first_sample: int) -> None:
        marks, self._action_marks = self._action_marks, []
        count = self.model.sample_count
        if not marks or not count:
            return
        for stream_sample, stamp, text, shown in marks:
            if stream_sample is not None:
                sample, name = stream_sample - first_sample, text
            else:
                sample = int((stamp - self._capture_started) * session.frequency)
                name = f"≈ {text}"
            sample = min(max(sample, 0), count - 1)
            if shown is not None:
                self.model.remove_bookmark(shown)
            self.model.add_bookmark(sample, name)

    # ------------------------------------------------------ monitor record
    def record_monitor(self, instrument: Instrument, rate: float, pins: list[str],
                       analog: tuple[str, ...] = ()) -> bool:
        """Record the monitor of ``instrument`` as a slow capture into this document."""
        from ...core.monitor_recording import MonitorRecording

        monitor = instrument.monitor
        if monitor is None or self.is_capturing:
            return False
        self.recording = MonitorRecording(rate, pins, analog)
        self._recording_session = None
        self._recording_marks: list[tuple[int, str]] = []
        self._recording_monitor = monitor
        self._recording_stop = monitor.on_state(self.recording.add)
        try:
            monitor.start(rate, pins, analog)
        except Exception as error:  # noqa: BLE001 - shown to the user
            self._recording_stop()
            self.recording = self._recording_stop = None
            messages.error(self, "Monitor", "The monitor could not be started.", str(error))
            return False
        self._recording_timer.start()
        self.show_status(f"Recording the monitor of {instrument.name}...")
        self._update_actions()
        return True

    def _show_recording(self) -> None:
        recording = self.recording
        if recording is None or recording.count == 0:
            return
        model = self.model
        first = self._recording_session is None
        session = recording.session(self._recording_session)
        if first:
            self._recording_session = session
            model.set_session(session, live=True)
            for sample, name in self._recording_marks:
                model.add_bookmark(sample, name)
        else:
            model.extend_live(0)
            if session.analog_channels:
                model.notify_analog_changed()
        window = max(session.frequency * 10, 50)
        model.set_view(model.sample_count - window, window)
        self.notify(
            f"Recording the monitor: {to_thousands(recording.count)} samples "
            f"({to_small_time(recording.count / session.frequency)})")

    def stop_recording(self) -> Optional[CaptureSession]:
        """End a monitor recording; the result stays in the document."""
        if self.recording is None:
            return None
        self._recording_timer.stop()
        self._recording_stop()
        try:
            self._recording_monitor.stop()
        except Exception:  # noqa: BLE001 - the device may be gone
            log.debug("self._recording_monitor.stop() failed: the device may be gone", exc_info=True)
        self._show_recording()
        recording, self.recording, self._recording_stop = self.recording, None, None
        session = recording.session(self._recording_session)
        bookmarks = list(self._recording_marks)
        if self._recording_session is None or not self.model.finish_live(session):
            self.load_session(session)
            for sample, name in bookmarks:
                self.model.add_bookmark(sample, name)
        self._recording_session = None
        self._dirty = True
        self._update_title()
        self._update_actions()
        self.notify(f"Recorded {to_thousands(recording.count)} monitor samples", 5000)
        return session

    def _apply_live_extras(self, live: CaptureSession, args: CaptureProgressArgs) -> None:
        """Analog channels and state times of a stream while it runs."""
        for channel in live.analog_channels:
            raw = args.analog.get(channel.channel_number)
            if raw is not None:
                channel.raw = raw
                channel.length = None
        if args.state_times is not None:
            live.state_times = args.state_times
        if args.analog and not live.capture_channels:
            live.post_trigger_samples = live.sample_count()

    def _on_capture_completed(self, args: CaptureCompletedArgs) -> None:
        self.status_label.setText("")  # (what went on is over; a transfer that goes on says so again)
        self._update_actions()
        was_live = self._live_session is not None
        live_first = self._live_first
        self._live_session = self._live_source = self._running_capture = None
        self.run_state_changed.emit("finished" if args.success else "error")

        if not args.success:
            if was_live:
                self.model.set_session(None)
            self._pending_simulation = None
            self.show_capture_problem("The capture failed: no samples were received from the device.",
                                      (f"{args.error} " if args.error else "")
                                      + "Try again; if it fails again, reconnect the device.")
            self.notify("Capture failed")
            return
        self.show_capture_problem("")

        self._loading += 1
        try:
            self.model.clear_regions()
        finally:
            self._loading -= 1
        self.current_file = None
        self._dirty = True
        self._update_title()
        aligned = ""
        if self.driver is not None and self.driver.board_count > 1:
            # Before loading, so the display and the decoders see the corrected samples.
            per_device = self.driver.channels_per_device
            self._remember_unaligned(args.session)
            options = alignment.alignment_options(args.session, per_device)
            if options:
                report = self._apply_alignment_choices(
                    args.session, per_device, {device: candidates[0] for device, candidates in options.items()}
                )
                if any(len(candidates) > 1 for candidates in options.values()):
                    report += " (Data > Align boards to choose another method)"
                aligned = "; " + report
        # Before loading: the capture change decodes with the new decoders once.
        self._apply_simulation_decoders(args.session)
        # After a live display the view stays where the user watched the samples arrive, and the
        # edge index of the live display is kept (a capture on disk is not read again).
        dropped = args.first_sample - live_first
        if was_live and self.model.finish_live(args.session, args.first_sample):
            if dropped:
                self.model.set_view(self.model.first_sample - dropped, self.model.visible_samples)
            self._sync_view_controls()
            self._update_info_panel()
            self._update_actions()
        else:
            self.load_session(args.session, reset_view=not was_live)
        if self.model.on_disk and self.provider.instances:
            self.decoder_manager.status_label.setText("Recorded to disk: press Decode now to decode it")
        self.notify(
            f"Captured {to_thousands(args.session.total_samples)} samples "
            f"at {to_large_frequency(args.session.frequency)}{aligned}"
        )
        self._place_action_marks(args.session, args.first_sample)
        if args.error:
            # Samples arrived, but the capture ended early (a stream the link could not keep up with)
            self.show_capture_problem(
                f"The capture ended early after {to_thousands(args.session.total_samples)} samples.", args.error,
                "warning")

    def _remember_unaligned(self, session: CaptureSession) -> None:
        """Keep the samples as captured, so another method can be chosen later."""
        if self._unaligned is not None and self._unaligned[0] is session:
            originals = self._unaligned[1]
            if all(
                id(channel) in originals and channel.samples is not None
                and originals[id(channel)].size == channel.samples.size
                for channel in session.capture_channels
            ):
                return
        self._unaligned = (
            session,
            {id(channel): channel.samples for channel in session.capture_channels if channel.samples is not None},
        )

    def _apply_alignment_choices(self, session: CaptureSession, per_device: int, choices: dict) -> str:
        lines = []
        for device, choice in sorted(choices.items()):
            if choice is None:
                lines.append(f"board {device + 1} left unchanged")
                continue
            if choice.changed:
                alignment.apply_alignment(session, per_device, choice)
            lines.append(choice.describe())
        report = "; ".join(lines)
        self._alignment_report = (session, report)
        return report

    def align_boards(self) -> None:
        """Align the boards of the loaded multi device capture, choosing the method if there are several."""
        session = self.model.session
        if session is None:
            return
        if self.driver is not None and self.driver.board_count > 1:
            per_device: Optional[int] = self.driver.channels_per_device
        else:
            per_device = alignment.channels_per_device_of(session)

        options = {}
        current = {id(channel): channel.samples for channel in session.capture_channels}
        if per_device:
            # Measure on the samples as captured, not on an earlier correction.
            self._remember_unaligned(session)
            originals = self._unaligned[1]
            for channel in session.capture_channels:
                if id(channel) in originals:
                    channel.samples = originals[id(channel)]
            options = alignment.alignment_options(session, per_device)

        def keep_current() -> None:
            for channel in session.capture_channels:
                channel.samples = current.get(id(channel), channel.samples)

        if not options:
            keep_current()
            messages.info(
                self,
                "Align boards",
                "The boards could not be aligned.",
                "Aligning needs a capture from a multi device set and either a reference line (a "
                "channel on the first board named like a channel of another board plus ' ref', e.g. "
                "'A0 (Y) ref') or a clock channel on the first board named Φ2, PHI2, CLK or clock.",
            )
            return

        if any(len(candidates) > 1 for candidates in options.values()):
            dialog = AlignDialog(options, self)
            if not dialog.exec():
                keep_current()
                return
            choices = dialog.choices
        else:
            choices = {device: candidates[0] for device, candidates in options.items()}

        report = self._apply_alignment_choices(session, per_device, choices)
        self._after_samples_modified(self.model.first_sample)
        self.notify(report, 10000)
        messages.info(self, "Align boards", "The boards were aligned.", report.replace("; ", "\n"))

    def load_session(self, session: CaptureSession, reset_view: bool = True) -> None:
        # Another capture: a state analysis shown before is no longer the way back
        self._timing_capture = None
        self._state_session = None
        if self.state_time_button.isChecked():
            self.state_time_button.blockSignals(True)
            self.state_time_button.setChecked(False)
            self.state_time_button.blockSignals(False)
        self._loading += 1
        try:
            self.model.set_session(session)
        finally:
            self._loading -= 1
        self._edited = False
        if reset_view:
            total = self.model.sample_count
            visible = max(min(total, 200), 4)
            first = max(session.pre_trigger_samples - visible // 4, 0)
            self.model.set_view(first, visible)
        self._sync_view_controls()
        self._update_info_panel()
        self._update_actions()
