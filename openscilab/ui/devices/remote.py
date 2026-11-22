# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Remote devices in the device list: the devices connected to the server (``remote:<name>``), the
simulated ones (``remote-sim:<demo>``) and an entry that opens the settings of the server."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

from ...core.instrument import Instrument
from . import DeviceBackend, DeviceEntry

BACKEND_ID = "remote"
SIM_BACKEND_ID = "remote-sim"
DEMO_TITLES = {"climate": "Simulated remote climate station", "audio": "Simulated remote microphone",
               "echo": "Simulated remote echo",
               # the timings a remote device can have: a DAQ behind USB on another computer (drifting
               # sample clock, a sync line, a loopback), its clock following UTC as with PTP, its
               # sample clock shared with openSciLab's devices
               "daq": "Simulated remote DAQ (drifting clock, USB, sync line)",
               "daq?timescale=utc": "Simulated remote DAQ, clock follows UTC (PTP)",
               "daq?shared_clock=1": "Simulated remote DAQ, shared sample clock"}
#: names of the devices (the titles say what they simulate)
DEMO_NAMES = {"daq": "Remote DAQ", "daq?timescale=utc": "Remote DAQ (UTC)",
              "daq?shared_clock=1": "Remote DAQ (shared clock)"}


class RemoteBackend(DeviceBackend):
    id = BACKEND_ID

    def detected(self) -> list[DeviceEntry]:
        from ...driver.remote import servers

        server = servers.running_main()
        if server is None:
            return []
        entries = []
        for connection in server.devices():
            state = connection.clock.state()
            accuracy = f", ± {state.uncertainty * 1000:.1f} ms" if state.uncertainty < 1 else ""
            entries.append(DeviceEntry(BACKEND_ID, "device", connection.name,
                                       f"{connection.name} (remote, {connection.address[0]}{accuracy})"))
        return entries

    def manual_entries(self) -> list[DeviceEntry]:
        from ...driver.remote import servers

        running = servers.running_main()
        label = (f"Remote devices: port {running.port}..." if running is not None
                 else "Remote devices (off): settings...")
        return [DeviceEntry(BACKEND_ID, "settings", None, label)]

    def open_instrument(self, entry: DeviceEntry, parent: QWidget) -> Optional[Instrument]:
        from ...driver.remote import servers
        from ...driver.remote.instrument import make_instrument

        if entry.kind == "settings":
            window = parent.window() if parent is not None else None
            show = getattr(window, "show_preferences", None)
            if show is not None:
                show("Remote devices")
            return None
        found = servers.find(str(entry.value))
        if found is None:
            raise ValueError(f"{entry.value} is no longer connected")
        return make_instrument(found[0], str(entry.value), uri=f"remote:{entry.value}")

    def connect(self, entry: DeviceEntry, parent: QWidget):
        raise ValueError("a remote device has no capture driver")


class RemoteSimulatorBackend(DeviceBackend):
    id = SIM_BACKEND_ID

    def manual_entries(self) -> list[DeviceEntry]:
        return [DeviceEntry(SIM_BACKEND_ID, "demo", name, title, simulated=True)
                for name, title in DEMO_TITLES.items()]

    def open_instrument(self, entry: DeviceEntry, parent: QWidget) -> Optional[Instrument]:
        from ...driver.remote.simulated import open_simulated_remote
        from .. import background

        hub = getattr(parent, "hub", None)
        taken = {instrument.uri for instrument in hub.instruments()} if hub is not None else set()
        from ...driver.remote.simulated import address as remote_address
        from ...driver.remote.simulated import parse

        demo_name, _instance, options = parse(f"remote-sim:{entry.value}")
        address, number = remote_address(demo_name, 1, options), 1
        while address in taken:
            number += 1
            address = remote_address(demo_name, number, options)
        instrument = background.run(parent, "Starting the simulated device...", lambda: open_simulated_remote(address))
        title = (DEMO_NAMES.get(str(entry.value))
                 or DEMO_TITLES.get(str(entry.value), address).removeprefix("Simulated "))
        instrument.name = title[:1].upper() + title[1:] + (f" ({number})" if number > 1 else "")
        return instrument

    def connect(self, entry: DeviceEntry, parent: QWidget):
        raise ValueError("a remote device has no capture driver")


class RemoteServer(QObject):
    """The server remote devices connect to, while a window is open (an extension of the window,
    ``ui/sections.py``, registered by ``plugins/remote.py``): started, restarted or stopped as the
    settings *Remote devices* say; a device that connects or goes is noted in the status bar and the
    Devices section lists it."""

    #: a remote device connected or went: (event, name), from the server's thread
    _event = Signal(str, str)

    def __init__(self, services) -> None:
        super().__init__(services)
        self.services = services
        self._event.connect(self._device_event)
        self.apply()

    def apply(self) -> None:
        """Start, restart or stop the server as the settings say."""
        from ...core import preferences
        from ...driver.remote import servers

        servers.managed = True  # flows do not start it by themselves in the application
        if not preferences.get("remote.enabled"):
            servers.stop_main()
            return
        token = str(preferences.get("remote.token") or "")
        if not token:
            token = servers.new_token()
            preferences.update({"remote.token": token})
        try:
            server = servers.start_main(int(preferences.get("remote.port")), token,
                                        beacon=bool(preferences.get("remote.beacon")))
        except OSError as error:
            self.services.notify(f"Remote devices: port {preferences.get('remote.port')} cannot be "
                                 f"used ({error})", 10000)
            return
        server.subscribe(lambda event, connection: self._event.emit(event, connection.name))

    def preferences_changed(self, changes: dict) -> None:
        if any(key.startswith("remote.") for key in changes):
            self.apply()
        if any(key.startswith("timing.") for key in changes):
            from ...driver.remote import servers

            for connection in servers.connections():
                connection.use_timescale()  # (this computer's clock follows a time scale now, or no more)

    def close(self) -> None:
        from ...driver.remote import servers

        servers.stop_main()

    def _device_event(self, event: str, name: str) -> None:
        self.services.notify(f"Remote device {name} {event}", 6000)
        self.services.refresh_devices()
