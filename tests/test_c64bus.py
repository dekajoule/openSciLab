"""The c64bus decoder fits the C64 expansion port profile."""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

from openscilab.core.profiles import read_profiles_file
from openscilab.driver.models import AnalyzerChannel, CaptureSession
from openscilab.sigrok.composition import compose
from openscilab.sigrok.provider import DecoderInstance, SigrokProvider

PROJECT_DIRECTORY = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE = os.path.join(PROJECT_DIRECTORY, "examples", "profiles", "c64-logicanalyzer-v2.json")

pytestmark = pytest.mark.skipif(
    not os.path.isfile(os.path.join(PROJECT_DIRECTORY, "decoders", "c64bus", "pd.py")),
    reason="the c64bus decoder is not installed in ./decoders",
)

#: (address, data, R/W, /ROML) per cycle, 20 samples per cycle.
CYCLES = [(0xFFFC, 0xE2, 1, 1), (0x8009, 0x4C, 1, 0), (0xD020, 0x06, 0, 1)]
HALF = 10


@pytest.fixture(scope="module")
def registry():
    from openscilab.sigrok.engine import DecoderRegistry

    registry = DecoderRegistry()
    registry.load()
    return registry


def master_slave_profile():
    return read_profiles_file(PROFILE)[0]


def decode_cycles(registry, options=None, modify=None):
    """Decode ``CYCLES`` with the profile; returns (session, c64bus group, all groups)."""
    profile = master_slave_profile()
    channels = profile.capture_settings.capture_channels
    index = {channel.channel_name: position for position, channel in enumerate(channels)}

    # PHI2 is high in the second half and falls two samples before the bus changes (hold time).
    count = (len(CYCLES) + 1) * 2 * HALF
    samples = {position: np.ones(count, dtype=np.uint8) for position in range(len(channels))}
    samples[index["Φ2 (E)"]][:] = 0
    # The rising edge after the last cycle closes it.
    samples[index["Φ2 (E)"]][len(CYCLES) * 2 * HALF + HALF:] = 1
    for number, (address, data, rw, roml) in enumerate(CYCLES):
        start = number * 2 * HALF
        cycle = slice(start, start + 2 * HALF)
        samples[index["Φ2 (E)"]][start + HALF:start + 2 * HALF - 2] = 1
        samples[index["R/W (5)"]][cycle] = rw
        samples[index["/ROML (11)"]][cycle] = roml
        samples[index["/IRQ (4)"]][cycle] = 0 if number == 1 else 1
        for bit, pin in enumerate("YXWVUTSRPNMLKJHF"):
            samples[index[f"A{bit} ({pin})"]][cycle] = (address >> bit) & 1
        for bit, pin in enumerate((21, 20, 19, 18, 17, 16, 15, 14)):
            samples[index[f"D{bit} ({pin})"]][cycle] = (data >> bit) & 1
    if modify is not None:
        modify(samples, index)

    session = CaptureSession(frequency=profile.capture_settings.frequency, pre_trigger_samples=0,
                             post_trigger_samples=count)
    session.capture_channels = [
        AnalyzerChannel(channel_number=channel.channel_number, channel_name=channel.channel_name,
                        samples=samples[position])
        for position, channel in enumerate(channels)
    ]

    provider = SigrokProvider(registry)
    provider.load_configuration(profile.decoder_configuration)
    instance = next(item for item in provider.instances if item.decoder_id == "c64bus")
    instance.options.update(options or {})
    groups = provider.run(session)
    (group,) = [group for group in groups if group.instance.decoder_id == "c64bus"]
    assert group.error is None, group.error
    return session, group, groups


def segments_of(group, annotation_id):
    names = [annotation[0] for annotation in group.info.annotations]
    return [
        segment
        for row in group.annotations
        for segment in sorted(row.segments, key=lambda item: item.first_sample)
        if segment.type_id == names.index(annotation_id)
    ]


def test_channels_are_assigned_automatically_by_name(registry):
    from openscilab.ui.widgets.decoder_manager import DecoderManager

    profile = master_slave_profile()
    info = registry.get("c64bus")
    instance = DecoderInstance(decoder_id="c64bus")
    fake_manager = SimpleNamespace(model=SimpleNamespace(channels=profile.capture_settings.capture_channels))
    DecoderManager._auto_assign_channels(fake_manager, info, instance)

    # Every decoder channel finds its profile channel, none falls back to "first free" (but SYNC:
    # the 6510 of the C64 has none)
    assert len(instance.channel_map) == len(info.channels) - 1
    assert [channel.id for channel in info.channels if channel.index not in instance.channel_map] == ["sync"]
    stored = next(item for item in profile.decoder_configuration if item["decoder_id"] == "c64bus")
    assert instance.channel_map == {int(key): value for key, value in stored["channel_map"].items()}


def test_capture_channel_0_is_assigned_by_id():
    from openscilab.driver.models import AnalyzerChannel
    from openscilab.ui.widgets.decoder_manager import DecoderManager

    # The id matches capture channel 0, the name matches nothing.
    info = SimpleNamespace(
        channels=[SimpleNamespace(id="cs", name="CS#", index=0)], required_channels=[]
    )
    channels = [AnalyzerChannel(channel_number=0, channel_name="CS")]
    instance = DecoderInstance(decoder_id="test")
    fake_manager = SimpleNamespace(model=SimpleNamespace(channels=channels))
    DecoderManager._auto_assign_channels(fake_manager, info, instance)

    assert instance.channel_map == {0: 0}


def test_bus_cycles_are_decoded_with_the_profile_configuration(registry):
    _session, group, _groups = decode_cycles(registry)

    def values(annotation_id):
        return [segment.values[0] for segment in segments_of(group, annotation_id)]

    assert values("read") == ["R $FFFC = $E2", "R $8009 = $4C"]
    assert values("write") == ["W $D020 = $06"]
    assert values("region") == ["Reset vector", "Cartridge ROM low (/ROML)", "VIC-II"]
    assert values("signal") == ["IRQ active"]
    assert values("unstable") == []
    # Three cycles are no program: the Disassembly row explains why it is empty.
    (hint,) = segments_of(group, "interrupt")
    assert hint.values[0].startswith("No disassembly: 3 bus cycles are too few to follow the program")
    assert (hint.first_sample, hint.last_sample) == (10, 70)


def test_the_bus_is_read_at_the_last_sample_before_the_falling_edge(registry):
    # The cycle runs from the rising PHI2 edge (sample 10) to the next one (30); PHI2 is low
    # from sample 18 on, so the last sample with PHI2 high is 17.
    _session, group, _groups = decode_cycles(registry)
    first_read = segments_of(group, "read")[0]
    assert (first_read.first_sample, first_read.last_sample, first_read.sample_point) == (10, 30, 17)

    _session, group, _groups = decode_cycles(registry, options={"read": "at falling edge"})
    assert segments_of(group, "read")[0].sample_point == 18

    _session, group, _groups = decode_cycles(registry, options={"offset": -3})
    assert segments_of(group, "read")[0].sample_point == 14


def test_a_bus_changing_at_the_read_point_is_flagged(registry):
    def change_a0_at_the_edge(samples, index):
        samples[index["A0 (Y)"]][18:20] = 1

    _session, group, _groups = decode_cycles(registry, modify=change_a0_at_the_edge)

    reads = segments_of(group, "read")
    assert reads[0].values[:2] == ("R $FFFC = $E2 (unstable: A0)", "R $FFFC = $E2?")
    assert reads[1].values[0] == "R $8009 = $4C"
    assert [segment.values[0] for segment in segments_of(group, "unstable")] == [
        "Bus changing at the read point: A0"
    ]


def test_composition_shows_how_address_and_data_are_built(registry):
    session, group, _groups = decode_cycles(registry)
    segment = segments_of(group, "read")[0]

    composition = compose(group.info, group.instance, session.capture_channels, segment)

    buses = {bus.name: bus for bus in composition.buses}
    assert set(buses) == {"A", "D"}  # /IO1 and /IO2 stay single lines
    assert buses["A"].value == 0xFFFC and buses["A"].hexadecimal == "$FFFC"
    assert buses["A"].describe() == "A15…A0 = 1111 1111 1111 1100 = $FFFC"
    assert buses["D"].describe() == "D7…D0 = 1110 0010 = $E2"

    labels = {bit.name: bit.label for bit in composition.bits}
    assert labels["A15"] == "A15 = 1 (+$8000)"
    assert labels["A0"] == "A0 = 0"
    assert labels["R/W"] == "R/W = 1"
    assert labels["Φ2"] == "Φ2 = 1 ~"  # the clock falls right after the read point
    assert "R/W = 1" in composition.describe()
    assert "Changing" not in composition.describe()


def test_hovering_an_annotation_marks_it_in_the_waveform(registry, make_dataview):
    from PySide6.QtCore import QPointF
    from PySide6.QtWidgets import QApplication

    from openscilab.ui.widgets.annotation_viewer import ANNOTATION_HEIGHT

    application = QApplication.instance() or QApplication([])  # noqa: F841
    session, group, groups = decode_cycles(registry)
    window = make_dataview()
    try:
        window.resize(1200, 800)
        window.load_session(session)
        window.model.set_view(0, len(CYCLES) * 2 * HALF + 2 * HALF)
        window.model.set_annotation_groups(groups)
        viewer = window.annotation_viewer
        viewer.resize(1000, viewer.height())

        segment = segments_of(group, "read")[0]
        row = next(index for index, entry in enumerate(viewer._rows()) if segment in entry[2].segments)
        position = QPointF(viewer._x_for(segment.first_sample + 3), row * ANNOTATION_HEIGHT + 5)

        hover = viewer.hover_at(position)
        assert hover is not None and hover.segment is segment
        assert "A15…A0 = 1111 1111 1111 1100 = $FFFC" in viewer.tooltip_text(hover)
        assert "Read at sample 17" in viewer.tooltip_text(hover)

        window.model.set_hover(hover)
        assert window.model.hover is hover
        window.sample_viewer.resize(1000, 2000)
        assert not window.sample_viewer.grab().isNull()
        assert not viewer.grab().isNull()

        assert viewer.hover_at(QPointF(10, 5)) is None  # the name column
        window.model.set_annotation_groups([])
        assert window.model.hover is None
    finally:
        window.close()


def test_an_annotation_row_opens_as_a_list(registry, make_dataview):
    from PySide6.QtCore import QEvent, QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from openscilab.ui.dialogs.annotation_list import TYPE_COLUMN, VALUE_COLUMN
    from openscilab.ui.view_model import AnnotationHover
    from openscilab.ui.widgets.annotation_viewer import ANNOTATION_HEIGHT

    application = QApplication.instance() or QApplication([])
    session, group, groups = decode_cycles(registry)
    window = make_dataview()
    try:
        window.resize(1200, 800)
        window.load_session(session)
        window.model.set_view(0, len(CYCLES) * 2 * HALF + 2 * HALF)
        window.model.set_annotation_groups(groups)
        viewer = window.annotation_viewer
        viewer.resize(1000, viewer.height())

        read = segments_of(group, "read")[0]
        write = segments_of(group, "write")[0]
        rows = viewer._rows()
        row = next(index for index, entry in enumerate(rows) if read in entry[2].segments)
        annotation = rows[row][2]

        # A click on the name column opens the list of the row
        QTest.mouseClick(viewer, Qt.LeftButton, Qt.NoModifier, QPoint(20, row * ANNOTATION_HEIGHT + 5))
        (lister,) = window._annotation_lists.values()
        assert lister.isVisible()
        assert lister.windowTitle() == f"{group.decoder_name}: {annotation.name}"
        model = lister.list_model
        assert model.rowCount() == len(annotation.segments)
        assert model.text(model.row_of(read), TYPE_COLUMN) == "Read cycle"
        assert "FFFC" in model.text(model.row_of(read), VALUE_COLUMN).upper()

        # Selecting an entry marks it in the waveform, hovering the waveform selects the entry
        lister.select_segment(read)
        assert window.model.hover.segment is read
        window.model.set_hover(AnnotationHover(group=group, segment=write))
        assert lister.current_segment() is write

        # Filter and copy
        lister.filter_edit.setText("write cycle")
        assert lister.proxy.rowCount() == 1
        copied = lister.copy_entries().splitlines()
        assert copied[0].split("\t")[0] == "Sample" and len(copied) == 2 and "D020" in copied[1].upper()
        assert application.clipboard().text() == "\n".join(copied)
        assert "D020" in lister.copy_entries(values_only=True).upper()
        lister.filter_edit.clear()

        # A double-click on an annotation reuses the window and selects the annotation
        x = int(viewer._x_for(read.first_sample + 3))
        QTest.mouseDClick(viewer, Qt.LeftButton, Qt.NoModifier, QPoint(x, row * ANNOTATION_HEIGHT + 5))
        assert list(window._annotation_lists.values()) == [lister]
        assert lister.current_segment() is read

        # Decoding again keeps the selection; without results the list is empty
        window.model.set_annotation_groups(groups)
        assert lister.current_segment() is read
        window.model.set_annotation_groups([])
        assert model.rowCount() == 0

        lister.close()
        QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        assert not window._annotation_lists
    finally:
        window.close()


def test_bus_cycles_keep_the_value_in_the_short_texts(registry):
    _session, group, _groups = decode_cycles(registry)
    assert segments_of(group, "read")[0].values == ("R $FFFC = $E2", "R $FFFC=$E2", "FFFC=E2", "E2", "R")
    (write,) = segments_of(group, "write")
    assert write.values == ("W $D020 = $06", "W $D020=$06", "D020=06", "06", "W")


def coarse_timing_capture(cycles: int, program=None, fetches=None):
    """The 6510 model (or ``program``: address, data, read) as an Arduino Mega samples a slow 6502
    (the Mega profile as a timing capture, Φ2 on channel 30): 1 kHz, 24 samples per cycle, the
    address of the next cycle already at the sample of the falling Φ2 edge. With ``fetches`` (the
    cycles of the program that fetch an opcode) SYNC on D23 (channel 1)."""
    from openscilab.core import c64_model

    program = [(0xFFFF, 0xFF, True, False)] * 3 + (c64_model.run_program()[:cycles] if program is None
                                                   else list(program))
    cycle, half = 24, 12
    count = len(program) * cycle
    position = np.arange(count)
    clock = ((position % cycle) >= half).astype(np.uint8)  # each cycle starts with Φ2 falling
    address = np.repeat([item[0] for item in program], cycle)
    read = np.repeat([1 if item[2] else 0 for item in program], cycle).astype(np.uint8)
    data = np.repeat([item[1] for item in program], cycle)
    data = np.concatenate(([0xFF] * half, data[:-half]))  # driven in the high phase, held into the next cycle
    profile = read_profiles_file(os.path.join(PROJECT_DIRECTORY, "examples", "profiles", "c64-arduino-mega.json"))[0]
    channels = []
    for channel in profile.capture_settings.capture_channels:
        name = channel.channel_name
        if name.startswith("A") and name[1].isdigit():
            samples = (address >> int(name[1:name.index(" ")])) & 1
        elif name.startswith("D") and name[1].isdigit():
            samples = (data >> int(name[1:name.index(" ")])) & 1
        else:
            samples = {"R/W (5)": read, "Φ2 (E)": clock}.get(name, np.ones(count))
        channels.append(AnalyzerChannel(channel_number=channel.channel_number, channel_name=name,
                                        samples=np.asarray(samples, dtype=np.uint8)))
    if fetches is not None:  # high from the edge before the fetch cycle to the edge after it, as the CPU drives it
        sync = np.repeat([1 if number - 3 in fetches else 0 for number in range(len(program))], cycle)
        channels.append(AnalyzerChannel(channel_number=1, channel_name="SYNC",
                                        samples=np.concatenate(([0] * half, sync[:-half])).astype(np.uint8)))
    session = CaptureSession(frequency=1000, pre_trigger_samples=0, post_trigger_samples=count)
    session.capture_channels = channels
    return session, profile


def test_a_slow_6502_in_a_coarse_timing_capture_is_disassembled(registry):
    session, profile = coarse_timing_capture(80)
    provider = SigrokProvider(registry)
    provider.load_configuration(profile.decoder_configuration)
    (group,) = provider.run(session)
    rows = {row.name: row.segments for row in group.annotations}
    assert [segment.values[0] for segment in rows["Disassembly"][:3]] == [
        "RESET → $C000", "$C000  CLI", "$C001  LDX #$00"]
    assert not rows.get("Warnings")  # the address changing at the edge sample is the next cycle


def test_too_few_cycles_are_named_as_the_reason(registry):
    session, profile = coarse_timing_capture(2)  # 120 samples at 1 kHz, as the capture of 100 samples
    provider = SigrokProvider(registry)
    provider.load_configuration(profile.decoder_configuration)
    (group,) = provider.run(session)
    (hint,) = next(row.segments for row in group.annotations if row.name == "Disassembly")
    assert hint.values[0].startswith("No disassembly: 4 bus cycles are too few")  # (the first one starts late)


@pytest.mark.parametrize("sync", [False, True])
def test_a_w65c02s_of_ben_eaters_computer(registry, sync):
    """CPU W65C02S and the memory map of Ben Eater's computer; SYNC, when it is assigned, marks the
    opcode fetches (and is assigned by its name)."""
    from test_c64_disassembler import CMOS_PROGRAM, CMOS_TEXTS, FETCHES

    from openscilab.ui.widgets.decoder_manager import refit_channels_by_name

    session, profile = coarse_timing_capture(0, [(a, d, r, False) for a, d, r in CMOS_PROGRAM],
                                             FETCHES if sync else None)
    provider = SigrokProvider(registry)
    provider.load_configuration(profile.decoder_configuration)
    instance = provider.instances[0]
    info = provider.info_for(instance)
    instance.options.update({"cpu": "W65C02S", "map": "Ben Eater 6502"})
    if sync:
        sync_index = next(channel.index for channel in info.channels if channel.id == "sync")
        instance.channel_map[sync_index] = 99  # (a profile of another device) found by its name
        assert refit_channels_by_name(info, instance, session.capture_channels)
        assert instance.channel_map[sync_index] == 1
    (group,) = provider.run(session)
    rows = {row.name: row.segments for row in group.annotations}
    assert [segment.values[0] for segment in rows["Disassembly"]][:len(CMOS_TEXTS)] == CMOS_TEXTS
    regions = [segment.values[1] for segment in rows["Memory region"]]
    assert {"ROM", "Zero page", "Stack", "VIA ORB"} <= set(regions) and "KERNAL" not in regions


def test_ben_eaters_computer_without_the_acia(registry):
    """The map without the ACIA: $4000-$5FFF free, the VIA registers named (mirrored over
    $6000-$7FFF) with what they do on the board."""
    from test_c64_disassembler import CMOS_PROGRAM

    program = [(a, d, r, False) for a, d, r in CMOS_PROGRAM]
    program[12] = (0x6011, 0x42, 1, False)  # (LDA ($20) on a mirror of ORA)
    session, profile = coarse_timing_capture(0, program)
    provider = SigrokProvider(registry)
    provider.load_configuration(profile.decoder_configuration)
    provider.instances[0].options.update({"cpu": "W65C02S", "map": "Ben Eater 6502 without ACIA"})
    (group,) = provider.run(session)
    regions = {segment.values[1]: segment.values[0]
               for row in group.annotations if row.name == "Memory region" for segment in row.segments}
    assert regions["VIA ORA"] == "6522 VIA Output register A: PA5-PA7 LCD RS, R/W, E; PA0-PA3 LEDs D2-D5"
    assert regions["VIA ORB"].endswith("LCD data DB0-DB7")
    assert regions["RES vec"].startswith("Reset vector") and "ACIA" not in regions
