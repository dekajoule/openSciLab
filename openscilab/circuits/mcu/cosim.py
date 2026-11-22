# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Microcontrollers and ngspice in lockstep (in the engine process, ``engines/process.py``).

* Before ngspice takes a step it asks how long the step may be (``GetSyncData``): the microcontrollers
  run ahead to its end - with the inputs they had - and the step is shortened to their first change
  of a pin, so an edge comes at its time.
* While ngspice solves the step it asks for the ``external`` sources of the pins (``GetVSRCData``):
  the level, whether the pin drives and its pull-up, at the time asked, from what the
  microcontrollers did.
* After each accepted point the inputs take the voltages of their nets: logic levels with the
  thresholds of the supply (and hysteresis), the analog inputs in volts.

What they sent on their UARTs, and their pins, go to the application with the points
(:meth:`CoSimulator.drain`); it sends what is typed into a serial monitor (:meth:`CoSimulator.request`).
"""

from __future__ import annotations

import bisect
import heapq
import logging
import threading

from .. import CircuitError
from .avr import EVENT_DIRECTION, EVENT_PORT, EVENT_UART, AvrMcu
from .boards import McuInfo, McuPin

log = logging.getLogger(__name__)

#: how far the timeline of a pin reaches back (seconds): ngspice may step back after a rejected step
KEEP = 0.01
#: a chip of a 6502 computer (``bus_chips.py``) changes its pins in a ramp this long (``boards.py``)
RAMP = 2e-6


class _PinState:
    """What a port pin did over time: (time, level, drives, pull-up)."""

    def __init__(self, pin: McuPin) -> None:
        self.pin = pin
        self.times: list[float] = [0.0]
        self.states: list[tuple[int, bool, bool]] = [(0, False, False)]
        self.input_level: bool | None = None

    def add(self, time: float, level: int, drives: bool, pullup: bool) -> None:
        state = (level, drives, pullup)
        if state == self.states[-1]:
            return
        time = max(time, self.times[-1])
        self.times.append(time)
        self.states.append(state)
        if len(self.times) > 64 and self.times[-1] - self.times[0] > KEEP:
            cut = bisect.bisect_left(self.times, self.times[-1] - KEEP)
            del self.times[:max(cut - 1, 0)]
            del self.states[:max(cut - 1, 0)]

    def rebase(self, now: float = 0.0, state: tuple[int, bool, bool] | None = None) -> None:
        """Forget the past: from ``now`` on the pin is in ``state`` (its current one by default) - after
        a restart of ngspice (its time starts at 0 again) or a reset of the chip."""
        self.times = [now]
        self.states = [state if state is not None else self.states[-1]]

    def at(self, time: float) -> tuple[int, bool, bool]:
        index = bisect.bisect_right(self.times, time) - 1
        return self.states[max(index, 0)]

    def next_change(self, after: float, until: float) -> float | None:
        index = bisect.bisect_right(self.times, after)
        if index < len(self.times) and self.times[index] <= until:
            return self.times[index]
        return None


class _PortState:
    """What a port of a chip did over time: (time, code) - the code holds its outputs and directions now and
    before the change (``boards.port_code``), the chip's output stages ramp from one to the other."""

    def __init__(self) -> None:
        self.times: list[float] = [0.0]
        self.codes: list[int] = [0]
        self.out = self.ddr = 0

    def add(self, time: float, out: int, ddr: int) -> None:
        if (out, ddr) == (self.out, self.ddr):
            return
        code = out | (ddr << 8) | (self.out << 16) | (self.ddr << 24)
        self.out, self.ddr = out, ddr
        time = max(time, self.times[-1])
        if time == self.times[-1] and len(self.times) > 1:
            # (two changes at once: from what it was before the first to the last)
            code = (code & 0xFFFF) | (self.codes[-1] & 0xFFFF0000)
            self.codes[-1] = code
            return
        self.times.append(time)
        self.codes.append(code)
        if len(self.times) > 64 and self.times[-1] - self.times[0] > KEEP:
            cut = bisect.bisect_left(self.times, self.times[-1] - KEEP)
            del self.times[:max(cut - 1, 0)]
            del self.codes[:max(cut - 1, 0)]

    def rebase(self, now: float) -> None:
        self.times = [now]
        self.codes = [self.out | (self.ddr << 8) | (self.out << 16) | (self.ddr << 24)]

    def next_change(self, after: float, until: float) -> float | None:
        index = bisect.bisect_right(self.times, after)
        if index < len(self.times) and self.times[index] <= until:
            return self.times[index]
        return None

    def value(self, time: float) -> float:
        """What its external source gives at ``time``: the code plus how far the ramp is (a fraction);
        after the ramp the code of the new state alone (as if it had always been)."""
        index = max(bisect.bisect_right(self.times, time) - 1, 0)
        code = self.codes[index]
        since = time - self.times[index]
        if since >= RAMP or index == 0:
            new = code & 0xFFFF
            return float(new | (new << 16))
        return code + max(since, 0.0) / RAMP * 0.999


class _Running:
    def __init__(self, info: McuInfo, mcu: AvrMcu) -> None:
        self.info = info
        self.mcu = mcu
        self.port_out: dict[str, int] = {}
        self.port_ddr: dict[str, int] = {}
        self.pins = {(pin.port, pin.bit): _PinState(pin) for pin in info.pins if not pin.analog_only}
        #: a chip's ports (their output stages read them)
        self.ports = {pin.port: _PortState() for pin in info.pins} if info.emulator == "chip" else {}
        #: (time, port) of the changes since the co-simulation last looked (nets it carries follow them)
        self.changed: list[tuple[float, str]] = []
        #: port -> the nets the co-simulation carries on it
        self.digital_ports: dict[str, set[str]] = {}
        for pin in info.pins:
            if pin.digital:
                self.digital_ports.setdefault(pin.port, set()).add(pin.net.lower())
        self.analog = [pin for pin in info.pins if pin.adc is not None]
        #: what it sent, by UART ("0" is Serial, "1" Serial1 ...)
        self.serial: dict[str, bytearray] = {}
        #: added to the chip's time to get ngspice's (the chip may have run before this run)
        self.time_offset = 0.0
        self._resync = False

    def apply_events(self, lock: threading.Lock) -> None:
        if self._resync:  # (after a reset: the registers as they are now, whatever was heard meanwhile)
            self._resync = False
            now = self.mcu.time + self.time_offset
            for port in sorted({port for port, _bit in self.pins}):
                registers = self.mcu.port(port)
                if registers is None:
                    continue
                self.port_out[port], self.port_ddr[port] = registers[0], registers[1]
                for bit in range(8):
                    state = self.pins.get((port, bit))
                    if state is not None:
                        drives = bool(registers[1] & (1 << bit))
                        level = (registers[0] >> bit) & 1
                        state.add(now, level, drives, (not drives) and bool(level))
        for event in self.mcu.events():
            time = event.time + self.time_offset
            if event.kind == EVENT_UART:
                with lock:
                    self.serial.setdefault(event.port, bytearray()).append(event.value)
                continue
            if event.kind == EVENT_PORT:
                self.port_out[event.port] = event.value
            elif event.kind == EVENT_DIRECTION:
                self.port_ddr[event.port] = event.value
            else:
                continue  # (EVENT_PIN of an older shim: not used)
            out, ddr = self.port_out.get(event.port, 0), self.port_ddr.get(event.port, 0)
            port_state = self.ports.get(event.port)
            if port_state is not None:
                port_state.add(time, out, ddr)
            self.changed.append((time, event.port))
            for bit in range(8):
                state = self.pins.get((event.port, bit))
                if state is None:
                    continue
                drives = bool(ddr & (1 << bit))
                level = (out >> bit) & 1
                state.add(time, level, drives, (not drives) and bool(level))

    def rebase(self, now: float = 0.0) -> None:
        """ngspice's time starts at ``now`` again: the chip's time maps onto it from here on."""
        self.time_offset = now - self.mcu.time
        for state in self.pins.values():
            state.rebase(now)
        for port in self.ports.values():
            port.rebase(now)

    def reset(self, now: float) -> None:
        """Start the firmware again at ngspice's time ``now``: the chip is as switched on - no pin drives,
        no pull-up - until the firmware says otherwise."""
        self.mcu.reset()  # (its cycle count starts at 0)
        self.time_offset = now
        self.port_out.clear()
        self.port_ddr.clear()
        for state in self.pins.values():
            state.rebase(now, (0, False, False))
            state.input_level = None
        for port in self.ports.values():
            port.out = port.ddr = 0
            port.rebase(now)
        self._resync = True


class CoSimulator:
    """See the module. ``mcus``: the :class:`~.boards.McuInfo` of the circuit's microcontrollers."""

    def __init__(self, mcus: list[McuInfo], circuit=None) -> None:
        self.running: dict[str, _Running] = {}
        self.sources: dict[str, tuple[_Running, _PinState, str]] = {}
        self.problems: list[str] = []
        #: guards what the application's threads touch (serial output, requests) against ngspice's thread
        self._lock = threading.Lock()
        self._requests: list[tuple[str, str, object]] = []
        self._time = 0.0  # (the history's time of the last point)
        self._raw_time = 0.0  # (ngspice's own time: 0 at every restart)
        self._simulation = None
        self.max_ahead = 50e-6
        for info in mcus:
            self._add(info)
        self._bind_sources()

    @property
    def vcc(self) -> float:
        """The supply of the first microcontroller (each has its own: ``info.vcc``)."""
        return next((running.info.vcc for running in self.running.values()), 5.0)

    def _add(self, info: McuInfo) -> _Running | None:
        try:
            if info.emulator == "chip":
                from .bus_chips import create

                mcu = create(info.core)
            else:
                mcu = AvrMcu(info.core, info.frequency, info.vcc)
            if info.firmware:
                mcu.load_firmware(info.firmware)
            elif info.needs_firmware:
                self.problems.append(f"{info.ref} has no firmware: it does nothing")
        except (CircuitError, OSError) as error:
            self.problems.append(f"{info.ref}: {error}")
            return None
        running = _Running(info, mcu)
        self.running[info.ref] = running
        return running

    def _bind_sources(self) -> None:
        self.sources = {}
        #: the nets the co-simulation carries: net -> its pins (running, state) and its level
        self.digital: dict[str, list] = {}
        #: (time, net) of changes of drivers of those nets still to be carried
        self._pending: list[tuple[float, str]] = []
        self._first_carry = True
        for running in self.running.values():
            for state in running.pins.values():
                if state.pin.digital:
                    self.digital.setdefault(state.pin.net.lower(), [[], False])[0].append((running, state))
        for running in self.running.values():
            if running.ports:
                for state in running.pins.values():
                    pin = state.pin
                    port = running.ports[pin.port]
                    if not pin.level:
                        continue
                    self.sources[pin.level.lower()] = (running, port, "code")
                continue
            for state in running.pins.values():
                pin = state.pin
                self.sources[pin.level] = (running, state, "level")
                self.sources[pin.drive] = (running, state, "drive")
                self.sources[pin.pullup] = (running, state, "pullup")

    def attach(self, simulation, restart: bool = False) -> None:
        """Let ``simulation`` (a :class:`~..engines.live.LocalSimulation`) run the microcontrollers."""
        from ..engines.live import MCU_STEP

        self._simulation = simulation
        self.max_ahead = simulation.max_step or MCU_STEP
        if restart:
            for running in self.running.values():
                running.rebase(0.0)
        simulation.prepare = self._prepare
        simulation.on_point = self._point
        simulation.extra_point = self._digital_values
        simulation.on_restart = self._restart
        self._watch(simulation)

    def _watch(self, simulation) -> None:
        for running in self.running.values():  # (the voltages at its pins at every point: _point)
            simulation.watched.update(state.pin.net.lower() for state in running.pins.values())
            simulation.watched.update(pin.net.lower() for pin in running.analog)

    def _restart(self, circuit) -> None:
        """The simulation starts again with ``circuit`` (its time at 0): the microcontrollers take their
        new wiring - a changed one starts its firmware anew, an unchanged one goes on - and their
        timelines start at 0 too."""
        infos = {info.ref: info for info in getattr(circuit, "mcus", [])}
        with self._lock:
            for ref in [ref for ref in self.running if ref not in infos]:
                self.running.pop(ref).mcu.close()
            for ref, info in infos.items():
                running = self.running.get(ref)
                if running is not None and running.info == info:
                    running.rebase(0.0)
                    continue
                if running is not None:
                    running.mcu.close()
                    del self.running[ref]
                self._add(info)
            self._bind_sources()
            self._raw_time = 0.0
        if self._simulation is not None:
            self._watch(self._simulation)

    def _prepare(self, engine) -> None:
        engine.synchronize(steps=True)
        engine.on_source = self._source
        engine.on_sync = self._sync

    # --------------------------------------------------- ngspice's thread
    def _advance(self, until: float) -> None:
        for running in self.running.values():
            local = until - running.time_offset
            if local > running.mcu.time:
                running.mcu.run_until(local)
                running.apply_events(self._lock)

    def _apply_requests(self, now: float) -> None:
        """Serial input and resets, applied between two steps (simavr is not thread-safe)."""
        with self._lock:
            requests, self._requests = self._requests, []
        for ref, action, data in requests:
            running = self.running.get(ref)
            if running is None:
                continue
            if action == "serial":
                uart, payload = data if isinstance(data, tuple) else ("0", data)
                running.mcu.uart_write(bytes(payload or b""), uart)
            elif action == "reset":
                running.reset(now)

    def _sync(self, time: float, delta: float, olddelta: float, redostep: int, location: int) -> float | None:
        if location != 0 or not self.running:
            return None
        self._raw_time = time
        if self._requests:
            self._apply_requests(time)
        delta = min(delta, self.max_ahead)
        end = time + delta
        self._advance(end)
        first = None
        chip = False
        for running in self.running.values():
            for state in (running.ports.values() if running.ports else running.pins.values()):
                change = state.next_change(time + 1e-12, end)
                if change is not None and (first is None or change < first):
                    first, chip = change, bool(running.ports)
        if first is not None and chip and first - time > 2e-9:
            # (a chip's change: a point just before it, then one just after - the nets the co-simulation
            # carries change sharply in the history, not along a long step)
            return first - time - 1e-9
        if first is not None and first - time > 1e-9:
            return first - time + 1e-10
        return delta

    def _source(self, name: str, time: float) -> float:
        found = self.sources.get(name)
        if found is None:
            return 0.0
        running, state, which = found
        if time > running.mcu.time + running.time_offset:
            self._advance(time)
        if which == "code":
            return state.value(time)
        level, drives, pullup = state.at(time)
        if which == "level":
            return running.info.vcc if level else 0.0
        if which == "drive":
            return 1.0 if drives else 0.0
        return 1.0 if pullup else 0.0

    def _level(self, entry: list, time: float) -> bool:
        """The level of a net the co-simulation carries: of the pin that drives it, else as it was."""
        for _running, state in entry[0]:
            level, drives, _pullup = state.at(time)
            if drives:
                return bool(level)
        return entry[1]

    def _digital_values(self, time: float) -> tuple[list, list]:
        """The nets the co-simulation carries, as volts at ngspice's ``time`` (for the history: probes)."""
        if not self.digital:
            return [], []
        vcc = self.vcc
        names = list(self.digital)
        return names, [vcc if self._level(self.digital[name], time) else 0.0 for name in names]

    def _collect(self, running: _Running) -> None:
        """Note when the nets it carries may change: at the changes of the ports that drive them."""
        if running.changed:
            for when, port in running.changed:
                for net in running.digital_ports.get(port, ()):
                    heapq.heappush(self._pending, (when, net))
            running.changed = []

    def _carry(self, time: float) -> None:
        """The nets the co-simulation carries take the levels their drivers have at ``time``; the chips that
        read a changed one react (their outputs come later: their delays) - until nothing changes now."""
        for running in self.running.values():
            self._collect(running)
        for _round in range(8):
            touched = []
            if self._first_carry:
                due = list(self.digital)
                self._first_carry = False
            else:
                due = []
                while self._pending and self._pending[0][0] <= time:
                    net = heapq.heappop(self._pending)[1]
                    if net not in due:
                        due.append(net)
            for net in due:
                entry = self.digital.get(net)
                if entry is None:
                    continue
                level = self._level(entry, time)
                entry[1] = level
                for running, state in entry[0]:
                    if state.states[-1][1] or state.input_level == level:
                        continue  # (it drives the net itself, or knows the level)
                    state.input_level = level
                    running.mcu.set_input_at(time - running.time_offset, state.pin.port, state.pin.bit, level)
                    if running not in touched:
                        touched.append(running)
            if not touched:
                return
            for running in touched:
                running.mcu.update(time - running.time_offset)
                running.apply_events(self._lock)
                self._collect(running)

    def _point(self, time: float, latest: dict) -> None:
        self._time = time
        if self.digital:
            self._carry(time)
        for running in self.running.values():
            vcc = running.info.vcc
            high, low = 0.6 * vcc, 0.3 * vcc
            reactive = getattr(running.mcu, "reactive", False)
            local = time - running.time_offset
            for state in running.pins.values():
                pin = state.pin
                if pin.digital or state.states[-1][1]:
                    continue  # (an output: what the net does is the pin's own doing; set_input would be filtered)
                value = latest.get(pin.net.lower()) if pin.net != "0" else 0.0
                if value is None:
                    continue
                volts = float(value.real if isinstance(value, complex) else value)
                level = state.input_level
                if level is None or (level and volts < low) or (not level and volts > high):
                    level = volts > (high + low) / 2 if level is None else not level
                    state.input_level = level
                    if reactive:
                        running.mcu.set_input_at(local, pin.port, pin.bit, level)
                    else:
                        running.mcu.set_input(pin.port, pin.bit, level)
            if reactive:  # (a chip of a 6502 computer answers its inputs at once: its outputs follow)
                running.mcu.update(local)
                running.apply_events(self._lock)
            for pin in running.analog:
                value = latest.get(pin.net.lower())
                if value is not None:
                    running.mcu.set_analog(pin.adc, float(value.real if isinstance(value, complex) else value))

    # ---------------------------------------------------------- the application
    def drain(self) -> dict | None:
        """What happened since the last call: serial output (by UART) and pin states by microcontroller."""
        found = {}
        with self._lock:
            for ref, running in self.running.items():
                serial, running.serial = running.serial, {}
                item = {"serial": bytes(serial.get("0", b"")),
                        "state": getattr(running.mcu, "state_text", None) or running.mcu.state,
                        **({"display": list(running.mcu.display_lines)} if hasattr(running.mcu, "display_lines")
                           else {}),
                        "time": running.mcu.time,
                        "ports": {port: (running.port_out.get(port, 0), running.port_ddr.get(port, 0))
                                  for port in set(running.port_out) | set(running.port_ddr)}}
                for uart, data in serial.items():
                    if uart != "0" and data:
                        item[f"serial{uart}"] = bytes(data)
                found[ref] = item
            if self.problems:
                found["#problems"] = list(self.problems)
                self.problems = []
        return found or None

    def request(self, ref: str, action: str, data=None):
        """Serial input (``data``: bytes, or (uart, bytes)) or a reset; taken at the next step."""
        if ref not in self.running:
            raise CircuitError(f"no microcontroller {ref}")
        if action not in ("serial", "reset"):
            raise CircuitError(f"unknown request {action!r}")
        with self._lock:
            self._requests.append((ref, action, data))

    def close(self) -> None:
        with self._lock:
            for running in self.running.values():
                running.mcu.close()
            self.running.clear()
            self.sources = {}


def attach(simulation, schematic, resolve, base_folder: str) -> list[str]:
    """Called by the document: give ``simulation`` (a :class:`~..engines.live.LiveSimulation`) the
    microcontrollers of its circuit; returns problems for people."""
    infos = getattr(simulation.circuit, "mcus", [])
    problems = []
    for info in infos:
        if not info.firmware and info.needs_firmware:
            what = "an image (a .bin or .hex file)" if info.emulator == "chip" else "a .hex or .elf file"
            problems.append(f"{info.ref} has no firmware (choose {what} in the inspector)")
    from . import avr

    avrs = [info for info in infos if info.emulator == "simavr"]
    if avrs and not avr.available():
        problems.append("the AVR emulator is not installed: the microcontrollers do not run")
        infos = [info for info in infos if info.emulator != "simavr"]
    simulation.mcus = list(infos)
    return problems
