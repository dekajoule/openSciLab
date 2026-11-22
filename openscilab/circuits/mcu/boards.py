# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The microcontrollers openSciLab emulates and how their pins are named.

A board (``mcu.uno``) names its pins as the Arduino does (D13, A0); a chip of KiCad's libraries
(ATmega328P-P) by its port (PB5, PC0). Either way a pin is a port, a bit and perhaps an analog input.

In the netlist every connected pin of a microcontroller is an output stage the co-simulation drives
(``external`` sources: the level, whether it drives, its pull-up) - see :func:`netlist_lines`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class PinMap:
    port: str
    bit: int
    #: its analog input (ADC channel), ``None``: none
    adc: int | None = None
    #: only analog (A6/A7 of a Nano): no port bit
    analog_only: bool = False


@dataclass(frozen=True)
class Board:
    name: str
    title: str
    #: simavr's name of the chip
    core: str
    frequency: float = 16e6
    #: pin name -> its port, bit, analog input
    pins: dict[str, PinMap] = field(default_factory=dict)
    #: pins that supply the circuit: name -> volts (the board's 5V and 3V3 pins)
    supplies: dict[str, float] = field(default_factory=dict)
    #: what runs it: "simavr" (an AVR and its firmware) or "chip" (a chip of a 6502 computer, ``bus_chips.py``)
    emulator: str = "simavr"
    #: pin name -> "in", "out", "oc" (open drain) or "io" (the default): the output stage it gets
    modes: dict[str, str] = field(default_factory=dict)
    #: it runs a firmware file (an EEPROM: its image) - without one it does nothing
    needs_firmware: bool = True


def _uno_pins(nano: bool = False) -> dict[str, PinMap]:
    pins = {f"D{n}": PinMap("D", n) for n in range(8)}
    pins.update({f"D{n}": PinMap("B", n - 8) for n in range(8, 14)})
    pins.update({f"A{n}": PinMap("C", n, n) for n in range(6)})
    if nano:
        pins.update({"A6": PinMap("C", 6, 6, True), "A7": PinMap("C", 7, 7, True)})
    return pins


_MEGA = ["E0", "E1", "E4", "E5", "G5", "E3", "H3", "H4", "H5", "H6", "B4", "B5", "B6", "B7", "J1", "J0", "H1", "H0", "D3", "D2", "D1", "D0", "A0", "A1", "A2", "A3", "A4", "A5", "A6", "A7", "C7", "C6", "C5", "C4", "C3", "C2", "C1", "C0", "D7", "G2", "G1", "G0", "L7", "L6", "L5", "L4", "L3", "L2", "L1", "L0", "B3", "B2", "B1", "B0"]


def _mega_pins() -> dict[str, PinMap]:
    pins = {f"D{n}": PinMap(code[0], int(code[1])) for n, code in enumerate(_MEGA)}
    pins.update({f"A{n}": PinMap("F", n, n) for n in range(8)})
    pins.update({f"A{n}": PinMap("K", n - 8, n) for n in range(8, 16)})
    return pins


def _chip_pins(ports: str, adc: dict[str, int]) -> dict[str, PinMap]:
    pins = {}
    for port in ports:
        for bit in range(8):
            name = f"P{port}{bit}"
            pins[name] = PinMap(port, bit, adc.get(name))
    pins.update({f"ADC{n}": PinMap("C", n, n, True) for n in (6, 7)})
    return pins


BOARDS: dict[str, Board] = {
    "mcu.uno": Board("mcu.uno", "Arduino Uno", "atmega328p", 16e6, _uno_pins(), {"5V": 5.0, "3V3": 3.3}),
    "mcu.nano": Board("mcu.nano", "Arduino Nano", "atmega328p", 16e6, _uno_pins(nano=True), {"5V": 5.0, "3V3": 3.3}),
    "mcu.mega2560": Board("mcu.mega2560", "Arduino Mega 2560", "atmega2560", 16e6, _mega_pins(), {"5V": 5.0, "3V3": 3.3}),
    "atmega328p": Board("atmega328p", "ATmega328P", "atmega328p", 16e6,
                        _chip_pins("BCD", {f"PC{n}": n for n in range(6)})),
    "atmega168": Board("atmega168", "ATmega168", "atmega168", 16e6, _chip_pins("BCD", {f"PC{n}": n for n in range(6)})),
    "atmega2560": Board("atmega2560", "ATmega2560", "atmega2560", 16e6,
                        _chip_pins("ABCDEFGHJKL", {**{f"PF{n}": n for n in range(8)}, **{f"PK{n}": n + 8 for n in range(8)}})),
    "atmega1280": Board("atmega1280", "ATmega1280", "atmega1280", 16e6,
                        _chip_pins("ABCDEFGHJKL", {**{f"PF{n}": n for n in range(8)}, **{f"PK{n}": n + 8 for n in range(8)}})),
}

def _bus_pins(groups: dict[str, list[str]]) -> dict[str, PinMap]:
    """Pins of a chip by port: ``{"A": ["A0", ..., "A7"], ...}``."""
    return {name.upper(): PinMap(port, bit) for port, names in groups.items() for bit, name in enumerate(names)}


def _chip(name: str, title: str, core: str, groups: dict[str, list[str]], modes: dict[str, str],
          needs_firmware: bool = False) -> Board:
    pins = _bus_pins(groups)
    by_pin = {pin: modes.get(port, "io") for port, names in groups.items() for pin in (n.upper() for n in names)}
    return Board(name, title, core, 0.0, pins, {}, "chip", by_pin, needs_firmware)


_ADDRESS = [f"A{n}" for n in range(8)], [f"A{n}" for n in range(8, 16)]
_DATA = [f"D{n}" for n in range(8)]
CHIP_BOARDS = {
    "chip.w65c02": _chip("chip.w65c02", "W65C02S", "w65c02",
                         {"A": _ADDRESS[0], "B": _ADDRESS[1], "D": _DATA,
                          "C": ["RWB", "SYNC", "VPB", "MLB", "PHI1O", "PHI2O"],
                          "I": ["PHI2", "RESB", "IRQB", "NMIB", "RDY", "BE", "SOB"]},
                         {"A": "out", "B": "out", "C": "out", "I": "in"}),
    "chip.w65c22": _chip("chip.w65c22", "W65C22S", "w65c22",
                         {"D": _DATA, "A": [f"PA{n}" for n in range(8)], "B": [f"PB{n}" for n in range(8)],
                          "R": ["RS0", "RS1", "RS2", "RS3"], "C": ["CS1", "CS2B", "RWB", "PHI2", "RESB", "CA1", "CB1"],
                          "X": ["CA2", "CB2"], "Q": ["IRQB"]},
                         {"R": "in", "C": "in", "X": "in", "Q": "oc"}),
    "chip.sram62256": _chip("chip.sram62256", "62256 SRAM", "sram62256",
                            {"A": _ADDRESS[0], "B": _ADDRESS[1][:7], "D": _DATA, "C": ["CE", "OE", "WE"]},
                            {"A": "in", "B": "in", "C": "in"}),
    "chip.eeprom28c256": _chip("chip.eeprom28c256", "28C256 EEPROM", "eeprom28c256",
                               {"A": _ADDRESS[0], "B": _ADDRESS[1][:7], "D": _DATA, "C": ["CE", "OE", "WE"]},
                               {"A": "in", "B": "in", "C": "in"}, needs_firmware=True),
    "chip.hd44780": _chip("chip.hd44780", "HD44780 display", "hd44780",
                          {"D": [f"DB{n}" for n in range(8)], "C": ["RS", "RW", "E"]}, {"C": "in"}),
}
BOARDS.update(CHIP_BOARDS)


def emulator_of(board_name: str) -> str:
    """What runs a microcontroller of ``board_name`` (a board of KiCad's AVRs: simavr)."""
    board = BOARDS.get(board_name)
    return board.emulator if board is not None else "simavr"


GROUND = ("GND", "AGND", "GND1", "GND2", "GND3", "VSS")


def pin_of(board: Board, label: str) -> PinMap | None:
    """The port pin a pin name of a symbol means (``D13``, ``D13/SCK``, ``PB5``, ``PB5 (SCK)``)."""
    for part in re.split(r"[/(),\s]+", label.upper()):
        if part in board.pins:
            return board.pins[part]
    return None


@dataclass
class McuPin:
    """A pin of a placed microcontroller with what it is connected to."""

    name: str
    port: str
    bit: int
    adc: int | None
    analog_only: bool
    #: the net in SPICE
    net: str
    #: names of its external sources: level, drive, pull-up
    level: str
    drive: str
    pullup: str
    #: the net is carried by the co-simulation (chips only, ``spice._chip_nets``), not by ngspice
    digital: bool = False


@dataclass
class McuInfo:
    """What the co-simulation needs to run a placed microcontroller (picklable)."""

    ref: str
    board: str
    core: str
    frequency: float
    firmware: str
    vcc: float
    pins: list[McuPin] = field(default_factory=list)
    #: "simavr" or "chip" (:attr:`Board.emulator`)
    emulator: str = "simavr"
    needs_firmware: bool = True


def _safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", text).lower()


def netlist_lines(ref: str, board: Board, pin_nets: dict[str, tuple[str, str]], vcc: float, firmware: str,
                  digital: set[str] = frozenset()) -> tuple[list[str], McuInfo]:
    """The netlist of a placed microcontroller: ``pin_nets`` maps its pin numbers to (pin name, SPICE net).

    Each connected port pin: a level source and an output resistance behind a switch the drive source
    closes, and a pull-up behind a switch of its own; supply pins of a board are sources, ground pins
    are tied to ground."""
    info = McuInfo(ref, board.name, board.core, board.frequency, firmware, vcc, emulator=board.emulator,
                   needs_firmware=board.needs_firmware)
    lines = [f"V{ref}_vcc {_safe(ref)}_vcc 0 DC {vcc:g}"]
    for number, (label, net) in sorted(pin_nets.items()):
        upper = label.upper()
        if net == "0" and board.emulator == "chip":
            found = pin_of(board, label)
            if found is not None and upper not in GROUND:  # (an input tied to ground: it reads low)
                info.pins.append(McuPin(label, found.port, found.bit, None, False, "0", "", "", ""))
            continue
        if net == "0":
            continue
        if upper in GROUND:
            lines.append(f"R{ref}_g{_safe(number)} {net} 0 1m")
            continue
        # (KiCad's modules call the pins +5V and +3V3 / +3.3V; VCC of a module is its 5 V)
        supply_key = upper.lstrip("+").replace(".", "")
        supply = board.supplies.get("5V" if supply_key in ("VCC", "5V0") else supply_key)
        if supply is not None:
            lines.append(f"V{ref}_s{_safe(number)} {_safe(ref)}_s{_safe(number)} 0 DC {supply:g}")
            lines.append(f"R{ref}_s{_safe(number)} {_safe(ref)}_s{_safe(number)} {net} 50m")
            continue
        found = pin_of(board, label)
        if found is None:
            continue
        stem = f"{_safe(ref)}_{_safe(label.split('/')[0])}"
        level, drive, pullup = f"v{stem}_l", f"v{stem}_d", f"v{stem}_u"
        if board.emulator == "chip" and net in digital:  # (the co-simulation carries the net: no output stage)
            info.pins.append(McuPin(label, found.port, found.bit, None, False, net, "", "", "", digital=True))
            continue
        if board.emulator == "chip":
            lines += _chip_stage(ref, stem, net, found, board.modes.get(upper, "io"), vcc)
            info.pins.append(McuPin(label, found.port, found.bit, found.adc, found.analog_only, net,
                                    _port_source(ref, found.port, "code"), "",
                                    pullup))
            continue
        info.pins.append(McuPin(label, found.port, found.bit, found.adc, found.analog_only, net, level, drive, pullup))
        if found.analog_only:
            lines.append(f"R{stem}_in {net} 0 100Meg")
            continue
        lines += [
            f"{level} {stem}_l 0 DC 0 external",
            f"{drive} {stem}_d 0 DC 0 external",
            f"{pullup} {stem}_u 0 DC 0 external",
            f"R{stem}_o {stem}_l {stem}_o 25",
            f"S{stem}_o {stem}_o {net} {stem}_d 0 osl_mcu_switch",
            f"R{stem}_p {_safe(ref)}_vcc {stem}_p 35k",
            f"S{stem}_p {stem}_p {net} {stem}_u 0 osl_mcu_switch",
            f"C{stem}_in {net} 0 10p",
        ]
    if board.emulator == "chip":
        driven = {pin.port for pin in info.pins
                  if board.modes.get(pin.name.upper(), "io") != "in" and not pin.digital and pin.net != "0"}
        for port in sorted(driven):
            name = _port_source(ref, port, "code")
            lines.append(f"{name} {name[1:]} 0 DC 0 external")
    else:
        lines.append(".model osl_mcu_switch SW(Ron=1 Roff=1G Vt=0.5 Vh=0.1)")
    return lines, info


#: a chip's pin changes in a ramp this long (``cosim.RAMP``)
RAMP = 2e-6


def _port_source(ref: str, port: str, which: str) -> str:
    """The external source of a chip's port: its code (outputs and directions now and before the last
    change, see :class:`~.cosim._PortState`) plus, as its fraction, how far the ramp between them is."""
    return f"v{_safe(ref)}_p{_safe(port)}_{which}"


def _chip_stage(ref: str, stem: str, net: str, pin: PinMap, mode: str, vcc: float) -> list[str]:
    """The output stage of a pin of a chip: bits of its port's code, from the old to the new state as the
    ramp goes (computed by ngspice - one callback per port, none per pin)."""
    lines = [f"C{stem}_in {net} 0 10p"]
    if mode == "in":
        return lines
    source = f"V({_port_source(ref, pin.port, 'code')[1:]})"
    code = f"floor({source})"

    def bit(shift: int) -> str:
        return f"(floor({code}/{2 ** shift})-2*floor({code}/{2 ** (shift + 1)}))"

    ramp = f"({source}-{code})/0.999"
    level = f"({bit(16 + pin.bit)}+({bit(pin.bit)}-{bit(16 + pin.bit)})*{ramp})"
    drive = f"({bit(24 + pin.bit)}+({bit(8 + pin.bit)}-{bit(24 + pin.bit)})*{ramp})"
    if mode == "out":
        lines += [f"B{stem}_l {stem}_l 0 V={vcc:g}*{level}", f"R{stem}_o {stem}_l {net} 50"]
    elif mode == "oc":
        lines.append(f"B{stem}_o {net} 0 I={drive}*V({net})/50")
    else:
        lines.append(f"B{stem}_o 0 {net} I={drive}*({vcc:g}*{level}-V({net}))/50")
    return lines
