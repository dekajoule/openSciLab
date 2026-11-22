# Architecture

This is the map of openSciLab's code: what lives where, how the parts depend on each other, which
rules hold everywhere and where a new thing goes. It is written for people and coding agents who
change the code. *Why* a rule exists is recorded in the [architecture decision records](docs/adr/)
(ADRs); *how* a single part works is in the topic documents under [docs/](docs/).

Read this first, then the ADRs that touch your change. Keep it true: a change that moves a
boundary, adds a package or breaks a rule below updates this file (and an ADR) in the same pull
request.

## Bird's eye view

openSciLab connects **devices** (Pico and Arduino boards, DSLogic, a Rigol oscilloscope, remote
devices, circuits, simulators), lets **flows** capture, process, check and generate signals with
them, shows captures in the **data view**, operates flows from **panels** and writes **reports**.
Everything works with simulated devices first.

```
                        ┌────────────────────────────────────────────────────────┐
  people                │ ui/   Qt (PySide6): shell, documents, widgets, dialogs │
                        └──────────────▲─────────────────────────────────────────┘
                                       │ (only ui/ and app.py import Qt)
  scripts, CLI          ┌──────────────┴───────────┐  ┌────────────────────────────┐
  api.py, cli.py ──────►│ lab/  flows: model, DSL, │  │ circuits/  schematics,     │
                        │ engine (asyncio), nodes  │  │ ngspice, MCU co-simulation │
                        └──────────────┬───────────┘  └─────────────┬──────────────┘
                        ┌──────────────▼────────────────────────────▼──────────────┐
                        │ core/  instruments + facets, hub, signals, samples,      │
                        │ capture files, analysis, timing, preferences             │
                        └──────────────┬───────────────────────────┬───────────────┘
                        ┌──────────────▼───────────────┐  ┌────────▼───────────────┐
                        │ driver/  AnalyzerDriverBase, │  │ sigrok/  decoder       │
                        │ kinds registry, device       │  │ runtime + decode       │
                        │ processes, one folder/device │  │ process; decoders/     │
                        └──────────────┬───────────────┘  └────────────────────────┘
                        ┌──────────────▼───────────────┐
  hardware              │ firmware/  Pico (C), Arduino │   openscilab_device/: the stand-alone
                        │ (PlatformIO), bridge (Android)│   package for remote devices
                        └──────────────────────────────┘
  plugins/  wire it together: each registers kinds of devices, nodes and (in setup_ui) UI
```

## Code map

| Path | What it holds | Start reading at |
| --- | --- | --- |
| `openscilab/app.py`, `__main__.py` | the application's entry point: Qt setup, crash handler, main window | `main()` |
| `openscilab/cli.py` | `openscilab-cli`: devices, capture, decode, run a flow ([docs/cli.md](docs/cli.md)) | `main()` |
| `openscilab/api.py` | the Python scripting interface, Qt free, loads the lab on demand | module docstring |
| `openscilab/core/` | Qt-free domain: `instrument.py` (instruments and facets), `hub.py` (open instruments, trigger routes, time offsets), `signals.py` (typed values between nodes), `sample_store.py` / `shared_arrays.py` (samples, also across processes), `capture_io.py` (file formats), `preferences.py` / `settings.py`, analysis, timing, search, compare | `instrument.py`, `signals.py` |
| `openscilab/driver/` | `base.py` (`AnalyzerDriverBase`, `CAPABILITY_*`, events), `models.py` (`CaptureSession`, channels), `kinds.py` (registry of address kinds), `discovery.py`, `process/` (device processes), one folder per device: `pico/`, `arduino/`, `dslogic/`, `rigoldho/`, `htool/`, `remote/`, `simulated/` | `base.py`, `kinds.py` |
| `openscilab/lab/` | flows: `model.py` (canonical model), `yaml_io.py`, `dsl.py` (Python), `engine/` (asyncio engine, real and virtual time, device opening), `nodes/` (built-in node types by group, `registry.py`), projects, panels, reports | `model.py`, `engine/engine.py` |
| `openscilab/sigrok/` | `runtime.py` (a `sigrokdecode`-compatible runtime on numpy), `engine.py` (decoder discovery), `decode.py` (decoding by the names people use: the API, the CLI and the decode nodes), `worker.py` (decode process), `provider.py` (decoders applied to a capture) | `runtime.py` |
| `decoders/` | protocol decoders, unmodified sigrok `pd.py` packages plus own ones (`c64bus`) | [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) |
| `openscilab/circuits/` | *Circuits*: model and YAML, component library and catalog, netlist (`spice.py`), `engines/` (ngspice via ctypes, in an engine process), `mcu/` (simavr co-simulation) | [docs/circuits.md](docs/circuits.md) |
| `openscilab/plugins/` | the built-in plugins (`BUILT_IN`), the loader for the user's plugins | `__init__.py` |
| `openscilab/ui/` | everything with Qt: `shell/` (`main_window.py` and its parts `window_*.py`, `devices_section.py`, sidebar, command palette, `services.py`), `documents/` (one per document kind, `base.py` = the contract; the data view in `dataview.py` and its parts `dataview_*.py`), `devices/` (device backends and device UI), `flow/`, `panel/`, `circuits/`, `widgets/`, `dialogs/`, `theme.py` (all colours, sizes, fonts) | `documents/base.py`, `shell/services.py`, `theme.py` |
| `openscilab/sdl/` | the signal description language parser | |
| `openscilab_device/` | stand-alone package (stdlib only) to write a remote device ([docs/remote.md](docs/remote.md)) | `device.py` |
| `firmware/` | Pico (C, Pico SDK), Arduino (PlatformIO), the Rigol bridge app (Android) | [firmware/README.md](firmware/README.md) |
| `examples/` | example projects, profiles, example plugins (the reference for plugin authors) | `examples/plugins/` |
| `tests/` | pytest suite (~2000 tests), Qt offscreen, never real hardware | `conftest.py` |
| `tools/` | developer scripts: vendor builds, node docs, demo captures, benchmarks | |
| `packaging/` | PyInstaller builds of the application | |

## Layers and dependency rules

`tests/test_architecture.py` checks these rules on every test run ([ADR 0016](docs/adr/0016-architecture-rules-are-tests.md));
its exception lists (`LAZY_EXCEPTIONS`, `DEVICE_EXCEPTIONS`, `LARGE_MODULES`) name every deviation
with its reason, and an entry that is no longer needed fails too.

*Module level* is an import at the top of a module; a *lazy* import, inside a function, is only run
when the function is. A lazy import may cross a boundary where the dependency is needed now and
then - each one is listed with its reason.

| Package | May import at module level | Never | Lazily, besides |
| --- | --- | --- | --- |
| `core` | `driver.models`, `driver.base`, `driver.software_trigger` (the capture vocabulary), `sigrok`, `sdl` | `ui`, Qt, `app` | loading the plugins |
| `driver` | `core` | `ui`, Qt, `app` | `lab` (a device process), `circuits` (simulators) |
| `sigrok` | `core`, `driver` | `ui`, Qt, `app` | – |
| `sdl` | `core` | `ui`, Qt, `app` | – |
| `lab` | `core`, `driver`, `sigrok`, `sdl` | `app`; `ui` only for the PDF of a report | loading the plugins |
| `circuits` | `core`, `driver` | `ui`, Qt, `app` | `lab` (the project of a circuit) |
| `api` | `core`, `driver`, `sigrok`, `sdl` | `ui`, Qt, `app` | `lab` (on demand) |
| `cli` | `core`, `driver`, `sigrok`, `sdl`, `lab`, `circuits`, `plugins` | `ui`, Qt, `app` | – |
| `plugins` | `core`, `driver`, `sigrok`, `lab`, `circuits` | Qt at module level | `ui` inside `setup_ui()` only |
| `ui` | everything | – | – |
| `openscilab_device` | the standard library (numpy lazily, when it is there) | `openscilab`, Qt | – |

Besides the layers:

* **No import cycles** between modules at module level.
* **Device drivers stay with their device.** `driver/pico`, `arduino`, `dslogic`, `rigoldho` and `htool`
  are used by their own folder, their plugin and `ui/devices/`; everything else asks for facets
  ([ADR 0003](docs/adr/0003-facets-not-device-kinds.md)). `driver/simulated` and `driver/remote` are
  features of the whole application (simulators, remote devices) and may be used where needed.
* **The main window** is imported only by `app.py` and `ui/shell/`. Documents talk to the shell through
  the document contract (`ui/documents/base.py`), plugins through `ui/shell/services.py` and
  extensions ([ADR 0014](docs/adr/0014-plugins-get-a-services-object.md), [0017](docs/adr/0017-large-windows-are-split-into-parts.md)).
* **No module over 1500 lines** (see *Known hotspots*).

## How the main paths run

**A device is opened.** An address `kind:rest` (`pico:/dev/cu.usbmodem1`, `sim:uno`,
`circuit:amp.circuit.yaml`) is looked up in `driver/kinds.py`; the kind's plugin opens a driver or
an instrument. Kinds registered with `process=True` open in a device process (`driver/process/`):
the application gets a proxy with the same interface. `Instrument.from_driver()` wraps the driver
in facets; the instrument goes into the hub (`core/hub.py`). The device list, flows, the CLI and
the API all take this one path ([ADR 0004](docs/adr/0004-every-device-comes-from-a-plugin.md),
[0006](docs/adr/0006-usb-devices-in-a-device-process.md)).

**A capture.** The UI (`ui/devices/capture.py`) or a node builds a `CaptureSession`, the driver's
`start_capture()` runs in its own thread, progress and completion come back as events
(`_raise_capture_progress`, `_raise_capture_completed`). Samples are one `uint8` array per channel,
allocated through `core/sample_store` so they live in shared memory when the driver runs in a
device process. The data view shows the result through `ui/view_model.py`.

**A flow run.** A `Flow` (`lab/model.py`, from YAML, the editor or the DSL) is run by
`lab/engine/engine.py`: values travel along wires into per-node mailboxes (data plane, with back
pressure); timers, sequences and Python nodes are coroutines (control plane). Nodes take their
time from `ctx.now()`/`ctx.sleep()` and call devices through `ctx.device_call()`
(`lab/engine/runtime.py`). In virtual time the clock jumps to the next wake-up as soon as every
node waits ([ADR 0008](docs/adr/0008-the-flow-model-is-canonical.md),
[0009](docs/adr/0009-one-engine-real-and-virtual-time.md)).

**Decoding.** `sigrok/provider.py` hands the samples to `sigrok/worker.py` (a decode process,
samples via shared memory); `sigrok/runtime.py` runs the unmodified sigrok decoders with numpy
([ADR 0007](docs/adr/0007-sigrok-decoders-on-an-own-runtime.md)).

**A circuit runs.** `circuits/spice.py` writes the netlist, `circuits/engines/process.py` runs
ngspice in an engine process, the document reads the history with a timer; the circuit is also a
device (`circuit:<file>`) whose probes are channels ([ADR 0011](docs/adr/0011-native-tools-in-the-vendor-folder.md)).

## Cross-cutting rules

* **Threads and the GIL.** Threads that receive samples must never wait: no Qt, no long work, no
  locks held by the UI in them. Heavy Python work that runs while devices stream goes into a process
  (device process, decode process, engine process). Qt objects are touched only from the GUI thread;
  other threads report through events or queued signals ([docs/timing.md](docs/timing.md), *Staying responsive*).
* **Ask for facets, not kinds.** No `isinstance(driver, PicoDriver)` or `driver_id == "pico"` outside
  the device's own folder; ask `facets_of()`, a facet, or a capability ([ADR 0003](docs/adr/0003-facets-not-device-kinds.md)).
* **One vocabulary for capabilities.** A new capability string goes into [docs/protocols.md](docs/protocols.md)
  first; Python and the firmware headers follow ([ADR 0005](docs/adr/0005-protocols-md-is-the-capability-spec.md)).
* **Errors are reported or logged, never swallowed** ([ADR 0013](docs/adr/0013-no-silent-failures.md)).
  Errors for people: `DeviceConnectionError`, `InstrumentError`, a message under the field, a badge,
  a banner; modal boxes only ask or report what cannot go on ([docs/design.md](docs/design.md), rule 4).
* **Look and words.** Colours, sizes and fonts come from `ui/theme.py`; user-visible texts are
  English and use the glossary of [docs/design.md](docs/design.md) ([ADR 0012](docs/adr/0012-design-rules-enforced-by-tests.md)).
* **Settings.** Application preferences have a default in `core/preferences.py` (a missing or broken
  file gives the defaults); tests run against a temporary settings directory (`tests/conftest.py`).
* **Files.** Flows (`*.flow.yaml`), circuits (`*.circuit.yaml`), projects and captures (`.lac`, `.sr`,
  `.csv`, `.vcd`) are user data: readers tolerate older files, a format change keeps reading the old
  one (fuzzed in `tests/test_fuzz_parsers.py`).
* **Simulator first.** Every function works with a simulator before it works with hardware, and the
  tests never touch real devices ([ADR 0010](docs/adr/0010-simulator-first.md)).

## Where a new thing goes

| You add | Where | Also touch |
| --- | --- | --- |
| a device | `driver/<device>/` + `ui/devices/<device>.py` + `plugins/<device>.py` (name in `BUILT_IN`), or a plugin outside the project | [docs/drivers.md](docs/drivers.md), a simulator profile, fake-transport tests |
| a capability or facet method | [docs/protocols.md](docs/protocols.md) first, then `driver/base.py` / `core/instrument.py`, firmware headers | `driver/simulated/facets.py` (simulators implement every facet) |
| a node type | `lab/nodes/<group>.py` with `@node` | `tools/gen_node_docs.py`, [docs/lab.md](docs/lab.md), `tests/test_node_schema.py` passes |
| a decoder | `decoders/<id>/` (sigrok layout) | `tests/test_packaging.py` checks its imports are packaged |
| a section of the window or a document kind | a plugin with `register_section` / `register_document_type` (`ui/sections.py`) | the document follows `ui/documents/base.py` |
| something that runs while a window is open (a server, a watcher) | an extension: `register_extension` (`ui/sections.py`) in the plugin's `setup_ui()` | [docs/drivers.md](docs/drivers.md#sections-and-documents-of-a-plugin) |
| a feature of the main window or the data view | the part it belongs to (`ui/shell/window_*.py`, `ui/documents/dataview_*.py`), or a new part | [ADR 0017](docs/adr/0017-large-windows-are-split-into-parts.md) |
| a preference | `core/preferences.py` (`DEFAULTS`) | the settings dialog |
| a user-visible word | the glossary in [docs/design.md](docs/design.md) | `tests/test_terminology.py` |
| a colour, size, font | a token in `ui/theme.py` (light and dark value) | `tests/test_theme_tokens.py` |
| a firmware command | firmware + driver + simulator shell (`driver/simulated/arduino_shell.py`, `scpi_shell.py`) together | [docs/protocols.md](docs/protocols.md), protocol version |
| a decision that changes a rule here | a new ADR in [docs/adr/](docs/adr/) | this file |

## Testing

`QT_QPA_PLATFORM=offscreen pytest` runs the suite (a few minutes); `ruff check .` the style. Tests
fake the transport of a device (`tests/test_driver.py` for serial, `tests/test_dslogic_driver.py` for
USB) or use a simulator; `tests/conftest.py` keeps them away from real settings, USB devices and
unanswered message boxes. Some tests guard the architecture itself:

| Test | Guards |
| --- | --- |
| `tests/test_architecture.py` | the layers, lazy imports, device drivers, the main window, import cycles and module sizes (above) |
| `tests/test_startup.py` | core, drivers, lab, sigrok, api and cli load without Qt; the lab loads on demand |
| `tests/test_protocol_spec.py` | `docs/protocols.md` ↔ `driver/base.py` ↔ firmware headers |
| `tests/test_theme_tokens.py` | no literal colours, sizes or fonts outside `ui/theme.py` |
| `tests/test_terminology.py` | user-visible strings use the glossary |
| `tests/test_commands.py` | shortcuts are unique, Run/Pause/Stop belong to the shell |
| `tests/test_plugins.py`, `tests/test_device_backends.py` | a device opens everywhere (list, flow, CLI, API, device process) |
| `tests/test_fuzz_parsers.py` | parsers survive arbitrary input |
| `tests/test_packaging.py` | the packaged build contains what the decoders import |

The soak test (`-m soak`) and the crash hunt run on request (*Actions → Stability*).

## Known hotspots

These modules are large and changed often; they are where regressions and merge conflicts come
from. They may not grow past the limits in `LARGE_MODULES` of `tests/test_architecture.py`: put new
behaviour into a module of its own next to them, and split them when you work on them anyway -
as `ui/shell/main_window.py` and `ui/documents/dataview.py` were (about 2900 lines each before,
[ADR 0017](docs/adr/0017-large-windows-are-split-into-parts.md)).

| Module | Lines (Nov 2026) |
| --- | --- |
| `ui/documents/device.py` | ~2000 |
| `ui/dialogs/capture_dialog.py` | ~1700 |
| `driver/pico/analyzer.py` | ~1700 |
| `ui/circuits/document.py` | ~1700 |
| `ui/documents/panel.py`, `ui/documents/flow.py` | ~1550 each |

## Further reading

[docs/drivers.md](docs/drivers.md) (drivers, facets, plugins) · [docs/protocols.md](docs/protocols.md)
(capabilities, firmware protocols) · [docs/lab.md](docs/lab.md) (flows, nodes, panels) ·
[docs/timing.md](docs/timing.md) (time of samples, responsiveness) · [docs/simulator.md](docs/simulator.md) ·
[docs/circuits.md](docs/circuits.md) · [docs/remote.md](docs/remote.md) · [docs/cli.md](docs/cli.md) ·
[docs/design.md](docs/design.md) (look and words) · [docs/adr/](docs/adr/) (decisions)
