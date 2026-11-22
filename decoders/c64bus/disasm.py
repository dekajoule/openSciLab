##
## This file is part of the openSciLab project.
##
## 6502/6510 and W65C02S disassembler for bus captures, with or without a SYNC signal.
##
## Copyright (C) 2026 Julian Decker
##
## SPDX-License-Identifier: GPL-2.0-or-later
##
## This program is free software: you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published by
## the Free Software Foundation, either version 2 of the License, or
## (at your option) any later version.
##

'''
The 6510 of the C64 has no SYNC output that marks opcode fetches, so the
disassembler follows the program flow through the bus cycles instead:

* it synchronises on a vector fetch (RESET, IRQ, NMI) or on a position where
  three instructions in a row are consistent;
* for every instruction it knows the length and the number of cycles, checks that
  the operand bytes are read from the following addresses and predicts where the
  next opcode is fetched: the next address, the branch/jump/subroutine target,
  the return address pulled from the stack or the interrupt vector;
* IRQ and NMI sequences (two reads of the PC, three pushes, vector fetch) are
  recognised between instructions.

When a prediction fails, it synchronises again. Cycles stolen by the VIC must not
be fed in.

With the SYNC output of a 6502 or 65C02 (high at every opcode fetch) nothing has to
be predicted: every instruction starts at a cycle with SYNC high. The W65C02S
(the CMOS 6502 of Ben Eater's computer, for one) has opcodes of its own where the
NMOS 6502 has its undocumented ones, and other cycle counts for some instructions.
'''

from dataclasses import dataclass

DOCUMENTED = '''
ADC R imm 69 zp 65 zpx 75 abs 6D abx 7D aby 79 izx 61 izy 71
AND R imm 29 zp 25 zpx 35 abs 2D abx 3D aby 39 izx 21 izy 31
ASL M acc 0A zp 06 zpx 16 abs 0E abx 1E
BCC branch rel 90
BCS branch rel B0
BEQ branch rel F0
BMI branch rel 30
BNE branch rel D0
BPL branch rel 10
BVC branch rel 50
BVS branch rel 70
BIT R zp 24 abs 2C
BRK BRK imp 00
CLC imp imp 18
CLD imp imp D8
CLI imp imp 58
CLV imp imp B8
CMP R imm C9 zp C5 zpx D5 abs CD abx DD aby D9 izx C1 izy D1
CPX R imm E0 zp E4 abs EC
CPY R imm C0 zp C4 abs CC
DEC M zp C6 zpx D6 abs CE abx DE
DEX imp imp CA
DEY imp imp 88
EOR R imm 49 zp 45 zpx 55 abs 4D abx 5D aby 59 izx 41 izy 51
INC M zp E6 zpx F6 abs EE abx FE
INX imp imp E8
INY imp imp C8
JMP JMP abs 4C ind 6C
JSR JSR abs 20
LDA R imm A9 zp A5 zpx B5 abs AD abx BD aby B9 izx A1 izy B1
LDX R imm A2 zp A6 zpy B6 abs AE aby BE
LDY R imm A0 zp A4 zpx B4 abs AC abx BC
LSR M acc 4A zp 46 zpx 56 abs 4E abx 5E
NOP imp imp EA
ORA R imm 09 zp 05 zpx 15 abs 0D abx 1D aby 19 izx 01 izy 11
PHA PUSH imp 48
PHP PUSH imp 08
PLA PULL imp 68
PLP PULL imp 28
ROL M acc 2A zp 26 zpx 36 abs 2E abx 3E
ROR M acc 6A zp 66 zpx 76 abs 6E abx 7E
RTI RTI imp 40
RTS RTS imp 60
SBC R imm E9 zp E5 zpx F5 abs ED abx FD aby F9 izx E1 izy F1
SEC imp imp 38
SED imp imp F8
SEI imp imp 78
STA W zp 85 zpx 95 abs 8D abx 9D aby 99 izx 81 izy 91
STX W zp 86 zpy 96 abs 8E
STY W zp 84 zpx 94 abs 8C
TAX imp imp AA
TAY imp imp A8
TSX imp imp BA
TXA imp imp 8A
TXS imp imp 9A
TYA imp imp 98
'''

UNDOCUMENTED = '''
SLO M izx 03 zp 07 abs 0F izy 13 zpx 17 aby 1B abx 1F
RLA M izx 23 zp 27 abs 2F izy 33 zpx 37 aby 3B abx 3F
SRE M izx 43 zp 47 abs 4F izy 53 zpx 57 aby 5B abx 5F
RRA M izx 63 zp 67 abs 6F izy 73 zpx 77 aby 7B abx 7F
SAX W izx 83 zp 87 abs 8F zpy 97
LAX R izx A3 zp A7 abs AF izy B3 zpy B7 aby BF imm AB
DCP M izx C3 zp C7 abs CF izy D3 zpx D7 aby DB abx DF
ISC M izx E3 zp E7 abs EF izy F3 zpx F7 aby FB abx FF
ANC R imm 0B imm 2B
ALR R imm 4B
ARR R imm 6B
ANE R imm 8B
AXS R imm CB
SBC R imm EB
SHA W izy 93 aby 9F
TAS W aby 9B
LAS R aby BB
SHY W abx 9C
SHX W aby 9E
NOP imp imp 1A imp 3A imp 5A imp 7A imp DA imp FA
NOP R imm 80 imm 82 imm 89 imm C2 imm E2 zp 04 zp 44 zp 64 zpx 14 zpx 34 zpx 54 zpx 74 zpx D4 zpx F4 abs 0C abx 1C abx 3C abx 5C abx 7C abx DC abx FC
JAM JAM imp 02 imp 12 imp 22 imp 32 imp 42 imp 52 imp 62 imp 72 imp 92 imp B2 imp D2 imp F2
'''


# The W65C02S: its new instructions (kinds BRA: always taken, BB: bit test and branch, HALT: the
# CPU waits for an interrupt or stops) and the bit instructions of every bit.
CMOS = '''
BRA BRA rel 80
PHX PUSH imp DA
PHY PUSH imp 5A
PLX PULL imp FA
PLY PULL imp 7A
STZ W zp 64 zpx 74 abs 9C abx 9E
TSB M zp 04 abs 0C
TRB M zp 14 abs 1C
INC M acc 1A
DEC M acc 3A
BIT R imm 89 zpx 34 abx 3C
ORA R izp 12
AND R izp 32
EOR R izp 52
ADC R izp 72
STA W izp 92
LDA R izp B2
CMP R izp D2
SBC R izp F2
JMP JMP iax 7C
WAI HALT imp CB
STP HALT imp DB
''' + ''.join('RMB%d M zp %02X\nSMB%d M zp %02X\nBBR%d BB zpr %02X\nBBS%d BB zpr %02X\n'
              % (bit, 0x07 + 16 * bit, bit, 0x87 + 16 * bit, bit, 0x0F + 16 * bit, bit, 0x8F + 16 * bit)
              for bit in range(8))

# The reserved opcodes of the W65C02S are NOPs of their own lengths and cycles.
CMOS_RESERVED = '''
NOP R imm 02 imm 22 imm 42 imm 62 imm 82 imm C2 imm E2 zp 44 zpx 54 zpx D4 zpx F4 abs DC abs FC
NOP NOP8 abs 5C
NOP NOP1 ''' + ' '.join('imp %02X imp %02X' % (0x03 + 16 * row, 0x0B + 16 * row) for row in range(16)
                         if 0x0B + 16 * row not in (0xCB, 0xDB)) + ' imp C3 imp D3'


def _table(blocks):
    table = {}
    for text, undocumented in blocks:
        for line in text.strip().splitlines():
            words = line.split()
            mnemonic, kind = words[0], words[1]
            for mode, opcode in zip(words[2::2], words[3::2]):
                table[int(opcode, 16)] = (mnemonic, mode, kind, undocumented)
    assert len(table) == 256, len(table)
    return table


#: opcode -> (mnemonic, addressing mode, kind, undocumented) of the NMOS 6502/6510
OPCODES = _table(((DOCUMENTED, False), (UNDOCUMENTED, True)))
#: the same of the W65C02S
CMOS_OPCODES = _table(((DOCUMENTED, False), (CMOS, False), (CMOS_RESERVED, True)))

#: The CPUs: the 6510 of the C64 (an NMOS 6502), the CMOS W65C02S
CPU_6510 = '6510 (C64)'
CPU_65C02 = 'W65C02S'

LENGTH = {'imp': 1, 'acc': 1, 'imm': 2, 'zp': 2, 'zpx': 2, 'zpy': 2, 'izx': 2, 'izy': 2, 'izp': 2,
          'rel': 2, 'abs': 3, 'abx': 3, 'aby': 3, 'ind': 3, 'iax': 3, 'zpr': 3}
READ_CYCLES = {'imm': 2, 'zp': 3, 'zpx': 4, 'zpy': 4, 'abs': 4, 'abx': 4, 'aby': 4, 'izx': 6, 'izy': 5,
               'izp': 5}
WRITE_CYCLES = {'zp': 3, 'zpx': 4, 'zpy': 4, 'abs': 4, 'abx': 5, 'aby': 5, 'izx': 6, 'izy': 6, 'izp': 5}
MODIFY_CYCLES = {'acc': 2, 'zp': 5, 'zpx': 6, 'abs': 6, 'abx': 7, 'aby': 7, 'izx': 8, 'izy': 8}
FIXED_CYCLES = {'BRK': 7, 'RTI': 6, 'RTS': 6, 'JSR': 6, 'PUSH': 3, 'PULL': 4, 'branch': 2, 'imp': 2,
                'BRA': 3, 'BB': 5, 'HALT': 3, 'NOP1': 1, 'NOP8': 8}

VECTORS = {0xFFFA: 'NMI', 0xFFFC: 'RESET', 0xFFFE: 'IRQ'}

#: Cycles an instruction can take longer than its base count (page crossing, taken branch).
EXTRA_CYCLES = 2
#: IRQ/NMI sequence: two reads of the PC, three pushes, two vector reads.
INTERRUPT_CYCLES = 7
#: Instructions that have to be consistent before the disassembler synchronises.
SYNC_CHAIN = 3
#: Cycles kept ahead of the position before deciding (the longest chain needs about 60).
LOOKAHEAD = 64

_NEED_MORE = object()


def instruction_cycles(opcode, cpu=CPU_6510):
    '''The cycles of an instruction without page crossing and taken branch.'''
    cmos = cpu == CPU_65C02
    mnemonic, mode, kind, _undocumented = (CMOS_OPCODES if cmos else OPCODES)[opcode]
    if kind == 'JMP':
        # JMP (abs): 5 cycles on the NMOS 6502, 6 on the 65C02 (without the page bug)
        return 3 if mode == 'abs' else 6 if cmos else 5
    if kind == 'R':
        return READ_CYCLES.get(mode, 2)
    if kind == 'W':
        return WRITE_CYCLES.get(mode, 4)
    if kind == 'M':
        # ASL/LSR/ROL/ROR abs,X take 6 cycles on the 65C02 without page crossing; INC/DEC 7
        if cmos and mode == 'abx' and mnemonic not in ('INC', 'DEC'):
            return 6
        return MODIFY_CYCLES.get(mode, 6)
    return FIXED_CYCLES.get(kind, 2)


def operand_text(mode, value, target):
    formats = {
        'imp': '', 'acc': 'A', 'imm': '#$%02X', 'zp': '$%02X', 'zpx': '$%02X,X',
        'zpy': '$%02X,Y', 'izx': '($%02X,X)', 'izy': '($%02X),Y', 'abs': '$%04X',
        'abx': '$%04X,X', 'aby': '$%04X,Y', 'ind': '($%04X)', 'rel': '$%04X',
        'izp': '($%02X)', 'iax': '($%04X,X)',
    }
    if mode == 'zpr':  # BBR/BBS: the zero page address and the branch target
        return '$%02X,$%04X' % (value, target)
    text = formats[mode]
    if '%' not in text:
        return text
    return text % (target if mode == 'rel' else value)


@dataclass
class Cycle:
    ss: int
    es: int
    #: Sample the bus was read from.
    sample: int
    address: int
    data: int
    read: bool
    #: Level of SYNC (high at an opcode fetch); None without the signal
    sync: object = None


@dataclass
class Event:
    kind: str  # 'instruction' or 'interrupt'
    ss: int
    es: int
    sample: int
    address: int
    texts: list


class Disassembler:
    def __init__(self, cpu=CPU_6510):
        self.cpu = cpu
        self.opcodes = CMOS_OPCODES if cpu == CPU_65C02 else OPCODES
        # JMP (abs) reads its target after the operand (the 65C02 one cycle later), JMP (abs,X) too
        self.pointer_cycles = (4, 5) if cpu == CPU_65C02 else (3, 4)
        self.cycles = []
        self.position = 0
        self.synced = False

    # ----------------------------------------------------------------- input
    def feed(self, cycle):
        '''Add a CPU cycle; returns the events that could be resolved.'''
        self.cycles.append(cycle)
        return self._run(final=False)

    def finish(self):
        '''Resolve what the remaining cycles allow at the end of the capture.'''
        return self._run(final=True)

    # ------------------------------------------------------------ processing
    def _run(self, final):
        events = []
        while self.position < len(self.cycles):
            if self.synced:
                outcome = self._decode(self.position, final)
                if outcome is _NEED_MORE:
                    break
                if outcome is None:
                    self.synced = False
                    self.position += 1
                    continue
            else:
                if not final and len(self.cycles) - self.position < LOOKAHEAD:
                    break
                outcome = self._sync(self.position, final)
                if outcome is _NEED_MORE:
                    break
                if outcome is None:
                    self.position += 1
                    continue
                self.synced = True
            new_events, self.position = outcome
            events.extend(new_events)
        self._trim()
        return events

    def _trim(self):
        if self.position > 4 * LOOKAHEAD:
            drop = self.position - LOOKAHEAD
            del self.cycles[:drop]
            self.position -= drop

    def _event(self, kind, first, end, texts):
        start = self.cycles[first]
        return Event(kind, start.ss, self.cycles[end].ss, start.sample, start.address, texts)

    def _sync(self, index, final):
        cycles = self.cycles
        cycle = cycles[index]
        following = cycles[index + 1] if index + 1 < len(cycles) else None
        if (cycle.read and cycle.address in VECTORS and following is not None and following.read
                and following.address == cycle.address + 1):
            vector = cycle.data | (following.data << 8)
            for fetch in range(index + 2, min(index + 5, len(cycles))):
                if cycles[fetch].read and cycles[fetch].address == vector:
                    name = VECTORS[cycle.address]
                    event = self._event('interrupt', index, fetch, ['%s → $%04X' % (name, vector), name])
                    return [event], fetch

        if cycle.sync is not None:  # SYNC marks the opcode fetches: no chain to check
            return ([], index) if cycle.sync else None

        position = index
        for _step in range(SYNC_CHAIN):
            outcome = self._decode(position, final, validate=False)
            if outcome is _NEED_MORE:
                return _NEED_MORE
            if outcome is None:
                return None
            position = outcome[1]
        return [], index

    def _consistent(self, index, steps=2):
        '''Whether ``steps`` instructions starting at ``index`` decode (dry run).'''
        for _step in range(steps):
            outcome = self._decode(index, final=True, validate=False)
            if outcome is None or outcome is _NEED_MORE:
                return False
            index = outcome[1]
        return True

    def _interrupt(self, index, pc):
        '''IRQ/NMI instead of the opcode fetch at ``index``: (event, next index) or None.'''
        cycles = self.cycles
        if index + INTERRUPT_CYCLES >= len(cycles):
            return None
        first, second = cycles[index], cycles[index + 1]
        if not (first.read and second.read and first.address == pc and second.address == pc):
            return None
        for push in cycles[index + 2:index + 5]:
            if push.read or (push.address >> 8) != 0x01:
                return None
        low, high = cycles[index + 5], cycles[index + 6]
        if not (low.read and high.read and low.address in (0xFFFA, 0xFFFE)
                and high.address == low.address + 1):
            return None
        vector = low.data | (high.data << 8)
        fetch = index + INTERRUPT_CYCLES
        if not cycles[fetch].read or cycles[fetch].address != vector:
            return None
        name = VECTORS[low.address]
        return self._event('interrupt', index, fetch, ['%s → $%04X' % (name, vector), name]), fetch

    def _decode(self, index, final, validate=True):
        '''The instruction fetched at ``index``: (events, next index), None or _NEED_MORE.'''
        cycles = self.cycles
        count = len(cycles)
        if index >= count:
            return None if final else _NEED_MORE
        fetch = cycles[index]
        if not fetch.read or fetch.sync is False:
            return None
        mnemonic, mode, kind, undocumented = self.opcodes[fetch.data]
        if kind == 'JAM':
            return None
        base = instruction_cycles(fetch.data, self.cpu)
        if not final and count < index + base + EXTRA_CYCLES + INTERRUPT_CYCLES + 1:
            return _NEED_MORE

        def at(position):
            return cycles[position] if position < count else None

        pc = fetch.address
        length = LENGTH[mode]
        # JSR reads the high byte of its target only after pushing the return address, BBR/BBS the
        # branch offset after reading the zero page byte.
        operand_cycles = [index + 1, index + 5] if kind == 'JSR' else list(range(index + 1, index + length))
        if mode == 'zpr':
            operand_cycles[1] = next((position for position in range(index + 2, index + 5)
                                      if at(position) is not None and at(position).read
                                      and at(position).address == (pc + 2) & 0xFFFF), index + 2)
        operand = []
        for offset, position in enumerate(operand_cycles, start=1):
            cycle = at(position)
            if cycle is None or not cycle.read or cycle.address != (pc + offset) & 0xFFFF:
                return None
            operand.append(cycle.data)
        if mode == 'zpr':  # the zero page address, then the branch offset
            value, offset = operand
        else:
            value = operand[0] | (operand[1] << 8) if length == 3 else (operand[0] if operand else 0)
            offset = value
        sequential = (pc + length) & 0xFFFF

        if kind in ('branch', 'BRA', 'BB'):
            target = (sequential + (offset - 256 if offset & 0x80 else offset)) & 0xFFFF
            targets = [target] if target == sequential or kind == 'BRA' else [target, sequential]
        elif kind in ('JSR', 'JMP') and mode == 'abs':
            targets = [value]
        elif kind in ('JMP', 'RTS', 'RTI', 'BRK'):
            low_index, high_index = {'JMP': self.pointer_cycles, 'RTS': (3, 4), 'RTI': (4, 5), 'BRK': (5, 6)}[kind]
            low, high = at(index + low_index), at(index + high_index)
            if low is None or high is None or not (low.read and high.read):
                return None
            address = low.data | (high.data << 8)
            targets = [(address + 1) & 0xFFFF if kind == 'RTS' else address]
        else:
            target = sequential
            targets = [sequential]

        statement = ('%s %s' % (mnemonic, operand_text(mode, value, targets[0]))).strip()
        full = '$%04X  %s' % (pc, statement) + ('  (undocumented)' if undocumented else '')
        texts = [full, statement, mnemonic]

        if kind == 'HALT':
            # WAI/STP: the CPU waits for an interrupt (or a reset); the next instruction is found anew
            end = min(index + base, count - 1)
            return [self._event('instruction', index, end, texts)], end

        candidates = []
        if fetch.sync is not None:
            # the next opcode fetch is the next cycle with SYNC high; an interrupt in between
            following = next((position for position in range(index + 1, count) if cycles[position].sync), None)
            if following is None:
                return None if final else _NEED_MORE
            for position in range(index + base, following + 1):
                interrupt = self._interrupt(position, cycles[position].address) \
                    if cycles[position].address in targets else None
                if interrupt is not None:
                    candidates.append((position, interrupt))
                    break
            else:
                candidates.append((following, None))
        for target in targets if fetch.sync is None else ():
            for position in range(index + base, index + base + EXTRA_CYCLES + 1):
                cycle = at(position)
                if cycle is None:
                    break
                interrupt = self._interrupt(position, target)
                if interrupt is not None:
                    candidates.append((position, interrupt))
                    break
                if cycle.read and cycle.address == target:
                    candidates.append((position, None))
                    break
        if not candidates:
            return None

        position, interrupt = candidates[0]
        if validate and len(candidates) > 1:
            # A taken branch and the next instruction can look alike (offset +1): follow the
            # program a little further to decide.
            for candidate, candidate_interrupt in candidates:
                if candidate_interrupt is not None or self._consistent(candidate):
                    position, interrupt = candidate, candidate_interrupt
                    break

        events = [self._event('instruction', index, position, texts)]
        if interrupt is not None:
            events.append(interrupt[0])
            return events, interrupt[1]
        return events, position
