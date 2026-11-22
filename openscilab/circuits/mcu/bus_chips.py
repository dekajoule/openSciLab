# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The chips of a 6502 computer as parts of a circuit: the W65C02S, the W65C22S, a static RAM, a parallel
EEPROM and an HD44780 display. They run in the co-simulation (:mod:`.cosim`) as the AVR does - their
pins are output stages of the circuit, their inputs follow its nets - but in Python and driven by what
they see: a chip changes its outputs a short time after its inputs changed (its delays, below), so a
6502 bus works through the nets of the circuit, the address decoder of real gates between the chips.

Every chip speaks the interface of :class:`~.avr.AvrMcu` that the co-simulation uses (``run_until``,
``events``, ``set_input``, ``port``, ``reset``) and, for its reactions, ``set_input_at(time, ...)`` and
``update(time)``: the inputs of a point, then what follows from them. Pins are grouped into 8-bit
ports (:data:`~.boards.BOARDS`, ``chip.*``); a port's level and direction are its events.
"""

from __future__ import annotations

import os

from .. import CircuitError
from .avr import EVENT_DIRECTION, EVENT_PORT, EVENT_UART, Event
from .cpu65c02 import Core, disassemble


class BusChip:
    """The common part: inputs by port, outputs as events, a time that only follows the simulation."""

    #: the co-simulation calls :meth:`set_input_at` and :meth:`update` with the time of each point
    reactive = True
    #: input levels before the first point (port -> byte)
    DEFAULT_INPUTS: dict[str, int] = {}

    def __init__(self, params: dict | None = None) -> None:
        self.params = dict(params or {})
        self._time = 0.0
        self._events: list[Event] = []
        self.state = "running"
        self.inputs: dict[str, int] = dict(self.DEFAULT_INPUTS)
        self.previous: dict[str, int] = dict(self.inputs)
        self.out: dict[str, int] = {}
        self.ddr: dict[str, int] = {}
        self._changed = False

    # ------------------------------------------------------------ AvrMcu's interface
    @property
    def time(self) -> float:
        return self._time

    @property
    def cycle(self) -> int:
        return 0

    def run_until(self, seconds: float) -> str:
        if seconds > self._time:
            self._time = seconds
        return self.state

    def events(self) -> list[Event]:
        events, self._events = self._events, []
        events.sort(key=lambda event: event.time)
        return events

    def set_input(self, port: str, bit: int, level: bool) -> None:
        self.set_input_at(self._time, port, bit, level)

    def set_input_at(self, time: float, port: str, bit: int, level: bool) -> None:
        value = self.inputs.get(port, 0)
        value = (value | (1 << bit)) if level else (value & ~(1 << bit))
        if value != self.inputs.get(port, 0):
            self.inputs[port] = value
            self._changed = True

    def update(self, time: float) -> None:
        """React to the inputs of the point at ``time`` (all of them set)."""
        if not self._changed:
            return
        self._changed = False
        self.react(time)
        self.previous = dict(self.inputs)

    def react(self, time: float) -> None:
        raise NotImplementedError

    def set_analog(self, channel: int, volts: float) -> None:
        pass

    def uart_write(self, data: bytes, uart: str = "0") -> None:
        pass

    def port(self, name: str) -> tuple[int, int, int] | None:
        if name not in self.out and name not in self.ddr:
            return None
        return self.out.get(name, 0), self.ddr.get(name, 0), self.inputs.get(name, 0)

    def load_firmware(self, path: str) -> None:
        pass

    def reset(self) -> None:
        self.inputs = dict(self.DEFAULT_INPUTS)
        self.previous = dict(self.inputs)
        for port in list(self.out):
            self.drive(port, 0, 0, self._time)

    def close(self) -> None:
        pass

    # ------------------------------------------------------------ helpers
    def bit(self, port: str, bit: int) -> bool:
        return bool(self.inputs.get(port, 0) >> bit & 1)

    def was(self, port: str, bit: int) -> bool:
        return bool(self.previous.get(port, 0) >> bit & 1)

    def rose(self, port: str, bit: int) -> bool:
        return self.bit(port, bit) and not self.was(port, bit)

    def fell(self, port: str, bit: int) -> bool:
        return self.was(port, bit) and not self.bit(port, bit)

    def drive(self, port: str, value: int, ddr: int, time: float) -> None:
        """From ``time`` on the pins of ``port`` drive ``value`` where ``ddr`` has a 1 (the others high
        impedance)."""
        value &= 0xFF
        ddr &= 0xFF
        if self.ddr.get(port) != ddr:
            self.ddr[port] = ddr
            self._events.append(Event(time, EVENT_DIRECTION, port, 0, ddr))
        if self.out.get(port) != value:
            self.out[port] = value
            self._events.append(Event(time, EVENT_PORT, port, 0, value))

    def send_text(self, text: str, time: float) -> None:
        for byte in text.encode("latin-1", "replace"):
            self._events.append(Event(time, EVENT_UART, "0", 0, byte))


def _seconds(params: dict, name: str, default: float) -> float:
    from .. import values

    found = values.parse_optional(str(params.get(name, "")))
    return default if found is None else found


# ---------------------------------------------------------------------------- the CPU
class W65C02(BusChip):
    """Ports: A = A0-A7, B = A8-A15, D = D0-D7, C (outputs) = RWB, SYNC, VPB, MLB, PHI1O, PHI2O;
    I (inputs) = PHI2, RESB, IRQB, NMIB, RDY, BE, SOB.

    A cycle starts at the falling edge of PHI2: the address, RWB and SYNC change ``address_delay``
    later; a write drives the data while PHI2 is high; a read takes the data at the falling edge,
    which ends the cycle; the data of a write are held ``hold`` past it."""

    DEFAULT_INPUTS = {"I": 0b1111110}  # (PHI2 low, the others high)
    PHI2, RESB, IRQB, NMIB, RDY = 0, 1, 2, 3, 4

    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params)
        # (longer than the data sheet's 10 ns: the address and the data of a write stay until the decoder's
        # gates - real ones in the circuit, with their edges - have ended the cycle)
        self.address_delay = _seconds(self.params, "address_delay", 400e-9)
        self.data_delay = _seconds(self.params, "data_delay", 100e-9)
        self.hold = _seconds(self.params, "hold", 300e-9)
        self.core = Core()
        self.core.reset = True
        self._cycles = self.core.run()
        self.current = next(self._cycles)
        self.count = 0
        self._output_cycle(0.0)

    def reset(self) -> None:
        super().reset()
        self.core = Core()
        self.core.reset = True
        self._cycles = self.core.run()
        self.current = next(self._cycles)
        self._output_cycle(self._time)

    def _output_cycle(self, time: float) -> None:
        cycle = self.current
        self.drive("A", cycle.address & 0xFF, 0xFF, time)
        self.drive("B", cycle.address >> 8, 0xFF, time)
        phi2 = self.bit("I", self.PHI2)
        control = ((0 if cycle.write else 1) | (2 if cycle.sync else 0) | (0 if cycle.vector else 4)
                   | (0 if cycle.lock else 8) | (0 if phi2 else 16) | (32 if phi2 else 0))
        self.drive("C", control, 0x3F, time)

    @property
    def state_text(self) -> str:
        start, opcode = self.core.instruction
        return f"PC ${start:04X}  A ${self.core.a:02X} X ${self.core.x:02X} Y ${self.core.y:02X}"

    def react(self, time: float) -> None:
        core = self.core
        if self.fell("I", self.RESB):
            core.reset = True
            self._cycles = core.run()
            self.current = next(self._cycles)
            self.drive("D", self.out.get("D", 0), 0, time + self.hold)
            self._output_cycle(time + self.address_delay)
        core.irq = not self.bit("I", self.IRQB)
        if self.fell("I", self.NMIB):
            core.nmi_edge = True
        if self.rose("I", self.PHI2):
            control = self.out.get("C", 0)
            self.drive("C", (control & ~0x30) | 0x20, 0x3F, time + 10e-9)
            if self.current.write:
                self.drive("D", self.current.data or 0, 0xFF, time + self.data_delay)
        elif self.fell("I", self.PHI2):
            control = self.out.get("C", 0)
            self.drive("C", (control & ~0x30) | 0x10, 0x3F, time + 10e-9)
            if not self.bit("I", self.RDY) and not self.current.write:
                return  # (RDY low holds a read cycle)
            core.reset = not self.bit("I", self.RESB)  # (sampled at the end of each cycle; high at power on: runs)
            if self.current.write:
                self.drive("D", self.current.data or 0, 0, time + self.hold)
                value = None
            else:
                value = self.inputs.get("D", 0)
            self.current = self._cycles.send(value)
            self.count += 1
            self.state = "reset" if core.reset else ("stopped" if core.stopped else
                                                     ("waiting" if core.waiting else "running"))
            self._output_cycle(time + self.address_delay)


# ---------------------------------------------------------------------------- memories
def load_image(path: str, size: int) -> bytearray:
    """A ROM image: a binary file (its last ``size`` bytes when it is larger - a 64 KB image of the
    whole address space) or Intel HEX (addresses taken modulo ``size``)."""
    if not path:
        return bytearray([0xFF] * size)
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as error:
        raise CircuitError(f"{os.path.basename(path)}: {error}") from None
    image = bytearray([0xFF] * size)
    if path.lower().endswith((".hex", ".ihx", ".ihex")) or data[:1] == b":":
        base = 0
        for number, line in enumerate(data.decode("ascii", "replace").splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            if not line.startswith(":"):
                raise CircuitError(f"{os.path.basename(path)}, line {number}: no Intel HEX")
            raw = bytes.fromhex(line[1:])
            count, address, kind = raw[0], (raw[1] << 8) | raw[2], raw[3]
            if sum(raw) & 0xFF:
                raise CircuitError(f"{os.path.basename(path)}, line {number}: wrong checksum")
            if kind == 0:
                for offset, byte in enumerate(raw[4:4 + count]):
                    image[(base + address + offset) % size] = byte
            elif kind == 2:
                base = ((raw[4] << 8) | raw[5]) << 4
            elif kind == 4:
                base = ((raw[4] << 8) | raw[5]) << 16
            elif kind == 1:
                break
        return image
    if len(data) > size:
        data = data[-size:]
    image[:len(data)] = data
    return image


class Memory(BusChip):
    """A JEDEC 32K × 8 memory: A = A0-A7, B = A8-A14, D = D0-D7, C = /CE, /OE, /WE (inputs). It drives the
    data ``access`` after the address or the selects changed while /CE and /OE are low and /WE high;
    a write (RAM) stores the data when /WE or /CE goes high."""

    DEFAULT_INPUTS = {"C": 0b111}
    SIZE = 32768

    def __init__(self, params: dict | None = None, writable: bool = True) -> None:
        super().__init__(params)
        self.writable = writable
        self.access = _seconds(self.params, "access", 55e-9 if writable else 150e-9)
        self.memory = bytearray(self.SIZE) if writable else bytearray([0xFF] * self.SIZE)
        self.drive("D", 0, 0, 0.0)

    def load_firmware(self, path: str) -> None:
        if not self.writable:
            self.memory = load_image(path, self.SIZE)

    def reset(self) -> None:
        super().reset()  # (the contents stay: a reset of the circuit is no power cycle)

    def address(self) -> int:
        return (self.inputs.get("A", 0) | (self.inputs.get("B", 0) << 8)) & (self.SIZE - 1)

    def react(self, time: float) -> None:
        selected = not self.bit("C", 0)
        output = not self.bit("C", 1)
        write = not self.bit("C", 2)
        was_writing = (not self.was("C", 0)) and (not self.was("C", 2))
        if self.writable and was_writing and not (selected and write):
            self.memory[self.address()] = self.inputs.get("D", 0)
        if selected and output and not write:
            self.drive("D", self.memory[self.address()], 0xFF, time + self.access)
        else:
            self.drive("D", self.out.get("D", 0), 0, time + 20e-9)


class SRAM62256(Memory):
    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params, writable=True)


class EEPROM28C256(Memory):
    """The EEPROM as programmed (its image: the firmware of the part); the circuit cannot write it."""

    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params, writable=False)


# ---------------------------------------------------------------------------- the VIA
class W65C22(BusChip):
    """The registers ORB, ORA, DDRB, DDRA, the timers T1 and T2 with their interrupt, IFR, IER, ACR and
    PCR (stored; handshakes and the shift register are not modelled).

    Ports: D = D0-D7, A = PA0-PA7, B = PB0-PB7, R = RS0-RS3, C = CS1, CS2B, RWB, PHI2, RESB, CA1, CB1;
    X = CA2, CB2 (inputs); Q = IRQB (open drain)."""

    DEFAULT_INPUTS = {"C": 0b1110010}  # (CS2B, RESB, CA1, CB1 high)

    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params)
        self.data_delay = _seconds(self.params, "data_delay", 50e-9)
        self._clear()
        self.drive("D", 0, 0, 0.0)
        self.drive("A", 0, 0, 0.0)
        self.drive("B", 0, 0, 0.0)
        self.drive("Q", 0, 0, 0.0)

    def _clear(self) -> None:
        self.orb = self.ora = self.ddrb = self.ddra = 0
        self.t1_latch = self.t1_counter = 0xFFFF
        self.t2_latch_low = 0xFF
        self.t2_counter = 0xFFFF
        self.t1_running = self.t2_running = False
        self.acr = self.pcr = self.ifr = self.ier = self.sr = 0

    def reset(self) -> None:
        super().reset()
        self._clear()

    def selected(self) -> bool:
        return self.bit("C", 0) and not self.bit("C", 1)

    def register(self) -> int:
        return self.inputs.get("R", 0) & 0x0F

    def _port_value(self, port: str, output: int, ddr: int) -> int:
        return (output & ddr) | (self.inputs.get(port, 0) & ~ddr & 0xFF)

    def read(self, register: int) -> int:
        if register == 0:
            return self._port_value("B", self.orb, self.ddrb)
        if register in (1, 15):
            if register == 1:
                self.ifr &= ~0x03
            return self._port_value("A", self.ora, self.ddra)
        if register == 2:
            return self.ddrb
        if register == 3:
            return self.ddra
        if register == 4:
            self.ifr &= ~0x40
            return self.t1_counter & 0xFF
        if register == 5:
            return self.t1_counter >> 8
        if register == 6:
            return self.t1_latch & 0xFF
        if register == 7:
            return self.t1_latch >> 8
        if register == 8:
            self.ifr &= ~0x20
            return self.t2_counter & 0xFF
        if register == 9:
            return self.t2_counter >> 8
        if register == 10:
            return self.sr
        if register == 11:
            return self.acr
        if register == 12:
            return self.pcr
        if register == 13:
            return self.ifr | (0x80 if self.ifr & self.ier & 0x7F else 0)
        return self.ier | 0x80

    def write(self, register: int, value: int) -> None:
        if register == 0:
            self.orb = value
        elif register in (1, 15):
            self.ora = value
        elif register == 2:
            self.ddrb = value
        elif register == 3:
            self.ddra = value
        elif register in (4, 6):
            self.t1_latch = (self.t1_latch & 0xFF00) | value
        elif register == 5:
            self.t1_latch = (self.t1_latch & 0x00FF) | (value << 8)
            self.t1_counter = self.t1_latch
            self.t1_running = True
            self.ifr &= ~0x40
        elif register == 7:
            self.t1_latch = (self.t1_latch & 0x00FF) | (value << 8)
            self.ifr &= ~0x40
        elif register == 8:
            self.t2_latch_low = value
        elif register == 9:
            self.t2_counter = (value << 8) | self.t2_latch_low
            self.t2_running = True
            self.ifr &= ~0x20
        elif register == 10:
            self.sr = value
        elif register == 11:
            self.acr = value
        elif register == 12:
            self.pcr = value
        elif register == 13:
            self.ifr &= ~(value & 0x7F)
        elif register == 14:
            self.ier = (self.ier | (value & 0x7F)) if value & 0x80 else (self.ier & ~value & 0x7F)

    def _tick(self) -> None:
        """One PHI2 cycle of the timers."""
        if self.t1_counter == 0:
            if self.t1_running:
                self.ifr |= 0x40
                if not self.acr & 0x40:
                    self.t1_running = False
            self.t1_counter = self.t1_latch if self.acr & 0x40 else 0xFFFF
        else:
            self.t1_counter -= 1
        if not self.acr & 0x20:
            if self.t2_counter == 0 and self.t2_running:
                self.ifr |= 0x20
                self.t2_running = False
            self.t2_counter = (self.t2_counter - 1) & 0xFFFF

    def _outputs(self, time: float) -> None:
        self.drive("A", self.ora, self.ddra, time)
        self.drive("B", self.orb, self.ddrb, time)
        irq = bool(self.ifr & self.ier & 0x7F)
        self.drive("Q", 0, 1 if irq else 0, time)

    def react(self, time: float) -> None:
        if not self.bit("C", 4):
            if self.was("C", 4) or self.ddra or self.ddrb or self.ifr:
                self._clear()
                self._outputs(time + self.data_delay)
            self.drive("D", self.out.get("D", 0), 0, time + 20e-9)
            return
        phi2 = self.bit("C", 3)
        reading = self.selected() and self.bit("C", 2) and phi2
        if self.fell("C", 3):
            if self.was("C", 0) and not self.was("C", 1) and not self.was("C", 2):
                self.write(self.previous.get("R", 0) & 0x0F, self.inputs.get("D", 0))
            self._tick()
            self._outputs(time + self.data_delay)
        if reading:
            if self.rose("C", 3) or self.inputs.get("R") != self.previous.get("R") or \
                    not (self.was("C", 0) and not self.was("C", 1) and self.was("C", 2)):
                self.drive("D", self.read(self.register()), 0xFF, time + self.data_delay)
        else:
            self.drive("D", self.out.get("D", 0), 0, time + 20e-9)


# ---------------------------------------------------------------------------- the display
class HD44780(BusChip):
    """A character display of 16 × 2 (DDRAM of two lines of 40): commands and characters at the falling
    edge of E, the busy flag (never busy) and the address read back; 8- and 4-bit interface.

    Ports: D = DB0-DB7, C = RS, RW, E (inputs). What it shows goes to the serial monitor (a line each
    time the text changed, after a pause in the writes) and to :attr:`state`."""

    DEFAULT_INPUTS = {"C": 0}

    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params)
        self.drive("D", 0, 0, 0.0)
        self._clear_all()

    def _clear_all(self) -> None:
        self.ddram = bytearray(b" " * 128)
        self.address = 0
        self.increment = True
        self.display_on = False
        self.eight_bit = True
        self.nibble: int | None = None
        self.shown = ""
        self.state = "display off"

    def reset(self) -> None:
        super().reset()
        self._clear_all()

    @property
    def display_lines(self) -> list[str]:
        """What it shows: its two lines (blank while the display is off)."""
        return list(self.lines()) if self.display_on else [" " * 16, " " * 16]

    def lines(self) -> tuple[str, str]:
        first = self.ddram[0x00:0x10].decode("latin-1")
        second = self.ddram[0x40:0x50].decode("latin-1")
        return first, second

    def _command(self, value: int) -> None:
        if value & 0x80:
            self.address = value & 0x7F
        elif value & 0x40:
            pass  # (CGRAM: own characters are not shown)
        elif value & 0x20:
            self.eight_bit = bool(value & 0x10)
            self.nibble = None
        elif value & 0x10:
            pass  # (cursor or display shift)
        elif value & 0x08:
            self.display_on = bool(value & 0x04)
        elif value & 0x04:
            self.increment = bool(value & 0x02)
        elif value & 0x02:
            self.address = 0
        elif value & 0x01:
            self.ddram[:] = b" " * 128
            self.address = 0

    def _data(self, value: int) -> None:
        self.ddram[self.address & 0x7F] = value
        self.address = (self.address + (1 if self.increment else -1)) & 0x7F

    #: what it shows goes to the serial monitor once the writes paused this long (of the circuit's time)
    QUIET = 100e-3

    def _show(self, time: float) -> None:
        first, second = self.lines()
        self.state = f"[{first}] [{second}]" if self.display_on else "display off"
        self._written = time

    def run_until(self, seconds: float) -> str:
        super().run_until(seconds)
        written = getattr(self, "_written", None)
        if written is not None and seconds - written > self.QUIET:
            self._written = None
            first, second = (line.rstrip() for line in self.lines())
            text = (f"{first} / {second}" if second else first) if self.display_on else ""
            if text != self.shown:
                self.shown = text
                if text.strip():
                    self.send_text(text + "\n", written)
        return self.state

    def react(self, time: float) -> None:
        rw, enable = self.bit("C", 1), self.bit("C", 2)
        if enable and rw:
            value = 0x00 | (self.address & 0x7F)  # (never busy)
            if not self.eight_bit:
                value = (value if self.nibble is None else (value << 4)) & 0xF0
            self.drive("D", value, 0xFF if self.eight_bit else 0xF0, time + 100e-9)
            return
        self.drive("D", self.out.get("D", 0), 0, time + 20e-9)
        if self.fell("C", 2) and not self.was("C", 1):
            value = self.inputs.get("D", 0)
            rs_was = self.was("C", 0)
            if not self.eight_bit:
                if self.nibble is None:
                    self.nibble = value & 0xF0
                    return
                value = self.nibble | (value >> 4)
                self.nibble = None
            if rs_was:
                self._data(value)
            else:
                self._command(value)
            self._show(time)
        elif self.fell("C", 2) and self.was("C", 1) and not self.eight_bit:
            self.nibble = None if self.nibble is not None else 0


CHIPS = {"w65c02": W65C02, "w65c22": W65C22, "sram62256": SRAM62256, "eeprom28c256": EEPROM28C256,
         "hd44780": HD44780}


def create(core: str, params: dict | None = None) -> BusChip:
    try:
        return CHIPS[core](params)
    except KeyError:
        raise CircuitError(f"no model of the chip {core!r}") from None


__all__ = ["BusChip", "CHIPS", "create", "load_image", "disassemble"]
