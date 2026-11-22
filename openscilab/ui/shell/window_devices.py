# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The devices of the window: connecting, the device's document and its data, firmware, recording,
disconnecting; the serial ports watched for boards that come and go.

A part of :class:`~.main_window.ShellWindow` (ARCHITECTURE.md, *Known hotspots*): the window
combines its parts, which share its state through ``self``. Signals stay declared on the window.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Callable, Optional

from PySide6.QtCore import QTimer

from ...core.hub import EVENT_REMOVED
from ...core.instrument import Instrument, InstrumentStatus
from ...driver.base import BoardMismatchError, DeviceConnectionError, FirmwareOutdatedError
from .. import background, devices, messages
from ..documents.dataview import DataView
from ..documents.device import DeviceDocument
from ..documents.flow import FlowDocument
from .window_common import drives_outputs

log = logging.getLogger(__name__)


def serial_ports() -> frozenset:
    """The serial ports of the computer (cheap enough to ask every few seconds)."""
    try:
        from ...driver.ports import comports

        return frozenset(port.device for port in comports())
    except Exception:  # noqa: BLE001 - no pyserial, no permission: nothing to watch
        log.debug("serial_ports: no pyserial, no permission: nothing to watch", exc_info=True)
        return frozenset()


def ports_of(instrument: Instrument) -> list[str]:
    """The serial ports named in an instrument's address, whatever its kind (``pico:/dev/a``,
    ``pico:/dev/a,/dev/b``, ``arduino:COM5``); none over the network, none of a simulator."""
    if ":" not in instrument.uri:
        return []
    rest = instrument.uri.split(":", 1)[1].split("?", 1)[0]
    return [port for port in rest.split(",") if port.startswith("/dev/") or re.fullmatch(r"COM\d+", port)]


class ShellDevicesMixin:
    """The devices of the window: connecting, the device's document and its data, firmware, recording,"""

    def poll_ports(self) -> None:
        """Read the serial ports in a thread (listing them takes up to a fifth of a second on
        some systems: every few seconds that was a stutter of the window); :meth:`watch_ports`
        gets them."""
        if self._ports_thread is not None and self._ports_thread.is_alive():
            return

        def read() -> None:
            ports = serial_ports()
            try:
                self._ports_read.emit(ports)
            except (RuntimeError, TypeError):  # the window was closed meanwhile (PySide 6.11: TypeError)
                log.debug("The serial ports were read after the window closed", exc_info=True)

        self._ports_thread = threading.Thread(target=read, name="openscilab-ports", daemon=True)
        self._ports_thread.start()

    def watch_ports(self, ports: Optional[frozenset] = None) -> None:
        """Mark open devices whose port went away as disconnected; list new boards."""
        ports = serial_ports() if ports is None else ports
        if ports == self._ports:
            return
        first = self._ports is None
        self._ports = ports
        for instrument in self.hub.instruments():
            used = ports_of(instrument)
            if used and any(port not in ports for port in used) and instrument.status != InstrumentStatus.DISCONNECTED:
                self.hub.set_status(instrument.name, InstrumentStatus.DISCONNECTED, "unplugged")
                self.statusBar().showMessage(f"{instrument.name} was unplugged", 8000)
        if not first and self.sidebar_dock.isVisible() and self.sidebar.current_key == "devices":
            self.devices_section.refresh()

    # -------------------------------------------------------------- devices
    def _fill_device_menus(self) -> None:
        instruments = self.hub.instruments()
        for menu, slot, needs_capture in ((self.card_menu, self.open_device_card, False),
                                          (self.data_menu_devices, self.show_data, True),
                                          (self.disconnect_menu, self.disconnect_instrument, False)):
            menu.clear()
            usable = [instrument for instrument in instruments if not needs_capture or instrument.capture is not None]
            for instrument in usable:
                menu.addAction(instrument.name, lambda instrument=instrument, slot=slot: slot(instrument))
            menu.setEnabled(bool(usable))
        self.action_disconnect_all.setEnabled(bool(instruments))

    def connect_dialog(self) -> Optional[Instrument]:
        """*Devices → Connect…*: choose any device (detected, by hand, simulator) and connect it."""
        from ..dialogs.connect_dialog import ConnectDialog

        dialog = ConnectDialog(self, frozenset(instrument.uri for instrument in self.hub.instruments()))
        if not dialog.exec() or dialog.entry is None:
            return None
        return self.connect_entry(dialog.entry)

    def try_simulator(self, profile: str = "free") -> Optional[Instrument]:
        """Connect the simulator ``profile`` (start page: *Try a simulator*)."""
        from ..devices import DeviceEntry
        from ..devices.simulated import BACKEND_ID

        backend = next((backend for backend in devices.backends() if backend.id == BACKEND_ID), None)
        entry = next((entry for entry in (backend.manual_entries() if backend else []) if entry.value == profile),
                     None)
        if entry is None:
            entry = DeviceEntry(BACKEND_ID, "profile", profile, profile)
        existing = next((instrument for instrument in self.hub.instruments()
                         if instrument.uri == devices.entry_uri(entry)), None)
        if existing is not None:
            self.open_device_card(existing)
            return existing
        return self.connect_entry(entry)

    def connect_entry(self, entry) -> Optional[Instrument]:
        """Open the device of a device list entry in the hub and show its device card."""
        backend = next((backend for backend in devices.backends() if backend.id == entry.backend), None)
        if backend is None:
            return None
        try:
            instrument = backend.open_instrument(entry, self)
        except BoardMismatchError as error:
            return self.resolve_board_mismatch(error, lambda: self.connect_entry(entry))
        except FirmwareOutdatedError as error:
            where = f" on {error.location}" if error.location else ""
            if messages.confirm(self, "Firmware update needed",
                                f"The device{where} needs the firmware of this version of openSciLab.",
                                "Update firmware...",
                                f"{error} The update takes a few seconds; the capture settings and profiles are kept."):
                self.show_hardware()  # lists the board with the update that fits it
            return None
        except background.Cancelled:
            self.statusBar().showMessage("Connecting was cancelled", 5000)
            return None
        except (DeviceConnectionError, OSError, ValueError) as error:
            self.activity_bar.select("devices")
            self.devices_section.show_error(f"{entry.label or entry.value} could not be connected: {error}")
            self.statusBar().showMessage("The device could not be connected", 8000)
            return None
        if instrument is None:
            return None
        self.devices_section.show_error("")
        self.hub.add(instrument)
        self.open_device_card(instrument)
        return instrument

    def resolve_board_mismatch(self, error: BoardMismatchError, retry: Callable[[], Optional[Instrument]]
                               ) -> Optional[Instrument]:
        """A Pico on a board its firmware does not fit (``core/board_check.py``): the warning offers its
        firmware; using it anyway takes the confirmed risk, then ``retry`` opens it again."""
        from ...core import board_check
        from ..dialogs import board_warning_dialog as warning

        finding = error.finding
        if not isinstance(finding, board_check.BoardFinding):
            messages.error(self, "Wrong firmware for this board", str(error))
            return None
        choice = warning.ask(self, finding, warning.OPENING)
        if choice == warning.ACCEPT:
            board_check.accept(finding)
            return retry()
        if choice == warning.INSTALL:
            document = self.show_hardware()
            if document is not None:
                document.install_fitting(error.location, finding)
            return None
        self.statusBar().showMessage("The board was not opened: its firmware does not fit it", 8000)
        return None

    def device_card(self, instrument: Instrument) -> Optional[DeviceDocument]:
        for document in self.area.documents():
            if isinstance(document, DeviceDocument) and document.instrument is instrument:
                return document
        return None

    def open_device_card(self, instrument: Instrument) -> Optional[DeviceDocument]:
        if instrument is None:
            return None
        document = self.device_card(instrument)
        if document is not None:
            self.area.activate(document)
            return document
        document = DeviceDocument(instrument, self.hub, controller=self.capture_controller(instrument))
        document.data_requested.connect(self.show_data)
        document.setup_requested.connect(lambda instrument: self.show_data(instrument, setup=True))
        document.flow_requested.connect(self.add_instrument_to_flow)
        document.disconnect_requested.connect(self.disconnect_instrument)
        document.reconnect_requested.connect(self.reconnect_instrument)
        document.action_performed.connect(self.mark_action)
        document.record_requested.connect(self.record_monitor)
        document.stimulus_requested.connect(self.stimulus_and_capture)
        document.firmware_requested.connect(self.update_device_firmware)
        document.example_requested.connect(self.open_example)
        document.settings_requested.connect(self.show_preferences)
        return self.add_document(document)

    def show_hardware(self):
        """The connected hardware with its firmware (one document)."""
        from ..documents.hardware import HardwareDocument

        for document in self.area.documents():
            if isinstance(document, HardwareDocument):
                self.area.activate(document)
                document.refresh()
                return document
        document = HardwareDocument(self.hub)
        document.open_requested.connect(self.connect_entry)
        document.card_requested.connect(self.open_device_card)
        document.firmware_requested.connect(self.update_firmware)
        return self.add_document(document)

    def update_device_firmware(self, instrument: Instrument):
        """*Update firmware* of the device card: the fitting image for UF2 boards (connected hardware),
        the update tool of the device otherwise."""
        from ...core import firmware

        driver = instrument.capture.driver if instrument.capture is not None else None
        method = firmware.update_method(driver) if driver is not None else None
        if method is not None and method.key == "uf2":
            return self.show_hardware().install_for(instrument)
        return self.update_firmware(instrument)

    def forget_known_devices(self) -> bool:
        """Drop the stored multi device sets so autodetect asks again."""
        from ..devices import pico as pico_devices

        known = pico_devices.known_device_sets()
        if not known:
            messages.info(self, "Multi device sets", "No multi device set has been registered yet.")
            return False
        if not messages.confirm(self, "Forget multi device sets",
                                f"Forget {len(known)} registered multi device set(s)?", "Forget",
                                "Autodetect asks again the next time several analyzers are connected.",
                                destructive=True):
            return False
        pico_devices.forget_known_device_sets()
        self.statusBar().showMessage("Registered multi device sets removed", 5000)
        return True

    def update_firmware(self, instrument: Optional[Instrument] = None) -> Optional[str]:
        """*Update firmware* of ``instrument`` (or of a board that is not open)."""
        from ..dialogs.firmware_dialog import update_firmware

        if instrument is not None:
            driver = instrument.capture.driver if instrument.capture is not None else None
            if driver is not None and driver.is_capturing:
                messages.info(self, "Update firmware", f"{instrument.name} is capturing; stop it first.")
                return None

        def release() -> None:
            if instrument is not None:
                devices.remove_instrument(self, self.hub, instrument)

        port = update_firmware(self, instrument, release)
        self.devices_section.refresh()
        return port

    def mark_action(self, instrument: Instrument, text: str) -> int:
        """Put a marker for an action on ``instrument`` into every capture running now."""
        stamp = None
        model = getattr(instrument, "simulated_driver", None)
        if model is not None:
            stamp = model.clock()
        marked = 0
        for document in self.area.documents():
            if isinstance(document, DataView) and document.mark_action(f"{instrument.name}: {text}", stamp):
                marked += 1
        if marked:
            self.statusBar().showMessage(f"Marker: {instrument.name}: {text}", 3000)
        return marked

    def stimulus_and_capture(self, capture: Instrument, source: Instrument, pin: str, width: float) -> bool:
        """Arm a capture with ``capture`` (any instrument of the hub), then pulse ``pin`` of ``source``."""
        controller = self.capture_controller(capture)
        card = self.device_card(source)

        def pulse() -> None:
            if card is not None:
                card.pulse(pin, width)  # confirmed when it was requested; reported as a marker
            else:
                source.gpio.pulse(pin, width)

        if controller is None or not controller.arm_and_then(pulse):
            messages.info(self, "Stimulus and capture",
                          f"{capture.name} cannot start a capture now.",
                          "Capture once with it (or set the toolbar's settings) first.")
            return False
        return True

    def record_stream(self, instrument: Instrument) -> bool:
        """A stream of ``instrument`` to the disk until it is stopped, in the device's data view."""
        controller = self.capture_controller(instrument)
        if controller is None:
            return False
        self.show_data(instrument, beside=True)
        return controller.record(self)

    def record_monitor(self, instrument: Instrument) -> Optional[DataView]:
        """Record the monitor of ``instrument`` into a new data view."""
        card = self.device_card(instrument)
        rate = card.monitor_rate.value() if card is not None else 20
        pins = [pin for pin in instrument.pins() if pin.usable]
        digital = [pin.name for pin in pins if "DIN" in pin.capabilities]
        analog = tuple(pin.name for pin in pins if "ADC" in pin.capabilities)
        document = self.new_data_view()
        if not document.record_monitor(instrument, rate, digital, analog):
            return None
        if card is not None:
            card.sync_monitor()
        return document

    def show_data(self, instrument: Instrument, beside: bool = False, setup: bool = False) -> DataView:
        """The data view of ``instrument``'s captures (a new one when no view shows them).
        ``beside``: a capture started on the device card, whose data view opens next to the card
        (both stay visible) and the card keeps the focus. ``setup``: a view without a capture opens
        the capture setup (instead of showing that there is no data)."""
        document = self._show_data(instrument, beside)
        controller = self.capture_controller(instrument)
        if setup and controller is not None and document.source is controller and \
                document.model.session is None and not controller.is_capturing:
            # (after the view is shown: the setup is a dialog over it)
            QTimer.singleShot(0, document, lambda: document.model.session is None and not controller.is_capturing
                              and document.source is controller and document.capture_controls.capture_settings())
        return document

    def _show_data(self, instrument: Instrument, beside: bool) -> DataView:
        controller = self.capture_controller(instrument)
        view = controller.view if controller is not None else None
        if view is not None and view in self.area.documents():
            if beside:
                self.area.reveal(view)
            else:
                self.area.activate(view)
            return view
        active = self.active_document()
        empty = [document for document in reversed(self.area.documents())
                 if isinstance(document, DataView) and document.source is None and document.model.session is None]
        if active in empty:
            document = active
        elif empty:
            document = empty[0]  # e.g. the data view of the Logic Analyzer template
        else:
            document = self.new_data_view()
        document.use_instrument(instrument)
        card = self.device_card(instrument)
        if beside and card is not None and not self.area.is_detached(card) and \
                self.area.group_of(card) is self.area.group_of(document):
            self.area.split(document)
            self.area.activate(card)
            self.area.reveal(document)
        elif beside:
            self.area.reveal(document)
        else:
            self.area.activate(document)
        return document

    def capture_controller(self, instrument: Instrument):
        """The capture controller of ``instrument``; its captures open a data view when none shows them."""
        from ..devices.capture import capture_controller

        controller = capture_controller(instrument)
        if controller is not None and controller.view_provider is None:
            controller.changed.connect(self._update_run_buttons)
            controller.connection_lost.connect(
                lambda reason, instrument=instrument: self.connection_lost(instrument, reason))
            controller.view_provider = lambda controller: self.show_data(controller.instrument, beside=True)
            controller.reveal_view = self.area.reveal
        return controller

    def disconnect_instrument(self, instrument: Instrument, ask: bool = True) -> bool:
        """Close ``instrument``; asks first while it captures, drives outputs or a flow uses it."""
        if instrument not in self.hub:
            return False
        if ask:
            reasons = self.disconnect_reasons(instrument)
            if reasons and not messages.confirm(self, "Disconnect", f"Disconnect {instrument.name}?", "Disconnect",
                                                "While " + " and ".join(reasons) + ".", destructive=True):
                return False
        devices.remove_instrument(self, self.hub, instrument)
        return True

    def disconnect_reasons(self, instrument: Instrument) -> list[str]:
        """Why disconnecting ``instrument`` now interrupts something."""
        reasons = []
        driver = instrument.capture.driver if instrument.capture is not None else None
        if driver is not None and driver.is_capturing:
            reasons.append("a capture is running")
        if drives_outputs(instrument):
            reasons.append("it drives outputs")
        running = [document.title for document in self.area.documents() if isinstance(document, FlowDocument)
                   and document.runner.running and instrument.uri in document.flow.devices.values()]
        if running:
            reasons.append("the flow " + ", ".join(running) + " uses it")
        return reasons

    def disconnect_all(self, ask: bool = True) -> bool:
        """Disconnect every device, after one question that names them and what that interrupts."""
        instruments = self.hub.instruments()
        if not instruments:
            return False
        if ask:
            reasons = {instrument.name: self.disconnect_reasons(instrument) for instrument in instruments}
            lines = [name + (": " + " and ".join(why) if why else "") for name, why in reasons.items()]
            count = f"all {len(instruments)} devices" if len(instruments) > 1 else instruments[0].name
            note = "Running captures and recordings stop, outputs are released." if any(reasons.values()) \
                else "Nothing is running on them now."
            if not messages.confirm(self, "Disconnect all devices", f"Disconnect {count}?", "Disconnect all",
                                    "\n".join(lines) + "\n\n" + note, destructive=True):
                return False
        for instrument in instruments:
            devices.remove_instrument(self, self.hub, instrument)
        self.statusBar().showMessage(f"{len(instruments)} device{'s' if len(instruments) > 1 else ''} disconnected",
                                     6000)
        return True

    def connection_lost(self, instrument: Instrument, reason: str) -> None:
        """A device stopped answering: its status says so (the card offers *Reconnect*)."""
        if instrument in self.hub and instrument.status not in (InstrumentStatus.DISCONNECTED,
                                                                 InstrumentStatus.ERROR):
            self.hub.set_status(self.hub.name_of(instrument), InstrumentStatus.ERROR, reason)
            self.statusBar().showMessage(f"{instrument.name} does not answer: {reason}", 8000)

    def reconnect_instrument(self, instrument: Instrument) -> Optional[Instrument]:
        """Open the device of ``instrument`` again (after it was disconnected or unplugged); its
        device card takes the place of the old one."""
        from ...lab.engine.devices import open_instrument

        old_card = self.device_card(instrument)
        devices.remove_instrument(self, self.hub, instrument)
        try:
            if instrument.uri.startswith("sim:"):
                from ..devices.simulated import connect_simulator

                new = connect_simulator(instrument.uri, self.hub, self)
            else:
                new = background.run(self, f"Connecting to {instrument.uri}...",
                                     lambda: open_instrument(instrument.uri))
        except background.Cancelled:
            return None
        except BoardMismatchError as error:
            if old_card is not None:
                self.area.close_document(old_card, force=True)
            return self.resolve_board_mismatch(error, lambda: self.reconnect_instrument(instrument))
        except Exception as error:  # noqa: BLE001 - every way a device can fail to open
            if old_card is not None:
                old_card.show_banner(f"{instrument.name} could not be opened: {error}", "error")
            else:
                messages.error(self, "Reconnect", f"{instrument.name} could not be opened.", str(error))
            return None
        new.name = instrument.name if instrument.name not in self.hub.names() else new.name
        self.hub.add(new)
        card = self.open_device_card(new)
        if old_card is not None and card is not None:
            group = self.area.group_of(old_card)
            if group is not None and self.area.group_of(card) is group:
                group.tabBar().moveTab(group.indexOf(card), group.indexOf(old_card))
            self.area.close_document(old_card, force=True)
            self.area.activate(card)
        return new

    def _on_hub_event(self, _event) -> None:
        removed = getattr(_event, "instrument", None) if getattr(_event, "kind", "") == EVENT_REMOVED else None
        controller = getattr(removed, "capture_controller", None)
        if controller is not None:
            controller.close()  # no handlers, timers or settings of a device that is gone
        self.devices_section.refresh_open()
        self._refresh_device_presets()
        count = len(self.hub)
        self.devices_label.setText("No devices" if not count else f"{count} device{'s' if count > 1 else ''}")
