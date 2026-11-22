"""Disassembly of 6510 bus cycles without a SYNC signal (decoders/c64bus/disasm.py)."""

from __future__ import annotations

import importlib.util
import os

import pytest

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "decoders", "c64bus", "disasm.py")

pytestmark = pytest.mark.skipif(not os.path.isfile(PATH), reason="the c64bus decoder is not installed")

R, W = True, False

#: Cycle by cycle: reset, a subroutine call, a taken branch and an IRQ with RTI.
PROGRAM = [
    (0xFFFC, 0x00, R), (0xFFFD, 0xC0, R),                                   # RESET vector -> $C000
    (0xC000, 0xA9, R), (0xC001, 0x05, R),                                   # LDA #$05
    (0xC002, 0x20, R), (0xC003, 0x10, R), (0x01FD, 0x00, R),                # JSR $C010
    (0x01FD, 0xC0, W), (0x01FC, 0x04, W), (0xC004, 0xC0, R),
    (0xC010, 0xE8, R), (0xC011, 0x60, R),                                   # INX
    (0xC011, 0x60, R), (0xC012, 0x00, R), (0x01FB, 0x00, R),                # RTS
    (0x01FC, 0x04, R), (0x01FD, 0xC0, R), (0xC004, 0xC0, R),
    (0xC005, 0xD0, R), (0xC006, 0xF9, R), (0xC007, 0x00, R),                # BNE $C000 (taken)
    (0xC000, 0xA9, R), (0xC001, 0x05, R),                                   # LDA #$05
    (0xC002, 0x20, R), (0xC002, 0x20, R), (0x01FD, 0xC0, W),                # IRQ
    (0x01FC, 0x02, W), (0x01FB, 0x20, W), (0xFFFE, 0x48, R), (0xFFFF, 0xFF, R),
    (0xFF48, 0x78, R), (0xFF49, 0x40, R),                                   # SEI
    (0xFF49, 0x40, R), (0xFF4A, 0x00, R), (0x01FA, 0x00, R),                # RTI
    (0x01FB, 0x20, R), (0x01FC, 0x02, R), (0x01FD, 0xC0, R),
    (0xC002, 0x20, R), (0xC003, 0x10, R), (0x01FD, 0x00, R),                # JSR $C010
    (0x01FD, 0xC0, W), (0x01FC, 0x04, W), (0xC004, 0xC0, R),
    (0xC010, 0xE8, R), (0xC011, 0x60, R),                                   # INX
    (0xC011, 0x60, R), (0xC012, 0x00, R), (0x01FB, 0x00, R),                # RTS
    (0x01FC, 0x04, R), (0x01FD, 0xC0, R), (0xC004, 0xC0, R),
    (0xC005, 0xD0, R), (0xC006, 0xF9, R), (0xC007, 0x00, R),                # BNE $C000 (taken)
    (0xC000, 0xA9, R), (0xC001, 0x05, R),                                   # LDA #$05
]


@pytest.fixture(scope="module")
def disasm():
    spec = importlib.util.spec_from_file_location("c64bus_disasm", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(disasm, steps):
    disassembler = disasm.Disassembler()
    events = []
    for number, (address, data, read) in enumerate(steps):
        cycle = disasm.Cycle(ss=number * 10, es=number * 10 + 10, sample=number * 10 + 7,
                             address=address, data=data, read=read)
        events += disassembler.feed(cycle)
    return events + disassembler.finish()


def test_the_opcode_table_is_complete(disasm):
    assert len(disasm.OPCODES) == 256
    assert disasm.OPCODES[0xA9][:3] == ("LDA", "imm", "R")
    assert disasm.OPCODES[0x20][:2] == ("JSR", "abs")
    assert disasm.OPCODES[0xA7] == ("LAX", "zp", "R", True)
    assert disasm.instruction_cycles(0x9D) == 5  # STA abs,X
    assert disasm.instruction_cycles(0xFE) == 7  # INC abs,X


def test_a_program_is_followed_through_calls_branches_and_interrupts(disasm):
    events = run(disasm, PROGRAM)

    assert [event.texts[0] for event in events] == [
        "RESET → $C000",
        "$C000  LDA #$05",
        "$C002  JSR $C010",
        "$C010  INX",
        "$C011  RTS",
        "$C005  BNE $C000",
        "$C000  LDA #$05",
        "IRQ → $FF48",
        "$FF48  SEI",
        "$FF49  RTI",
        "$C002  JSR $C010",
        "$C010  INX",
        "$C011  RTS",
        "$C005  BNE $C000",
    ]
    # An instruction spans from its opcode fetch to the next fetch and keeps the fetch sample.
    jsr = events[2]
    assert (jsr.ss, jsr.es, jsr.sample, jsr.address) == (40, 100, 47, 0xC002)
    assert jsr.texts[1:] == ["JSR $C010", "JSR"]


def test_it_synchronises_in_the_middle_of_a_program(disasm):
    events = run(disasm, PROGRAM[3:])  # starts with the operand of LDA

    texts = [event.texts[0] for event in events]
    assert texts[0] == "$C002  JSR $C010"
    assert "IRQ → $FF48" in texts


def test_undocumented_opcodes_are_marked(disasm):
    steps = [
        (0xFFFC, 0x00, R), (0xFFFD, 0xC0, R),
        (0xC000, 0xA7, R), (0xC001, 0x10, R), (0x0010, 0x55, R),            # LAX $10
        (0xC002, 0xEA, R), (0xC003, 0xEA, R),                               # NOP
        (0xC003, 0xEA, R), (0xC004, 0xEA, R),                               # NOP
        (0xC004, 0xEA, R), (0xC005, 0xEA, R),                               # NOP
        (0xC005, 0xEA, R), (0xC006, 0xEA, R),                               # NOP
    ]
    texts = [event.texts[0] for event in run(disasm, steps)]
    assert texts[:3] == ["RESET → $C000", "$C000  LAX $10  (undocumented)", "$C002  NOP"]


#: A W65C02S program, cycle by cycle (Ben Eater's computer: RAM low, ROM from $8000).
CMOS_PROGRAM = [
    (0xFFFC, 0x00, R), (0xFFFD, 0x80, R),                                   # RESET vector -> $8000
    (0x8000, 0x64, R), (0x8001, 0x10, R), (0x0010, 0x00, W),                # STZ $10
    (0x8002, 0xDA, R), (0x8003, 0xB2, R), (0x01FF, 0x00, W),                # PHX
    (0x8003, 0xB2, R), (0x8004, 0x20, R), (0x0020, 0x00, R),                # LDA ($20)
    (0x0021, 0x60, R), (0x6000, 0x42, R),
    (0x8005, 0x1E, R), (0x8006, 0x00, R), (0x8007, 0x02, R),                # ASL $0200,X (6 cycles)
    (0x0200, 0x01, R), (0x0200, 0x01, R), (0x0200, 0x02, W),
    (0x8008, 0x07, R), (0x8009, 0x10, R), (0x0010, 0x00, R),                # RMB0 $10
    (0x0010, 0x00, R), (0x0010, 0x00, W),
    (0x800A, 0x0F, R), (0x800B, 0x10, R), (0x0010, 0x00, R),                # BBR0 $10,$800F (taken)
    (0x0010, 0x00, R), (0x800C, 0x02, R), (0x800D, 0xEA, R),
    (0x800F, 0x80, R), (0x8010, 0xEF, R), (0x8011, 0x00, R),                # BRA $8000
    (0x8000, 0x64, R), (0x8001, 0x10, R), (0x0010, 0x00, W),                # STZ $10
    (0x8002, 0xDA, R), (0x8003, 0xB2, R), (0x01FE, 0x00, W),                # PHX
    (0x8003, 0xB2, R), (0x8004, 0x20, R), (0x0020, 0x00, R),                # LDA ($20)
    (0x0021, 0x60, R), (0x6000, 0x42, R),
    (0x8005, 0x1E, R), (0x8006, 0x00, R), (0x8007, 0x02, R),                # ASL $0200,X
    (0x0200, 0x02, R), (0x0200, 0x02, R), (0x0200, 0x04, W),
    (0x8008, 0x07, R), (0x8009, 0x10, R),                                   # RMB0 $10
]
CMOS_TEXTS = [
    "RESET → $8000", "$8000  STZ $10", "$8002  PHX", "$8003  LDA ($20)", "$8005  ASL $0200,X",
    "$8008  RMB0 $10", "$800A  BBR0 $10,$800F", "$800F  BRA $8000", "$8000  STZ $10", "$8002  PHX",
    "$8003  LDA ($20)", "$8005  ASL $0200,X",
]
#: The opcode fetches of CMOS_PROGRAM: what SYNC marks
FETCHES = {2, 5, 8, 13, 19, 24, 30, 33, 36, 39, 44, 50}


def run_cpu(disasm, steps, cpu, sync=False):
    disassembler = disasm.Disassembler(cpu)
    events = []
    for number, (address, data, read) in enumerate(steps):
        events += disassembler.feed(disasm.Cycle(ss=number * 10, es=number * 10 + 10, sample=number * 10 + 7,
                                                 address=address, data=data, read=read,
                                                 sync=(number in FETCHES) if sync else None))
    return [event.texts[0] for event in events + disassembler.finish()]


def test_the_w65c02s_has_opcodes_and_cycles_of_its_own(disasm):
    assert len(disasm.CMOS_OPCODES) == 256
    assert disasm.CMOS_OPCODES[0x80][:3] == ("BRA", "rel", "BRA")
    assert disasm.CMOS_OPCODES[0xB2][:2] == ("LDA", "izp")
    assert disasm.CMOS_OPCODES[0x8F][:3] == ("BBS0", "zpr", "BB")
    assert disasm.CMOS_OPCODES[0xC3] == ("NOP", "imp", "NOP1", True)  # (1 byte, 1 cycle)
    cmos = disasm.CPU_65C02
    assert disasm.instruction_cycles(0x1E, cmos) == 6 and disasm.instruction_cycles(0x1E) == 7  # ASL abs,X
    assert disasm.instruction_cycles(0xFE, cmos) == 7  # INC abs,X: 7 on both
    assert disasm.instruction_cycles(0x6C, cmos) == 6 and disasm.instruction_cycles(0x6C) == 5  # JMP (abs)


def test_a_w65c02s_program_is_followed(disasm):
    assert run_cpu(disasm, CMOS_PROGRAM, disasm.CPU_65C02)[:len(CMOS_TEXTS)] == CMOS_TEXTS
    # read as a 6510, STZ is a NOP and PHX takes too long: not the program
    assert run_cpu(disasm, CMOS_PROGRAM, disasm.CPU_6510)[:len(CMOS_TEXTS)] != CMOS_TEXTS


def test_sync_marks_the_opcode_fetches(disasm):
    # in the middle of the program and without three instructions to check: SYNC is enough
    texts = run_cpu(disasm, CMOS_PROGRAM, disasm.CPU_65C02, sync=True)
    assert texts[:len(CMOS_TEXTS)] == CMOS_TEXTS
    middle = CMOS_PROGRAM[30:]
    shifted = {fetch - 30 for fetch in FETCHES if fetch >= 30}
    disassembler = disasm.Disassembler(disasm.CPU_65C02)
    events = []
    for number, (address, data, read) in enumerate(middle):
        events += disassembler.feed(disasm.Cycle(number, number + 1, number, address, data, read,
                                                 sync=number in shifted))
    assert [event.texts[0] for event in events + disassembler.finish()][:2] == ["$800F  BRA $8000", "$8000  STZ $10"]
