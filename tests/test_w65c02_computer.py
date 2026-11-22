"""The 6502 computer of the circuits (``circuits/mcu/cpu65c02.py``, ``bus_chips.py``): the W65C02S bus cycle by
bus cycle, the chips on a bus of their own (without ngspice), EEPROM images, the nets the co-simulation
carries, and the wiring of a simulated logic analyzer at it. The whole computer in a circuit runs in
``test_examples.py`` (14-circuits/07-w65c02-computer: its flows trace and decode the bus)."""

from __future__ import annotations

import os

import pytest

from openscilab.circuits.mcu import bus_chips
from openscilab.circuits.mcu.avr import EVENT_UART
from openscilab.circuits.mcu.boards import BOARDS, pin_of
from openscilab.circuits.mcu.cpu65c02 import OPCODES, Core, disassemble

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLE = os.path.join(ROOT, "examples", "library", "14-circuits", "07-w65c02-computer")


# ------------------------------------------------------------------ the core
def run(program: dict[int, list[int]], until_pc: int, start: int = 0x0200, steps: int = 10_000, setup=None):
    """Run ``program`` (address -> bytes) from ``start`` until the opcode fetch at ``until_pc``; returns the
    core, the memory and the bus cycles (address, write, data)."""
    memory = bytearray(65536)
    for address, data in program.items():
        memory[address:address + len(data)] = bytes(data)
    core = Core()
    core.reset = False
    core.pc = start
    if setup:
        setup(core)
    cycles = core.run()
    cycle = next(cycles)
    bus = []
    for _ in range(steps):
        if cycle.sync and cycle.address == until_pc:
            return core, memory, bus
        bus.append((cycle.address, cycle.write, cycle.data if cycle.write else memory[cycle.address]))
        if cycle.write:
            memory[cycle.address] = cycle.data
            cycle = cycles.send(None)
        else:
            cycle = cycles.send(memory[cycle.address])
    raise AssertionError("the program did not get there")


def cycles_of(program: list[int], setup=None) -> int:
    _core, _memory, bus = run({0x0200: program + [0xDB]}, 0x0200 + len(program), setup=setup)
    return len(bus)


def test_the_instruction_set_is_complete():
    assert len(OPCODES) == 256
    assert OPCODES[0xA9] == ("LDA", "imm", 2) and OPCODES[0x7C] == ("JMP", "iax", 6)
    assert OPCODES[0x0F][0] == "BBR0" and OPCODES[0xCB][0] == "WAI" and OPCODES[0x03] == ("NOP", "imp", 1)


@pytest.mark.parametrize("program, cycles, setup", [
    ([0xA9, 0x01], 2, None),                       # LDA #
    ([0xBD, 0xFF, 0x12], 5, lambda c: setattr(c, "x", 1)),  # LDA abs,X across a page: one more
    ([0xBD, 0x00, 0x12], 4, lambda c: setattr(c, "x", 1)),
    ([0x91, 0x10], 6, None),                       # STA (zp),Y
    ([0xFE, 0x00, 0x12], 7, None),                 # INC abs,X
    ([0x80, 0x00], 3, None),                       # BRA
    ([0x69, 0x01], 3, lambda c: setattr(c, "p", c.p | 0x08)),  # ADC # in decimal mode: one more
    ([0x5C, 0x00, 0x00], 8, None),                 # the free opcode $5C: 3 bytes, 8 cycles
])
def test_cycles_are_those_of_the_data_sheet(program, cycles, setup):
    assert cycles_of(program, setup) == cycles


def test_a_program_with_subroutine_stack_and_decimal_mode():
    program = {
        0x0200: [0xA2, 0xFF, 0x9A,          # LDX #$FF, TXS
                 0xF8, 0x18, 0xA9, 0x19,    # SED, CLC, LDA #$19
                 0x20, 0x00, 0x03,          # JSR $0300
                 0xD8, 0x85, 0x10,          # CLD, STA $10
                 0x07, 0x10,                # RMB0 $10
                 0x9F, 0x10, 0x02,          # BBS1 $10, +2 (bit 1 of $46 is set: taken)
                 0xA9, 0xEE,                # (skipped)
                 0x64, 0x11,                # STZ $11
                 0xDB],                     # STP
        0x0300: [0x69, 0x28, 0x60],         # ADC #$28 (decimal), RTS
    }
    core, memory, bus = run(program, 0x0216)
    assert core.a == 0x47 and memory[0x10] == 0x46  # 19 + 28 = 47 in BCD, then bit 0 reset
    assert memory[0x01FF] == 0x02 and memory[0x01FE] == 0x09  # the return address JSR pushed
    assert core.s == 0xFF
    assert disassemble(memory, 0x0207) == ("JSR $0300", 3)
    assert disassemble(memory, 0x020F) == ("BBS1 $10,$0214", 3)


def test_interrupts_brk_and_wai():
    program = {0x0200: [0xA2, 0xFF, 0x9A, 0x58, 0xCB, 0xEA, 0x00, 0xEA, 0xDB],  # CLI, WAI, NOP, BRK
               0x0400: [0x48, 0x68, 0x40],                                       # handler: PHA, PLA, RTI
               0xFFFE: [0x00, 0x04]}
    memory = bytearray(65536)
    for address, data in program.items():
        memory[address:address + len(data)] = bytes(data)
    core = Core()
    core.reset = False
    core.pc = 0x0200
    cycles = core.run()
    cycle = next(cycles)
    fetched = []
    for count in range(200):
        if cycle.sync:
            fetched.append(cycle.address)
        if count == 30:
            core.irq = True  # (IRQB low while it waits)
        if cycle.sync and cycle.address == 0x0400:
            core.irq = False  # (the handler takes it back)
        if cycle.write:
            memory[cycle.address] = cycle.data
            cycle = cycles.send(None)
        else:
            cycle = cycles.send(memory[cycle.address])
        if core.stopped:
            break
    # WAI waits, the IRQ runs the handler, RTI returns after WAI; BRK runs it again with B set
    assert fetched[:4] == [0x0200, 0x0202, 0x0203, 0x0204]
    assert 0x0400 in fetched and fetched.count(0x0400) == 2 and fetched[-1] == 0x0208
    assert memory[0x01FD] & 0x10  # (the status BRK pushed: B set)


# ------------------------------------------------------------------ the chips on a bus
def wire(chip: bus_chips.BusChip, board: str, pins: dict[str, str]) -> list[tuple[str, int, str]]:
    found = []
    for name, net in pins.items():
        pin = pin_of(BOARDS[board], name)
        assert pin is not None, name
        found.append((pin.port, pin.bit, net))
    return found


class Bus:
    """The computer's chips with its decoder (74HC00) as plain logic: enough to see they work together."""

    def __init__(self, image: str):
        address = {f"A{n}": f"A{n}" for n in range(16)}
        data = {f"D{n}": f"D{n}" for n in range(8)}
        memory = {**{f"A{n}": f"A{n}" for n in range(15)}, **data}
        self.chips = {
            "cpu": (bus_chips.W65C02(), wire(bus_chips.W65C02(), "chip.w65c02", {
                **address, **data, "RWB": "RW", "PHI2": "PHI2", "RESB": "RES", "IRQB": "IRQ", "NMIB": "NMI",
                "RDY": "VCC", "BE": "VCC", "SOB": "VCC"})),
            "ram": (bus_chips.SRAM62256(), wire(bus_chips.SRAM62256(), "chip.sram62256", {
                **memory, "A14": "A14", "CE": "RAM_CS", "OE": "A14", "WE": "RW"})),
            "rom": (bus_chips.EEPROM28C256(), wire(bus_chips.EEPROM28C256(), "chip.eeprom28c256", {
                **memory, "CE": "ROM_CE", "OE": "GND", "WE": "VCC"})),
            "via": (bus_chips.W65C22(), wire(bus_chips.W65C22(), "chip.w65c22", {
                **data, **{f"RS{n}": f"A{n}" for n in range(4)}, "CS1": "A13", "CS2B": "VIA_CS", "RWB": "RW",
                "PHI2": "PHI2", "RESB": "RES", "CA1": "VCC", "CB1": "VCC",
                **{f"PA{n}": f"PA{n}" for n in range(8)}, **{f"PB{n}": f"PB{n}" for n in range(8)}})),
            "lcd": (bus_chips.HD44780(), wire(bus_chips.HD44780(), "chip.hd44780", {
                **{f"DB{n}": f"PB{n}" for n in range(8)}, "RS": "PA5", "RW": "PA6", "E": "PA7"})),
        }
        self.chips["rom"][0].load_firmware(image)
        self.nets = {"VCC": 1, "GND": 0, "RES": 1, "IRQ": 1, "NMI": 1, "PHI2": 0}
        self.time = 0.0
        self.text = b""

    def settle(self):
        for _ in range(6):
            nets = self.nets
            nets["ROM_CE"] = 1 - nets.get("A15", 0)
            nets["RAM_CS"] = 1 - (nets["PHI2"] & (1 - nets.get("A15", 0)))
            nets["VIA_CS"] = 1 - ((1 - nets.get("A15", 0)) & nets.get("A14", 0))
            for chip, pins in self.chips.values():
                for port, bit, net in pins:
                    if not chip.ddr.get(port, 0) >> bit & 1:
                        chip.set_input_at(self.time, port, bit, bool(nets.get(net, 0)))
                chip.update(self.time)
                chip.run_until(self.time)
                for event in chip.events():
                    if event.kind == EVENT_UART:
                        self.text += bytes([event.value])
            for chip, pins in self.chips.values():
                for port, bit, net in pins:
                    if chip.ddr.get(port, 0) >> bit & 1:
                        nets[net] = chip.out.get(port, 0) >> bit & 1

    def run(self, cycles: int, period: float = 1e-4):
        for _ in range(cycles):
            for level in (1, 0):
                self.time += period / 2
                self.nets["PHI2"] = level
                self.settle()


def test_the_chips_run_hello_world_and_the_leds(tmp_path):
    bus = Bus(os.path.join(EXAMPLE, "firmware", "hello.bin"))
    bus.run(4000)
    cpu, lcd, via = bus.chips["cpu"][0], bus.chips["lcd"][0], bus.chips["via"][0]
    assert lcd.lines() == ("Hello, world!   ", " " * 16)
    assert lcd.display_lines == ["Hello, world!   ", " " * 16]  # (what its window on the schematic shows)
    assert b"Hello, world!" in bus.text  # (what the serial monitor shows)
    assert via.ddra == 0xEF and via.ddrb == 0xFF
    assert 0x803A <= cpu.core.instruction[0] < 0x8050  # (counting the LEDs)
    before = via.ora & 0x0F
    bus.run(200)
    assert via.ora & 0x0F != before  # (they count)


def test_the_via_timer_interrupts():
    via = bus_chips.W65C22()
    via.write(11, 0x40)  # ACR: T1 free running
    via.write(14, 0xC0)  # IER: T1
    via.write(4, 3)
    via.write(5, 0)      # start with 3
    for _ in range(4):
        via._tick()
    assert via.ifr & 0x40 and via.read(13) & 0x80
    via.read(4)          # (reading T1C-L takes the flag back)
    assert not via.ifr & 0x40


def test_eeprom_images_binary_and_intel_hex(tmp_path):
    binary = tmp_path / "rom.bin"
    binary.write_bytes(bytes(range(256)) * 128)
    assert bus_chips.load_image(str(binary), 32768)[0x7FFF] == 0xFF
    whole = tmp_path / "whole.bin"  # (an image of the whole address space: its upper half)
    whole.write_bytes(bytes(32768) + bytes([0xEA]) * 32768)
    assert bus_chips.load_image(str(whole), 32768)[0] == 0xEA
    image = bus_chips.load_image(os.path.join(EXAMPLE, "firmware", "hello.hex"), 32768)
    assert image[:3] == bytes([0xA2, 0xFF, 0x9A]) and image[0x7FFC:0x7FFE] == bytes([0x00, 0x80])
    binary_image = bus_chips.load_image(os.path.join(EXAMPLE, "firmware", "hello.bin"), 32768)
    assert image[:0x80] == binary_image[:0x80]  # (the same program; the unused bytes: $FF, the binary's $00)
    broken = tmp_path / "broken.hex"
    broken.write_text(":0100000041FF\n")
    with pytest.raises(Exception):
        bus_chips.load_image(str(broken), 32768)


# ------------------------------------------------------------------ in a circuit
def test_the_co_simulation_carries_the_nets_only_chips_connect():
    from openscilab.circuits import spice, yaml_io
    from openscilab.circuits.catalog import default_catalog

    path = os.path.join(EXAMPLE, "circuits", "computer.circuit.yaml")
    schematic = yaml_io.load(path)
    catalog = default_catalog()
    circuit = spice.emit(schematic, catalog.spec, catalog.models, base_folder=os.path.dirname(path))
    assert not circuit.errors
    def net(ref, pin):  # (the schematic has wires only: nets by their pins)
        return circuit.netlist.net_of(ref, pin)

    # D0, D7, A0, A12, A13, R/W of the CPU, PB0 of the VIA: only chips (and probes)
    assert {net("U1", "33"), net("U1", "26"), net("U1", "9"), net("U1", "22"), net("U1", "23"), net("U1", "34"),
            net("U4", "10")} <= circuit.digital_nets
    # A14, A15 (into the decoder's gates), PHI2, the RAM's /CE (a gate's output), PA0 (an LED): ngspice
    assert not {net("U1", "24"), net("U1", "25"), net("U1", "37"), net("U2", "20"), net("U4", "2")} & \
        circuit.digital_nets
    infos = {info.ref: info for info in circuit.mcus}
    assert set(infos) == {"U1", "U2", "U3", "U4", "LCD1"} and all(info.emulator == "chip" for info in infos.values())
    assert infos["U3"].firmware.endswith("hello.bin") and not infos["U2"].needs_firmware
    # one external source per port that drives a net of ngspice (none per pin), none for D0-D7
    externals = [line for line in circuit.lines if line.endswith(" external")]
    assert len(externals) <= 8 and not any("_pd_" in line for line in externals if line.startswith("vu1"))


def test_a_simulated_logic_analyzer_is_wired_as_ben_eaters_arduino_mega():
    from openscilab.driver.simulated import open_simulated, scenarios

    wiring = dict(scenarios.WIRINGS["Arduino Mega (Ben Eater)"])
    assert wiring["D22"] == "A0" and wiring["D52"] == "A15" and wiring["D31"] == "D0" and wiring["D45"] == "D7"
    assert wiring["D2"] == "PHI2" and wiring["D3"] == "RW"
    assert len(set(wiring.values())) == len(wiring)
    driver = open_simulated("mega").simulated_driver
    with pytest.raises(scenarios.ScenarioError):
        scenarios.apply(driver, {"scenario": "circuit"})  # (no file)
    small = open_simulated("uno").simulated_driver
    with pytest.raises(scenarios.ScenarioError):
        scenarios.apply(small, {"scenario": "circuit", "path": "x.circuit.yaml"})  # (too few pins)


def test_a_simulated_arduino_mega_traces_the_running_computer():
    from openscilab.circuits import engines, running
    from openscilab.driver.simulated import open_simulated, scenarios

    if not engines.available():
        pytest.skip("ngspice is not installed")
    driver = open_simulated("mega").simulated_driver
    path = os.path.join(EXAMPLE, "circuits", "computer.circuit.yaml")
    scenarios.apply(driver, {"scenario": "circuit", "path": path})
    try:
        assert driver.signal_names[driver.channel_names().index("D22")] == "A0"
        sources = driver.circuit.sources
        rate = 1e6
        phi2 = sources["D2"].digital(0.0, rate, 1000)  # (the first millisecond: waits for the circuit)
        falls = [i for i in range(1, len(phi2)) if phi2[i - 1] and not phi2[i]]
        assert len(falls) >= 8  # (10 kHz)

        def bus(sample: int, pins: list[str]) -> int:
            return sum(int(sources[pin].digital(sample / rate, rate, 1)[0]) << bit for bit, pin in enumerate(pins))

        cycles = [(bus(i - 2, [f"D{22 + 2 * n}" for n in range(16)]), bus(i - 2, [f"D{31 + 2 * n}" for n in range(8)]))
                  for i in falls]
        assert (0xFFFC, 0x00) in cycles and (0xFFFD, 0x80) in cycles  # the reset vector: $8000
        assert (0x8000, 0xA2) in cycles  # LDX #$FF, the first instruction of hello.bin
        run = sources["D2"].simulation
    finally:
        scenarios.apply(driver, {"scenario": "idle"})
    assert running.find(path) is None and run.state != "running"  # (the run started for it stopped)
