# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The W65C02S, bus cycle by bus cycle (no Qt, no circuit): :meth:`Core.run` is a generator that yields
every cycle as a :class:`Cycle` and takes the byte the bus gave a read back (``send``)::

    core = Core()
    cycles = core.run()
    cycle = next(cycles)                  # the first cycle of the reset sequence
    while True:
        value = memory[cycle.address] if not cycle.write else None
        if cycle.write:
            memory[cycle.address] = cycle.data
        cycle = cycles.send(value)

Every instruction takes the cycles of the WDC data sheet (W65C02S, table 5-7), its page crossings and
the extra cycle of ADC/SBC in decimal mode included; the reads and writes that carry data are those
of the data sheet, the cycles between them read the next byte of the program (the real chip reads
other addresses there, as the data sheet lists - nothing on the bus depends on it). Interrupts (IRQB,
NMIB), BRK, WAI and STP behave as the data sheet says; RESB starts the reset sequence.
"""

from __future__ import annotations

from dataclasses import dataclass

C, Z, I, D, B, U, V, N = 0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80

NMI_VECTOR, RESET_VECTOR, IRQ_VECTOR = 0xFFFA, 0xFFFC, 0xFFFE


@dataclass(frozen=True)
class Cycle:
    """One bus cycle: what the CPU puts on the bus."""

    address: int
    write: bool = False
    #: the byte written (``None`` for a read)
    data: int | None = None
    #: SYNC: the opcode fetch of an instruction
    sync: bool = False
    #: VPB low: a vector is read
    vector: bool = False
    #: MLB low: a read-modify-write instruction holds the bus
    lock: bool = False


# --------------------------------------------------------------------- the instruction set
# opcode -> (mnemonic, addressing mode, cycles); the modes: imp, acc, imm, zp, zpx, zpy, abs, abx, aby,
# izx (zp,X), izy (zp),Y, izp (zp), ind (abs), iax (abs,X), rel, zpr (zp, relative: BBR/BBS)
OPCODES: dict[int, tuple[str, str, int]] = {}


def _ops(name: str, **modes: int) -> None:
    for mode, (opcode, cycles) in modes.items():
        OPCODES[opcode] = (name, mode, cycles)


for _name, _base in (("ORA", 0x00), ("AND", 0x20), ("EOR", 0x40), ("ADC", 0x60), ("STA", 0x80), ("LDA", 0xA0),
                     ("CMP", 0xC0), ("SBC", 0xE0)):
    _store = _name == "STA"
    _ops(_name, izx=(_base + 0x01, 6), zp=(_base + 0x05, 3), abs=(_base + 0x0D, 4), izy=(_base + 0x11, 6 if _store else 5),
         zpx=(_base + 0x15, 4), aby=(_base + 0x19, 5 if _store else 4), abx=(_base + 0x1D, 5 if _store else 4),
         izp=(_base + 0x12, 5))
    if not _store:
        _ops(_name, imm=(_base + 0x09, 2))
for _name, _base in (("ASL", 0x00), ("ROL", 0x20), ("LSR", 0x40), ("ROR", 0x60)):
    _ops(_name, zp=(_base + 0x06, 5), acc=(_base + 0x0A, 2), abs=(_base + 0x0E, 6), zpx=(_base + 0x16, 6),
         abx=(_base + 0x1E, 6))
_ops("INC", zp=(0xE6, 5), abs=(0xEE, 6), zpx=(0xF6, 6), abx=(0xFE, 7), acc=(0x1A, 2))
_ops("DEC", zp=(0xC6, 5), abs=(0xCE, 6), zpx=(0xD6, 6), abx=(0xDE, 7), acc=(0x3A, 2))
_ops("BIT", zp=(0x24, 3), abs=(0x2C, 4), zpx=(0x34, 4), abx=(0x3C, 4), imm=(0x89, 2))
_ops("TSB", zp=(0x04, 5), abs=(0x0C, 6))
_ops("TRB", zp=(0x14, 5), abs=(0x1C, 6))
_ops("STZ", zp=(0x64, 3), zpx=(0x74, 4), abs=(0x9C, 4), abx=(0x9E, 5))
_ops("STX", zp=(0x86, 3), abs=(0x8E, 4), zpy=(0x96, 4))
_ops("STY", zp=(0x84, 3), abs=(0x8C, 4), zpx=(0x94, 4))
_ops("LDX", imm=(0xA2, 2), zp=(0xA6, 3), abs=(0xAE, 4), zpy=(0xB6, 4), aby=(0xBE, 4))
_ops("LDY", imm=(0xA0, 2), zp=(0xA4, 3), abs=(0xAC, 4), zpx=(0xB4, 4), abx=(0xBC, 4))
_ops("CPX", imm=(0xE0, 2), zp=(0xE4, 3), abs=(0xEC, 4))
_ops("CPY", imm=(0xC0, 2), zp=(0xC4, 3), abs=(0xCC, 4))
_ops("JMP", abs=(0x4C, 3), ind=(0x6C, 6), iax=(0x7C, 6))
_ops("JSR", abs=(0x20, 6))
for _name, _opcode in (("BPL", 0x10), ("BMI", 0x30), ("BVC", 0x50), ("BVS", 0x70), ("BRA", 0x80), ("BCC", 0x90),
                       ("BCS", 0xB0), ("BNE", 0xD0), ("BEQ", 0xF0)):
    _ops(_name, rel=(_opcode, 3 if _name == "BRA" else 2))
for _name, _opcode, _cycles in (("BRK", 0x00, 7), ("RTI", 0x40, 6), ("RTS", 0x60, 6), ("PHP", 0x08, 3), ("PLP", 0x28, 4),
                                ("PHA", 0x48, 3), ("PLA", 0x68, 4), ("PHY", 0x5A, 3), ("PLY", 0x7A, 4), ("PHX", 0xDA, 3),
                                ("PLX", 0xFA, 4), ("CLC", 0x18, 2), ("SEC", 0x38, 2), ("CLI", 0x58, 2), ("SEI", 0x78, 2),
                                ("CLV", 0xB8, 2), ("CLD", 0xD8, 2), ("SED", 0xF8, 2), ("DEY", 0x88, 2), ("INY", 0xC8, 2),
                                ("DEX", 0xCA, 2), ("INX", 0xE8, 2), ("TXA", 0x8A, 2), ("TYA", 0x98, 2), ("TXS", 0x9A, 2),
                                ("TAY", 0xA8, 2), ("TAX", 0xAA, 2), ("TSX", 0xBA, 2), ("NOP", 0xEA, 2), ("WAI", 0xCB, 3),
                                ("STP", 0xDB, 3)):
    _ops(_name, imp=(_opcode, _cycles))
for _bit in range(8):
    _ops(f"RMB{_bit}", zp=(0x07 + 16 * _bit, 5))
    _ops(f"SMB{_bit}", zp=(0x87 + 16 * _bit, 5))
    _ops(f"BBR{_bit}", zpr=(0x0F + 16 * _bit, 5))
    _ops(f"BBS{_bit}", zpr=(0x8F + 16 * _bit, 5))
# the opcodes the W65C02S leaves free are NOPs of 1, 2 or 3 bytes
for _opcode in range(256):
    if _opcode in OPCODES:
        continue
    if _opcode in (0x02, 0x22, 0x42, 0x62, 0x82, 0xC2, 0xE2):
        OPCODES[_opcode] = ("NOP", "imm", 2)
    elif _opcode == 0x44:
        OPCODES[_opcode] = ("NOP", "zp", 3)
    elif _opcode in (0x54, 0xD4, 0xF4):
        OPCODES[_opcode] = ("NOP", "zpx", 4)
    elif _opcode == 0x5C:
        OPCODES[_opcode] = ("NOP", "abs", 8)
    elif _opcode in (0xDC, 0xFC):
        OPCODES[_opcode] = ("NOP", "abs", 4)
    else:
        OPCODES[_opcode] = ("NOP", "imp", 1)

#: bytes of an instruction by addressing mode
LENGTHS = {"imp": 1, "acc": 1, "imm": 2, "zp": 2, "zpx": 2, "zpy": 2, "izx": 2, "izy": 2, "izp": 2, "rel": 2,
           "abs": 3, "abx": 3, "aby": 3, "ind": 3, "iax": 3, "zpr": 3}

_READS = {"ORA", "AND", "EOR", "ADC", "SBC", "CMP", "LDA", "LDX", "LDY", "BIT", "CPX", "CPY", "NOP"}
_RMW = {"ASL", "ROL", "LSR", "ROR", "INC", "DEC", "TSB", "TRB"}
_BRANCHES = {"BPL": (N, False), "BMI": (N, True), "BVC": (V, False), "BVS": (V, True), "BCC": (C, False),
             "BCS": (C, True), "BNE": (Z, False), "BEQ": (Z, True), "BRA": (0, True)}


class Core:
    """The registers and the program of a W65C02S. Inputs (set between cycles): :attr:`irq` (IRQB low),
    :attr:`nmi_edge` (a falling edge of NMIB was seen), :attr:`reset` (RESB low)."""

    def __init__(self) -> None:
        self.a = self.x = self.y = 0
        self.s = 0xFD
        self.p = U | I
        self.pc = 0
        self.irq = False
        self.nmi_edge = False
        self.reset = True
        self.stopped = False
        self.waiting = False
        #: the instruction now running (for people): its address and opcode
        self.instruction = (0, 0xEA)
        self.cycles = 0

    # ------------------------------------------------------------------ flags
    def _nz(self, value: int) -> int:
        value &= 0xFF
        self.p = (self.p & ~(N | Z)) | (value & N) | (0 if value else Z)
        return value

    def _flag(self, flag: int, on) -> None:
        self.p = (self.p | flag) if on else (self.p & ~flag)

    # ------------------------------------------------------------------ arithmetic
    def _adc(self, value: int) -> None:
        carry = self.p & C
        if self.p & D:
            low = (self.a & 0x0F) + (value & 0x0F) + carry
            high = (self.a & 0xF0) + (value & 0xF0)
            binary = (self.a + value + carry) & 0xFF
            self._flag(V, (~(self.a ^ value) & (self.a ^ binary) & 0x80))
            if low > 9:
                low += 6
            if low > 0x0F:
                high += 0x10
            if high > 0x90:
                high += 0x60
            self._flag(C, high > 0xFF)
            self.a = self._nz((high & 0xF0) | (low & 0x0F))
        else:
            total = self.a + value + carry
            self._flag(V, (~(self.a ^ value) & (self.a ^ total) & 0x80))
            self._flag(C, total > 0xFF)
            self.a = self._nz(total)

    def _sbc(self, value: int) -> None:
        if self.p & D:
            borrow = 1 - (self.p & C)
            binary = self.a - value - borrow
            self._flag(V, ((self.a ^ value) & (self.a ^ binary) & 0x80))
            low = (self.a & 0x0F) - (value & 0x0F) - borrow
            high = (self.a >> 4) - (value >> 4)
            if low < 0:
                low += 10
                high -= 1
            if high < 0:
                high += 10
            self._flag(C, binary >= 0)
            self.a = self._nz(((high & 0x0F) << 4) | (low & 0x0F))
        else:
            self._adc(value ^ 0xFF)

    def _compare(self, register: int, value: int) -> None:
        result = register - value
        self._flag(C, result >= 0)
        self._nz(result)

    def _operate(self, name: str, value: int) -> int | None:
        """A read instruction with its operand; returns nothing (registers change)."""
        if name == "ORA":
            self.a = self._nz(self.a | value)
        elif name == "AND":
            self.a = self._nz(self.a & value)
        elif name == "EOR":
            self.a = self._nz(self.a ^ value)
        elif name == "ADC":
            self._adc(value)
        elif name == "SBC":
            self._sbc(value)
        elif name == "CMP":
            self._compare(self.a, value)
        elif name == "CPX":
            self._compare(self.x, value)
        elif name == "CPY":
            self._compare(self.y, value)
        elif name == "LDA":
            self.a = self._nz(value)
        elif name == "LDX":
            self.x = self._nz(value)
        elif name == "LDY":
            self.y = self._nz(value)
        return None

    def _bit(self, value: int, immediate: bool) -> None:
        self._flag(Z, not (self.a & value))
        if not immediate:
            self.p = (self.p & ~(N | V)) | (value & (N | V))

    def _modify(self, name: str, value: int) -> int:
        if name == "ASL":
            self._flag(C, value & 0x80)
            return self._nz(value << 1)
        if name == "LSR":
            self._flag(C, value & 1)
            return self._nz(value >> 1)
        if name == "ROL":
            result = (value << 1) | (self.p & C)
            self._flag(C, value & 0x80)
            return self._nz(result)
        if name == "ROR":
            result = (value >> 1) | ((self.p & C) << 7)
            self._flag(C, value & 1)
            return self._nz(result)
        if name == "INC":
            return self._nz(value + 1)
        if name == "DEC":
            return self._nz(value - 1)
        if name == "TSB":
            self._flag(Z, not (self.a & value))
            return (value | self.a) & 0xFF
        if name == "TRB":
            self._flag(Z, not (self.a & value))
            return (value & ~self.a) & 0xFF
        raise ValueError(name)

    # ------------------------------------------------------------------ the bus
    def run(self):
        """The generator of the bus cycles (see the module)."""
        while True:
            if self.reset:
                yield from self._reset()
                continue
            if self.stopped:
                yield Cycle(self.pc)
                continue
            if self.waiting:
                if not (self.nmi_edge or self.irq):
                    yield Cycle(self.pc)
                    continue
                self.waiting = False
            if self.nmi_edge:
                self.nmi_edge = False
                yield from self._interrupt(NMI_VECTOR)
                continue
            if self.irq and not (self.p & I):
                yield from self._interrupt(IRQ_VECTOR)
                continue
            yield from self._instruction()

    def _push(self, value: int):
        cycle = Cycle(0x0100 | self.s, True, value & 0xFF)
        self.s = (self.s - 1) & 0xFF
        return cycle

    def _reset(self):
        # (RESB low: the bus reads; once high, seven cycles - three "pushes" that only read - and the vector)
        while self.reset:
            yield Cycle(self.pc)
        yield Cycle(self.pc)
        yield Cycle(self.pc)
        for _ in range(3):
            yield Cycle(0x0100 | self.s)
            self.s = (self.s - 1) & 0xFF
        low = yield Cycle(RESET_VECTOR, vector=True)
        high = yield Cycle(RESET_VECTOR + 1, vector=True)
        self.pc = ((high or 0) << 8) | (low or 0)
        self.p = (self.p | I | U) & ~D
        self.stopped = self.waiting = False
        self.nmi_edge = False  # (an edge while it was in reset - the supply coming up - is no interrupt)

    def _interrupt(self, vector: int, brk: bool = False):
        if not brk:
            yield Cycle(self.pc)
            yield Cycle(self.pc)
        yield self._push(self.pc >> 8)
        yield self._push(self.pc)
        yield self._push((self.p | U | B) if brk else ((self.p | U) & ~B))
        self.p = (self.p | I) & ~D
        low = yield Cycle(vector, vector=True)
        high = yield Cycle(vector + 1, vector=True)
        self.pc = ((high or 0) << 8) | (low or 0)

    def _read(self, address: int, lock: bool = False):
        value = yield Cycle(address & 0xFFFF, lock=lock)
        return (value or 0) & 0xFF

    def _instruction(self):
        start = self.pc
        opcode = yield from self._fetch_opcode()
        self.instruction = (start, opcode)
        name, mode, cycles = OPCODES[opcode]
        extra = 0
        used = 1  # (cycles on the bus so far)

        def fetch():
            nonlocal used
            value = yield Cycle(self.pc)
            self.pc = (self.pc + 1) & 0xFFFF
            used += 1
            return (value or 0) & 0xFF

        def pad(until: int):
            nonlocal used
            while used < until:
                yield Cycle(self.pc)
                used += 1

        # ------------------------------------------------------------ the address of the operand
        address = None
        if mode in ("zp", "zpx", "zpy", "izx", "izy", "izp", "zpr"):
            base = yield from fetch()
            if mode == "zp" or mode == "zpr":
                address = base
            elif mode == "zpx":
                address = (base + self.x) & 0xFF
            elif mode == "zpy":
                address = (base + self.y) & 0xFF
            elif mode in ("izx", "izp"):
                pointer = (base + self.x) & 0xFF if mode == "izx" else base
                yield from pad(cycles - 3 if mode == "izx" else used)  # (the index is added)
                low = yield from self._read(pointer)
                high = yield from self._read((pointer + 1) & 0xFF)
                used += 2
                address = (high << 8) | low
            else:  # izy
                low = yield from self._read(base)
                high = yield from self._read((base + 1) & 0xFF)
                used += 2
                pointer = (high << 8) | low
                address = (pointer + self.y) & 0xFFFF
                if (pointer & 0xFF00) != (address & 0xFF00) and name in _READS:
                    extra += 1
        elif mode in ("abs", "abx", "aby", "ind", "iax") and name != "JSR":
            low = yield from fetch()
            high = yield from fetch()
            base = (high << 8) | low
            if mode == "abs":
                address = base
            elif mode in ("abx", "aby", "iax"):
                address = (base + (self.y if mode == "aby" else self.x)) & 0xFFFF
                crossed = (base & 0xFF00) != (address & 0xFF00)
                if crossed and (name in _READS or name in ("ASL", "LSR", "ROL", "ROR")) and name != "NOP":
                    extra += 1
            else:
                address = base
        elif mode == "imm":
            address = self.pc
            self.pc = (self.pc + 1) & 0xFFFF
        total = cycles + extra
        if name in ("ADC", "SBC") and self.p & D:
            total += 1

        # ------------------------------------------------------------ the instruction
        if name in _READS:
            if mode == "imp":
                yield from pad(total)
                return
            if mode == "imm":
                used += 1
                value = yield from self._read(address)
                if name == "BIT":
                    self._bit(value, True)
                elif name != "NOP":
                    self._operate(name, value)
                yield from pad(total)
                return
            yield from pad(total - 1)
            value = yield from self._read(address)
            used += 1
            if name == "BIT":
                self._bit(value, False)
            elif name != "NOP":
                self._operate(name, value)
            yield from pad(total)
            return
        if name in ("STA", "STX", "STY", "STZ"):
            value = {"STA": self.a, "STX": self.x, "STY": self.y, "STZ": 0}[name]
            yield from pad(total - 1)
            yield Cycle(address, True, value)
            return
        if name in _RMW:
            if mode == "acc":
                if name == "INC":
                    self.a = self._nz(self.a + 1)
                elif name == "DEC":
                    self.a = self._nz(self.a - 1)
                else:
                    self.a = self._modify(name, self.a)
                yield from pad(total)
                return
            yield from pad(total - 3)
            value = yield from self._read(address, lock=True)
            yield Cycle(address, lock=True)  # (the 65C02 reads again while it modifies)
            result = self._modify(name, value)
            yield Cycle(address, True, result, lock=True)
            return
        if name.startswith(("RMB", "SMB")):
            bit = int(name[3])
            yield from pad(total - 3)
            value = yield from self._read(address, lock=True)
            yield Cycle(address, lock=True)
            result = value & ~(1 << bit) if name.startswith("RMB") else value | (1 << bit)
            yield Cycle(address, True, result & 0xFF, lock=True)
            return
        if name.startswith(("BBR", "BBS")):
            bit = int(name[3])
            value = yield from self._read(address)
            used += 1
            offset = yield from fetch()
            yield from pad(cycles)
            taken = bool(value & (1 << bit)) == name.startswith("BBS")
            if taken:
                target = (self.pc + (offset - 256 if offset & 0x80 else offset)) & 0xFFFF
                yield Cycle(self.pc)
                if (target & 0xFF00) != (self.pc & 0xFF00):
                    yield Cycle(self.pc)
                self.pc = target
            return
        if name in _BRANCHES:
            offset = yield from fetch()
            flag, wanted = _BRANCHES[name]
            taken = True if name == "BRA" else bool(self.p & flag) == wanted
            if taken:
                target = (self.pc + (offset - 256 if offset & 0x80 else offset)) & 0xFFFF
                yield Cycle(self.pc)
                if (target & 0xFF00) != (self.pc & 0xFF00):
                    yield Cycle(self.pc)
                self.pc = target
            return
        if name == "JMP":
            if mode == "abs":
                self.pc = address
                return
            yield from pad(total - 2)
            low = yield from self._read(address)
            high = yield from self._read((address + 1) & 0xFFFF)
            self.pc = (high << 8) | low
            return
        if name == "JSR":
            low = yield from fetch()
            yield Cycle(0x0100 | self.s)  # (the stack is read while the CPU waits)
            yield self._push(self.pc >> 8)
            yield self._push(self.pc)
            high = yield from self._read(self.pc)
            self.pc = (high << 8) | low
            return
        if name == "RTS":
            yield Cycle(self.pc)
            yield Cycle(0x0100 | self.s)
            self.s = (self.s + 1) & 0xFF
            low = yield from self._read(0x0100 | self.s)
            self.s = (self.s + 1) & 0xFF
            high = yield from self._read(0x0100 | self.s)
            self.pc = (((high << 8) | low) + 1) & 0xFFFF
            yield Cycle(self.pc - 1 & 0xFFFF)
            return
        if name == "RTI":
            yield Cycle(self.pc)
            yield Cycle(0x0100 | self.s)
            self.s = (self.s + 1) & 0xFF
            status = yield from self._read(0x0100 | self.s)
            self.p = (status | U) & ~B
            self.s = (self.s + 1) & 0xFF
            low = yield from self._read(0x0100 | self.s)
            self.s = (self.s + 1) & 0xFF
            high = yield from self._read(0x0100 | self.s)
            self.pc = (high << 8) | low
            return
        if name == "BRK":
            yield Cycle(self.pc)  # (the signature byte)
            self.pc = (self.pc + 1) & 0xFFFF
            yield from self._interrupt(IRQ_VECTOR, brk=True)
            return
        if name in ("PHA", "PHP", "PHX", "PHY"):
            yield Cycle(self.pc)
            value = {"PHA": self.a, "PHX": self.x, "PHY": self.y, "PHP": self.p | B | U}[name]
            yield self._push(value)
            return
        if name in ("PLA", "PLP", "PLX", "PLY"):
            yield Cycle(self.pc)
            yield Cycle(0x0100 | self.s)
            self.s = (self.s + 1) & 0xFF
            value = yield from self._read(0x0100 | self.s)
            if name == "PLP":
                self.p = (value | U) & ~B
            elif name == "PLA":
                self.a = self._nz(value)
            elif name == "PLX":
                self.x = self._nz(value)
            else:
                self.y = self._nz(value)
            return
        # implied, two cycles (WAI, STP three)
        yield from pad(total)
        if name == "CLC":
            self.p &= ~C
        elif name == "SEC":
            self.p |= C
        elif name == "CLI":
            self.p &= ~I
        elif name == "SEI":
            self.p |= I
        elif name == "CLV":
            self.p &= ~V
        elif name == "CLD":
            self.p &= ~D
        elif name == "SED":
            self.p |= D
        elif name == "INX":
            self.x = self._nz(self.x + 1)
        elif name == "INY":
            self.y = self._nz(self.y + 1)
        elif name == "DEX":
            self.x = self._nz(self.x - 1)
        elif name == "DEY":
            self.y = self._nz(self.y - 1)
        elif name == "TAX":
            self.x = self._nz(self.a)
        elif name == "TAY":
            self.y = self._nz(self.a)
        elif name == "TXA":
            self.a = self._nz(self.x)
        elif name == "TYA":
            self.a = self._nz(self.y)
        elif name == "TSX":
            self.x = self._nz(self.s)
        elif name == "TXS":
            self.s = self.x
        elif name == "WAI":
            self.waiting = True
        elif name == "STP":
            self.stopped = True

    def _fetch_opcode(self):
        value = yield Cycle(self.pc, sync=True)
        self.pc = (self.pc + 1) & 0xFFFF
        return (value or 0) & 0xFF


def disassemble(memory, address: int) -> tuple[str, int]:
    """The instruction at ``address`` of ``memory`` (indexable) as text, and its length."""
    opcode = memory[address & 0xFFFF]
    name, mode, _cycles = OPCODES[opcode]
    length = LENGTHS[mode]
    low = memory[(address + 1) & 0xFFFF]
    word = low | (memory[(address + 2) & 0xFFFF] << 8)
    operand = {"imp": "", "acc": "A", "imm": f"#${low:02X}", "zp": f"${low:02X}", "zpx": f"${low:02X},X",
               "zpy": f"${low:02X},Y", "izx": f"(${low:02X},X)", "izy": f"(${low:02X}),Y", "izp": f"(${low:02X})",
               "abs": f"${word:04X}", "abx": f"${word:04X},X", "aby": f"${word:04X},Y", "ind": f"(${word:04X})",
               "iax": f"(${word:04X},X)",
               "rel": f"${(address + 2 + (low - 256 if low & 0x80 else low)) & 0xFFFF:04X}",
               "zpr": ""}[mode]
    if mode == "zpr":
        offset = memory[(address + 2) & 0xFFFF]
        operand = f"${low:02X},${(address + 3 + (offset - 256 if offset & 0x80 else offset)) & 0xFFFF:04X}"
    return (f"{name} {operand}".strip(), length)
