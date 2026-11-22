# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The Devices section of the sidebar: the open devices with their status, the devices to connect,
the simulators."""

from __future__ import annotations

import html
import logging
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core import preferences
from ...core.hub import Hub
from ...core.instrument import Instrument, InstrumentStatus
from .. import devices
from ..icons import icon, set_icon
from ..theme import set_role
from .window_common import (
    SIMULATOR_GROUP,
    simulator_label,
)

log = logging.getLogger(__name__)


class DevicesSection(QWidget):
    """The instruments open in the hub - a row each with its state, connection, the settings of its
    next capture and how much of the device they use (``device_rows``) - and the devices that can
    be opened."""

    #: how often the rows of the connected devices show what they do now, while the list is shown (ms)
    STATUS_INTERVAL_MS = 1000

    #: an open instrument was chosen (its document)
    instrument_activated = Signal(object)
    #: a device list entry was chosen (connect it)
    entry_activated = Signal(object)
    data_requested = Signal(object)
    disconnect_requested = Signal(object)
    #: disconnect every connected device (asked first)
    disconnect_all_requested = Signal()
    #: a device node for an open instrument in the active flow
    flow_node_requested = Signal(object)
    #: install the firmware on a board that cannot be used yet
    firmware_requested = Signal()
    #: the connected hardware with its firmware
    hardware_requested = Signal()

    def __init__(self, hub: Hub, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.hub = hub
        from .sections import Part, PartStack

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 4)
        layout.setSpacing(0)
        self.parts = PartStack(self)
        layout.addWidget(self.parts, 1)
        self.open_list = QListWidget(self)
        self.open_list.setFrameShape(QFrame.NoFrame)
        self.open_list.itemActivated.connect(lambda item: self.instrument_activated.emit(item.data(Qt.UserRole)))
        self.open_list.itemClicked.connect(lambda item: self.instrument_activated.emit(item.data(Qt.UserRole)))
        self.open_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.open_list.customContextMenuRequested.connect(self._open_menu)
        self.connected_part = self.parts.add(Part("connected", "Connected", self.open_list), 1)
        from ...core.device_summary import LimitCache

        #: the rows of the connected devices (``device_rows.DeviceRow``), in the order of the list
        self.rows: list = []
        self._limits = LimitCache()
        self.status_timer = QTimer(self)
        self.status_timer.setInterval(self.STATUS_INTERVAL_MS)
        self.status_timer.timeout.connect(self.update_rows)
        self.disconnect_all_button = self.connected_part.add_button(
            "unplug", "Disconnect all devices...", self.disconnect_all_requested.emit)
        self.list = QListWidget(self)
        self.list.setFrameShape(QFrame.NoFrame)
        self.list.setToolTip("Click a device to connect it")
        # one click connects a device (as everywhere in the sidebar, a click opens), and opens or
        # closes the group of the simulators
        self.list.itemActivated.connect(self._entry_activated)
        self.list.itemClicked.connect(self._entry_clicked)
        self.available_part = self.parts.add(Part("available", "Available", self.list), 2)
        self.available_part.add_button("refresh", "Look for devices again", lambda: self.refresh())
        # boards that cannot be used yet (e.g. without the firmware of this application)
        self.notice = QLabel(self)
        self.notice.setWordWrap(True)
        self.notice.setContentsMargins(10, 4, 10, 4)
        set_role(self.notice, "warning")
        self.notice.setVisible(False)
        layout.addWidget(self.notice)
        from ..dialogs.common import Banner

        # why the last device could not be connected (instead of a dialog)
        self.error_banner = Banner("error", self)
        self.error_banner.setVisible(False)
        self.error_banner.button.clicked.connect(lambda: self.error_banner.setVisible(False))
        layout.addWidget(self.error_banner)
        self.firmware_button = QPushButton("Install firmware...", self)
        set_icon(self.firmware_button, "chip")
        self.firmware_button.clicked.connect(self.firmware_requested.emit)
        self.firmware_button.setVisible(False)
        layout.addWidget(self.firmware_button)
        buttons = QHBoxLayout()
        buttons.setContentsMargins(6, 4, 6, 0)
        self.hardware_button = QPushButton("Boards and firmware", self)
        set_icon(self.hardware_button, "chip")
        self.hardware_button.setToolTip("Every board on this computer with its firmware: install or update it")
        self.hardware_button.clicked.connect(self.hardware_requested.emit)
        buttons.addWidget(self.hardware_button, 1)
        layout.addLayout(buttons)
        self.refresh_open()

    def _entry_clicked(self, item: QListWidgetItem) -> None:
        if item.data(Qt.UserRole) == SIMULATOR_GROUP:
            self.toggle_simulators()
        else:
            self._entry_activated(item)

    def show_error(self, text: str) -> None:
        self.error_banner.set_message(html.escape(text), "Dismiss" if text else None)

    def _entry_activated(self, item: QListWidgetItem) -> None:
        entry = item.data(Qt.UserRole)
        if entry == SIMULATOR_GROUP:
            return  # (a click toggles it)
        if entry is not None:
            self.entry_activated.emit(entry)

    def toggle_simulators(self) -> None:
        """Open or close the group of the simulators."""
        preferences.update({"devices.simulators_open": not preferences.get("devices.simulators_open")})
        self.refresh()

    def _open_menu(self, position) -> None:
        item = self.open_list.itemAt(position)
        if item is None:
            return
        instrument = item.data(Qt.UserRole)
        menu = QMenu(self)
        menu.addAction(icon("devices"), "Open", lambda: self.instrument_activated.emit(instrument))
        menu.addAction(icon("nodes"), "Use in a flow", lambda: self.flow_node_requested.emit(instrument))
        if instrument.capture is not None:
            menu.addAction(icon("channels"), "Data view", lambda: self.data_requested.emit(instrument))
        menu.addSeparator()
        menu.addAction(icon("unplug"), "Disconnect", lambda: self.disconnect_requested.emit(instrument))
        if len(self.hub) > 1:
            menu.addAction(icon("unplug"), "Disconnect all devices...", self.disconnect_all_requested.emit)
        menu.exec(self.open_list.mapToGlobal(position))

    def refresh_open(self) -> None:
        from .device_rows import DeviceRow

        self.open_list.clear()
        self.rows = []
        for instrument in self.hub.instruments():
            item = QListWidgetItem(self.open_list)
            item.setToolTip(f"{instrument.kind} · {instrument.status.value}\n{instrument.uri}")
            item.setData(Qt.UserRole, instrument)
            item.setData(Qt.AccessibleTextRole, instrument.name)
            row = DeviceRow(instrument)
            self.rows.append(row)
            self.open_list.setItemWidget(item, row)
        self.update_rows()
        if not len(self.hub):
            item = QListWidgetItem("No device connected", self.open_list)
            item.setFlags(Qt.NoItemFlags)
        self.disconnect_all_button.setVisible(len(self.hub) > 0)
        self.connected_part.set_count(len(self.hub) or None)

    def update_rows(self) -> None:
        """What each connected device does now (state, settings, load)."""
        for index, row in enumerate(self.rows):
            row.show_summary(self.summary_of(row.instrument))
            item = self.open_list.item(index)
            if item is not None:
                item.setSizeHint(row.sizeHint())

    def summary_of(self, instrument: Instrument):
        from ...core.device_summary import summarize
        from ..devices.capture import capture_controller

        controller = None
        if instrument.capture is not None and instrument.status != InstrumentStatus.DISCONNECTED:
            controller = capture_controller(instrument)
        try:
            session = controller.settings() if controller is not None else None
            state = controller.state_text() if controller is not None else None
            received = controller.received if controller is not None else None
            return summarize(instrument, session, state, received, self._limits)
        except Exception:  # noqa: BLE001 - a device that went away between two refreshes: its bare status
            log.debug("The summary of %s cannot be made", instrument.name, exc_info=True)
            return summarize(instrument)

    def showEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().showEvent(event)
        self.update_rows()
        self.status_timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().hideEvent(event)
        self.status_timer.stop()

    def refresh(self) -> None:
        self.refresh_open()
        self.list.clear()
        open_uris = {instrument.uri for instrument in self.hub.instruments()}
        detected, manual = [], []
        show_simulators = preferences.get("devices.show_simulators")
        for backend in devices.backends():
            try:
                detected += backend.detected()
                manual += backend.manual_entries()
            except Exception:  # noqa: BLE001 - one kind of device must not hide the others
                log.debug("The devices of %s cannot be listed", backend.id, exc_info=True)
                continue
        detected = [entry for entry in detected if devices.entry_uri(entry) not in open_uris]
        for entry in detected:
            item = QListWidgetItem(icon("chip"), entry.label, self.list)
            item.setData(Qt.UserRole, entry)
            item.setToolTip("Click to connect")
        if not detected:
            item = QListWidgetItem("No device detected", self.list)
            item.setFlags(Qt.NoItemFlags)
        # every simulated device is in the group of the simulators, whatever backend lists it
        simulated = [entry for entry in manual if entry.simulated] if show_simulators else []
        for entry in manual:
            if entry.simulated:
                continue
            item = QListWidgetItem(icon("plus"), entry.label, self.list)
            item.setData(Qt.UserRole, entry)
        if simulated:
            opened = bool(preferences.get("devices.simulators_open"))
            group = QListWidgetItem(icon("down" if opened else "arrow-right"), f"Simulators ({len(simulated)})",
                                    self.list)
            group.setData(Qt.UserRole, SIMULATOR_GROUP)
            group.setToolTip("Simulated devices: click to show or hide them")
            font = group.font()
            font.setBold(True)
            group.setFont(font)
            for entry in simulated if opened else []:
                item = QListWidgetItem(icon("plus"), "    " + simulator_label(entry.label), self.list)
                item.setData(Qt.UserRole, entry)
                item.setToolTip(f"{entry.label}: click to connect")
        notice = None
        for backend in devices.backends():
            try:
                notice = backend.idle_notice()
            except Exception:  # noqa: BLE001 - enumeration problems must not disturb the list
                log.debug("notice = backend.idle_notice() failed: enumeration problems must not disturb the list", exc_info=True)
                notice = None
            if notice:
                break
        self.notice.setText(notice or "")
        self.notice.setVisible(bool(notice))
        self.firmware_button.setVisible(bool(notice))


#: the item of the device list that opens and closes the simulators
