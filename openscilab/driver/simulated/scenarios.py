# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""What a simulated instrument simulates: the signals on its channels.

A *scenario* puts signals on the digital channels of a simulator: its own test signals
(``default``), a UART, SPI, I²C, a counter, a Commodore 64 at its expansion port, the channels of
a capture file, or nothing (``idle``). Single channels can carry any source of the circuit
(:data:`~.circuit.SOURCE_TYPES`) on top. The description is plain data, so it is stored with the
device, in a session and in a flow::

    {"scenario": "uart", "text": "Hello\\n", "baud": 9600, "channel": 1,
     "channels": {"D5": {"type": "square", "frequency": "1 kHz"}}}

What the device drives itself (its outputs, a running generator, wires of its profile such as a
loop back or an RC filter) stays as it is.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Optional

from ...core import units
from .facets import DacSource
from .circuit import (
    Alias,
    Button,
    Constant,
    OutputSource,
    RCFilter,
    RemoteSource,
    Replay,
    Source,
    SyncSource,
    WaveSource,
    make_source,
)

#: sources the device or its wiring drives: a scenario does not replace them
DEVICE_DRIVEN = (OutputSource, WaveSource, SyncSource, RemoteSource, DacSource)
WIRED = (Alias, RCFilter, Button)


class ScenarioError(ValueError):
    """The scenario does not fit the device, or a parameter is wrong."""


#: the defaults leave every step of a signal (a count, a clock period, a bit) at least this many
#: samples at the highest rate of the device: what it simulates can be captured
SAMPLES_PER_STEP = 10
#: baud rates a default UART takes (the highest that fits the device)
STANDARD_BAUDS = (300, 1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200)


def _round_down(value: float) -> float:
    """The largest 1, 2 or 5 × 10ⁿ up to ``value``."""
    exponent = math.floor(math.log10(value))
    for mantissa in (5, 2, 1):
        if mantissa * 10 ** exponent <= value * (1 + 1e-9):
            return float(mantissa * 10 ** exponent)
    return float(10 ** exponent)


@dataclass(frozen=True)
class ScenarioParam:
    name: str
    label: str
    default: Any
    #: "text", "quantity" (a number with a unit), "channel" (1 = the first channel) or "path"
    kind: str = "text"
    unit: str = ""
    #: the default follows the highest rate of the device: "rate" (a step rate in Hz) or "baud"
    scaled: str = ""

    def default_for(self, max_rate: float = 0.0) -> Any:
        """The default for a device that samples at most at ``max_rate`` (0: the default as it is):
        no faster than its captures can show (:data:`SAMPLES_PER_STEP`)."""
        if not self.scaled or max_rate <= 0:
            return self.default
        limit = max_rate / SAMPLES_PER_STEP
        if self.scaled == "baud":
            fitting = [baud for baud in STANDARD_BAUDS if baud <= min(limit, float(self.default))]
            return fitting[-1] if fitting else STANDARD_BAUDS[0]
        default = units.parse(self.default, self.unit)
        if limit >= default:
            return self.default
        return units.format_quantity(_round_down(limit), self.unit)


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    description: str
    #: ``build(nets, params)`` -> ({net: source description}, {channel index: suggested name})
    build: Callable[[list[str], dict], tuple[dict, dict]]
    params: tuple[ScenarioParam, ...] = ()
    min_channels: int = 1

    def defaults(self, max_rate: float = 0.0) -> dict[str, Any]:
        """The defaults of its parameters for a device sampling at most at ``max_rate`` (0: as written)."""
        return {param.name: param.default_for(max_rate) for param in self.params}


_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "0": "\0", "\\": "\\"}


def unescape(text: str) -> str:
    """Backslash escapes typed into a text field (n, r, t, 0, a backslash, xNN) as the characters
    they mean; everything else, also non-ASCII letters, stays as it is."""
    result, index = [], 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            following = text[index + 1]
            if following in _ESCAPES:
                result.append(_ESCAPES[following])
                index += 2
                continue
            if following == "x" and index + 3 < len(text):
                try:
                    result.append(chr(int(text[index + 2:index + 4], 16)))
                    index += 4
                    continue
                except ValueError:
                    pass
        result.append(char)
        index += 1
    return "".join(result)


def _first(params: dict, nets: list[str], needed: int, name: str = "channel") -> int:
    """Index of the first channel of a scenario that takes ``needed`` channels from ``channel``."""
    try:
        first = int(params.get(name, 1)) - 1
    except (TypeError, ValueError):
        raise ScenarioError(f"{name}: {params.get(name)!r} is not a channel number") from None
    if first < 0 or first + needed > len(nets):
        raise ScenarioError(f"channel {first + 1} to {first + needed}: the device has {len(nets)} channels")
    return first


def _default(nets: list[str], params: dict) -> tuple[dict, dict]:
    return {}, {}


def _idle(nets: list[str], params: dict) -> tuple[dict, dict]:
    return {}, {}


def _counter(nets: list[str], params: dict) -> tuple[dict, dict]:
    frequency = params.get("frequency", "1 MHz")
    bits = min(8, len(nets))
    sources = {nets[bit]: {"type": "counter", "frequency": frequency, "bit": bit} for bit in range(bits)}
    names = {bit: f"Q{bit}" for bit in range(bits)}
    if len(nets) > bits:
        sources[nets[bits]] = {"type": "clock", "frequency": frequency, "phase": 0.5}
        names[bits] = "CLK"
    return sources, names


def _uart(nets: list[str], params: dict) -> tuple[dict, dict]:
    first = _first(params, nets, 1)
    text = unescape(str(params.get("text", "openSciLab\n")))
    return {nets[first]: {"type": "uart", "text": text, "baud": params.get("baud", 115200)}}, {first: "TX"}


def _spi(nets: list[str], params: dict) -> tuple[dict, dict]:
    first = _first(params, nets, 3)
    frequency = params.get("frequency", "1 MHz")
    lines = ("clk", "mosi", "cs")
    return ({nets[first + offset]: {"type": "spi", "line": line, "frequency": frequency}
             for offset, line in enumerate(lines)},
            {first + offset: line.upper() for offset, line in enumerate(lines)})


def _i2c(nets: list[str], params: dict) -> tuple[dict, dict]:
    first = _first(params, nets, 2)
    frequency = params.get("frequency", "1 MHz")
    lines = ("scl", "sda")
    return ({nets[first + offset]: {"type": "i2c", "line": line, "frequency": frequency}
             for offset, line in enumerate(lines)},
            {first + offset: line.upper() for offset, line in enumerate(lines)})


def _protocols(nets: list[str], params: dict) -> tuple[dict, dict]:
    sources, names = _uart(nets, {"channel": 1, "text": params.get("text", "openSciLab\n"),
                                  "baud": params.get("baud", 115200)})
    for build, first in ((_spi, 2), (_i2c, 5)):
        more, more_names = build(nets, {"channel": first, "frequency": params.get("frequency", "1 MHz")})
        sources.update(more)
        names.update(more_names)
    return sources, names


def _c64(nets: list[str], params: dict) -> tuple[dict, dict]:
    from ...core import c64_model

    sources = {nets[channel]: {"type": "c64", "line": line} for line, channel in c64_model.PROFILE_CHANNELS.items()}
    sources[nets[c64_model.REFERENCE_CHANNEL]] = {"type": "c64", "line": "A0"}
    names = {channel: line for line, channel in c64_model.PROFILE_CHANNELS.items()}
    names[c64_model.REFERENCE_CHANNEL] = "A0 ref"
    return sources, names


#: how a logic analyzer is wired to a 6502 computer: (its pin, the circuit's net or probe). An Arduino
#: Mega after Ben Eater's 6502 monitor (as the profile "C64 expansion port - Arduino Mega 2560"): the
#: address bus on the even pins 22-52 (A0 on 22), the data bus on the odd pins 31-45 (D0 on 31), Φ2 on D2
#: (the clock input of the state mode), R/W on D3, the reset on D53 - and the selects and SYNC on free pins.
WIRINGS: dict[str, tuple[tuple[str, str], ...]] = {
    "Arduino Mega (Ben Eater)": tuple((f"D{22 + 2 * bit}", f"A{bit}") for bit in range(16))
    + tuple((f"D{31 + 2 * bit}", f"D{bit}") for bit in range(8))
    + (("D2", "PHI2"), ("D3", "RW"), ("D53", "RESB"), ("D23", "RAM_CS"), ("D25", "VIA_CS"), ("D27", "ROM_CE"),
       ("D29", "IRQB"), ("D47", "SYNC")),
}


def _circuit(nets: list[str], params: dict) -> tuple[dict, dict]:
    path = str(params.get("path", "")).strip()
    if not path:
        raise ScenarioError("choose the circuit file (a .circuit.yaml)")
    wiring = WIRINGS.get(str(params.get("wiring", "")))
    if wiring is None:
        raise ScenarioError(f"unknown wiring {params.get('wiring')!r} (known: {', '.join(WIRINGS)})")
    missing = [pin for pin, _net in wiring if pin not in nets]
    if missing:
        raise ScenarioError(f"the wiring needs the pins {', '.join(missing[:6])} - an Arduino Mega has them")
    sources = {pin: {"type": "circuit", "path": path, "net": net} for pin, net in wiring}
    names = {nets.index(pin): net for pin, net in wiring}
    return sources, names


def _file(nets: list[str], params: dict) -> tuple[dict, dict]:
    from .circuit import _capture_file

    path = str(params.get("path", "")).strip()
    if not path:
        raise ScenarioError("choose the capture file to play")
    try:
        session = _capture_file(path)
    except (OSError, ValueError, KeyError) as error:
        raise ScenarioError(f"{path} cannot be read: {error}") from None
    sources, names = {}, {}
    for channel in session.capture_channels:
        if channel.samples is not None and 0 <= channel.channel_number < len(nets):
            sources[nets[channel.channel_number]] = {"type": "file", "path": path, "channel": channel.channel_number}
            if channel.channel_name:
                names[channel.channel_number] = channel.channel_name
    if not sources:
        raise ScenarioError(f"{path} has no channel this device has")
    return sources, names


SCENARIOS: dict[str, Scenario] = {scenario.key: scenario for scenario in (
    Scenario("default", "Test signals of the device", "What the profile of the simulator connects: its counter, "
             "clock, UART, … and its wiring.", _default),
    Scenario("uart", "UART", "8N1 frames of a text on one channel, repeated.", _uart, (
        ScenarioParam("text", "Text", "openSciLab\\n"),
        ScenarioParam("baud", "Baud rate", 115200, "quantity", scaled="baud"),
        ScenarioParam("channel", "Channel", 1, "channel"))),
    Scenario("spi", "SPI", "Clock, MOSI and chip select on three channels in a row.", _spi, (
        ScenarioParam("frequency", "Step rate", "1 MHz", "quantity", "Hz", scaled="rate"),
        ScenarioParam("channel", "First channel", 1, "channel")), min_channels=3),
    Scenario("i2c", "I²C", "SCL and SDA on two channels in a row.", _i2c, (
        ScenarioParam("frequency", "Step rate", "1 MHz", "quantity", "Hz", scaled="rate"),
        ScenarioParam("channel", "First channel", 1, "channel")), min_channels=2),
    Scenario("protocols", "UART, SPI and I²C", "UART on channel 1, SPI on 2–4, I²C on 5–6.", _protocols, (
        ScenarioParam("text", "UART text", "openSciLab\\n"),
        ScenarioParam("baud", "Baud rate", 115200, "quantity", scaled="baud"),
        ScenarioParam("frequency", "SPI and I²C step rate", "1 MHz", "quantity", "Hz", scaled="rate")), min_channels=6),
    Scenario("counter", "Counter", "An 8 bit counter on the first channels, its clock on the next one.", _counter, (
        ScenarioParam("frequency", "Count rate", "1 MHz", "quantity", "Hz", scaled="rate"),)),
    Scenario("circuit", "Circuit", "The nets of a circuit while it runs (Circuits), on the pins a logic "
             "analyzer is wired to them with - e.g. the W65C02 breadboard computer at an Arduino Mega as Ben Eater "
             "wires it: the address bus on D22-D52, the data bus on D31-D45, Φ2 on D2, R/W on D3. The device "
             "takes the circuit's time.", _circuit, (
                 ScenarioParam("path", "Circuit file", "", "path"),
                 ScenarioParam("wiring", "Wiring", "Arduino Mega (Ben Eater)")), min_channels=32),
    Scenario("c64", "C64 bus", "A Commodore 64 at its expansion port running a small program (reset, a loop, "
             "two interrupts), on the channels of the profile “C64 expansion port”: control lines on "
             "channels 1–15, address and data bus on 25–48. Needs 48 channels: a multi device of two "
             "simulated boards.", _c64, min_channels=48),
    Scenario("file", "Capture file", "The channels of a capture file (.lac), played at its rate and repeated.",
             _file, (ScenarioParam("path", "File", "", "path"),)),
    Scenario("idle", "Nothing connected", "Every channel is low, except what the board itself has wired "
             "(a button with its pull-up, a wire from an output, an RC low pass).", _idle),
)}


def max_rate(driver) -> float:
    """The highest rate the simulated ``driver`` samples at (0: unknown)."""
    try:
        return float(getattr(driver, "max_frequency", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def normalized(config: Optional[dict], rate: float = 0.0) -> dict:
    """``config`` with its scenario and the defaults of its parameters (``None``: the default), those
    for a device sampling at most at ``rate`` (0: as written)."""
    config = dict(config or {})
    key = config.setdefault("scenario", "default")
    if key not in SCENARIOS:
        raise ScenarioError(f"unknown scenario {key!r} (known: {', '.join(SCENARIOS)})")
    for name, value in SCENARIOS[key].defaults(rate).items():
        config.setdefault(name, value)
    channels = config.get("channels") or {}
    if not isinstance(channels, dict):
        raise ScenarioError("channels: a table of channel -> source is needed")
    config["channels"] = {str(net): dict(source) for net, source in channels.items() if isinstance(source, dict)}
    return config


def available(driver) -> list[tuple[Scenario, str]]:
    """Every scenario with the reason it cannot be used with ``driver`` ("" when it can)."""
    count = len(driver.channel_names())
    return [(scenario, "" if count >= scenario.min_channels else
             f"needs {scenario.min_channels} channels, this device has {count}")
            for scenario in SCENARIOS.values()]


def apply(driver, config: Optional[dict]) -> dict:
    """Put the signals of ``config`` on the channels of the simulated ``driver`` (at once, also
    while it captures). Returns the configuration as applied; raises :class:`ScenarioError`
    and leaves the device unchanged when it does not fit."""
    config = normalized(config, max_rate(driver))
    scenario = SCENARIOS[config["scenario"]]
    nets = driver.channel_names()
    if len(nets) < scenario.min_channels:
        raise ScenarioError(f"{scenario.title} needs {scenario.min_channels} channels, "
                            f"{driver.profile['title']} has {len(nets)}")
    params = {key: value for key, value in config.items() if key not in ("scenario", "channels")}
    descriptions, names = scenario.build(nets, params)
    known = set(nets) | set(driver.analog_channel_names())
    for net in config["channels"]:
        if net not in known:
            raise ScenarioError(f"{driver.profile['title']} has no channel {net}")
    high = float(driver.profile.get("logic_level", 3.3))
    try:
        built: dict[str, Source] = {net: make_source(description, high) for net, description in descriptions.items()}
        single = {net: make_source(description, high) for net, description in config["channels"].items()}
    except (TypeError, ValueError, KeyError) as error:
        raise ScenarioError(str(error)) from None

    circuit = driver.circuit
    try:
        sources = circuit.rebuilt(driver.profile.get("circuit"), getattr(driver, "extra_wiring", None), high=high)
    except ValueError as error:
        raise ScenarioError(str(error)) from None
    if scenario.key != "default":
        for net in nets:
            if not isinstance(sources.get(net), WIRED):
                sources[net] = Constant(0.0)  # only what the scenario says is connected
    sources.update(built)
    sources.update(single)
    # a circuit's nets: the device takes the circuit's time (a capture waits for the simulation, which runs
    # slower than real time when it has much to do)
    followed_run = next((getattr(source, "simulation", None) for source in built.values()
                         if getattr(source, "simulation", None) is not None), None)
    if followed_run is not None:
        driver.clock = followed_run.clock
        driver._drift_origin = driver.clock()
    # nets wired to another simulator stay wired (what drives them now is what they give back to)
    followed = getattr(driver, "followed", {})
    for net, (source, _previous) in list(followed.items()):
        followed[net] = (source, sources.get(net))
        sources[net] = source
    # what the device drives itself right now stays on its nets, above the new signals: an
    # output shows them again when it lets go, a generator gives them back when it stops
    generator = getattr(driver, "generator", None)
    if generator is not None:
        generator.rebase(sources)
    for net, driven in dict(circuit.sources).items():
        if isinstance(driven, (WaveSource, SyncSource)) and driven.end is not None:
            continue  # a generator that stopped: the new signals are what the net carries now
        if isinstance(driven, DEVICE_DRIVEN):
            if isinstance(driven, OutputSource):
                driven.below = sources.get(net)
            sources[net] = driven
    for source in sources.values():
        if hasattr(source, "seed"):
            source.seed = driver.seed
        if isinstance(source, Replay):
            source.high = high
    replaced = [source for source in circuit.sources.values() if getattr(source, "net", None) is not None
                and getattr(source, "owned", False) and source not in sources.values()]
    circuit.sources = sources  # one assignment: a running capture never sees half a table
    if replaced:  # (a circuit's nets no longer on the device: a run started for them stops)
        from ...circuits.source import release_net_source

        for source in replaced:
            release_net_source(source)
    driver.signals = config
    driver.signal_names = dict(names)
    driver.log(f"signals: {describe(config)}")
    return config


def describe(config: Optional[dict]) -> str:
    """A line for people: ``UART (9600 Bd) + 2 channels set by hand``."""
    config = normalized(config)
    scenario = SCENARIOS[config["scenario"]]
    details = ", ".join(f"{param.label.lower()} {config[param.name]}" for param in scenario.params
                        if config.get(param.name) not in ("", None) and param.kind != "path")
    path = next((str(config[param.name]).rsplit("/", 1)[-1] for param in scenario.params
                 if param.kind == "path" and config.get(param.name)), "")
    text = scenario.title + (f" ({path or details})" if path or details else "")
    single = len(config.get("channels") or {})
    return text + (f" + {single} channel{'s' if single != 1 else ''} set by hand" if single else "")
