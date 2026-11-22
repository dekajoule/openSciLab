# Working on openSciLab (for coding agents and contributors)

Read [ARCHITECTURE.md](ARCHITECTURE.md) before changing code, and the [ADRs](docs/adr/) that touch
your change. The topic documents in [docs/](docs/) explain the parts; [CONTRIBUTING.md](CONTRIBUTING.md)
the process.

## Commands

```bash
pip install -e ".[dev]"                       # in .venv
QT_QPA_PLATFORM=offscreen pytest -q           # whole suite, a few minutes
QT_QPA_PLATFORM=offscreen pytest tests/test_x.py -q   # while iterating
ruff check .                                  # style (line length 100)
```

Run the tests that cover what you changed while iterating, and the whole suite before you call a
change done. Report failing tests as they are; do not weaken or delete a test to make it pass.

## Rules that are easy to break

1. **No Qt outside `openscilab/ui/`** (and `app.py`). Plugins import UI inside `setup_ui()` only.
   `tests/test_startup.py` fails otherwise. [ADR 0002](docs/adr/0002-qt-only-in-the-ui-layer.md)
2. **Ask for facets or capabilities, never for the kind of device** (`isinstance(driver, PicoDriver)`,
   `driver_id == ...` outside the device's own folder). [ADR 0003](docs/adr/0003-facets-not-device-kinds.md)
3. **A new capability or firmware command starts in [docs/protocols.md](docs/protocols.md)**, then
   Python, firmware headers and the simulator in the same change. [ADR 0005](docs/adr/0005-protocols-md-is-the-capability-spec.md)
4. **Never block a thread that receives samples**; heavy Python work next to streaming belongs in a
   process. Samples are allocated through `core/sample_store`. [ADR 0006](docs/adr/0006-usb-devices-in-a-device-process.md)
5. **Flow nodes use the flow's time** (`ctx.now()`, `ctx.sleep()`) and call devices through
   `ctx.device_call()`; virtual time must stay deterministic. [ADR 0009](docs/adr/0009-one-engine-real-and-virtual-time.md)
6. **Simulator first; tests never touch hardware.** Fake the transport or use a simulator.
   [ADR 0010](docs/adr/0010-simulator-first.md)
7. **No literal colours, fonts or sizes outside `ui/theme.py`; UI texts in English with the words of
   the glossary** ([docs/design.md](docs/design.md)). `tests/test_theme_tokens.py`,
   `tests/test_terminology.py`. [ADR 0012](docs/adr/0012-design-rules-enforced-by-tests.md)
8. **No silent `except`**: report the error or log it (`log.debug(..., exc_info=True)` at least).
   [ADR 0013](docs/adr/0013-no-silent-failures.md)
9. **Documents and plugins use their contracts** (`ui/documents/base.py`, `ui/shell/services.py`),
   never the main window (`ShellWindow`). [ADR 0014](docs/adr/0014-plugins-get-a-services-object.md)
10. **No module over 1500 lines; do not grow the hotspots** (ARCHITECTURE.md, *Known hotspots*). A
    feature of the main window or the data view goes into its part (`ui/shell/window_*.py`,
    `ui/documents/dataview_*.py`). [ADR 0017](docs/adr/0017-large-windows-are-split-into-parts.md)

Rules 1, 2, 9 and 10 are checked by `tests/test_architecture.py` (a few seconds, no Qt), rule 3 by
`tests/test_protocol_spec.py`, rule 7 by the tests named there: run them after moving code or adding
imports. Its failure message says what to do; an exception list there is the
last resort, with a reason and an ADR when it changes a rule.

## Style

* Follow the surrounding code. Modules start with a docstring that says what the module is for and
  why it exists; comments explain *why*, not *what*.
* Docs and docstrings are plain English in short sentences, as in the existing `docs/`.
* Keep a change focused: no drive-by reformatting or renames in files you only read.
* Users' files (`*.flow.yaml`, `*.circuit.yaml`, projects, captures) keep loading after a format
  change.

## Done means

* Tests for the changed behaviour added or updated; the suite and `ruff check .` pass.
* User-visible changes in [CHANGELOG.md](CHANGELOG.md) (section *Unreleased*); the topic document in
  `docs/` updated where it describes what changed.
* A change that moves a boundary or breaks a rule above comes with a new ADR
  ([docs/adr/README.md](docs/adr/README.md)) and an update of ARCHITECTURE.md.
* Hardware claims only for what was actually tried on hardware; say which board and firmware.
