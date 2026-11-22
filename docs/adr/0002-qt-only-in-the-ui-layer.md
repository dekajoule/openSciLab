# 0002. Qt only in the user interface layer

* Status: accepted
* Date: 2026-11-22 (recorded; followed since the port from C#)

## Context

openSciLab runs in four kinds of processes: the application, `openscilab-cli`, scripts using
`openscilab.api`, and helper processes (device processes, the decode process, the ngspice engine
process). Only the application has a display. Importing PySide6 costs start-up time and memory,
fails on headless machines without the Qt libraries, and in a device process would bring Qt's
threads next to the thread that reads a device. Domain code that depends on Qt can also only be
tested with a `QApplication`.

## Decision

* `openscilab/core`, `driver`, `lab`, `sigrok`, `circuits`, `sdl`, `api.py` and `cli.py` never import
  Qt, directly or through a module they import.
* Qt is imported in `openscilab/ui/`, `app.py` and `qt_plugins.py` only.
* A plugin imports its UI inside `setup_ui()`, which only the application calls.
* Domain code reports to the UI through plain callbacks, events or polling (the circuit document
  reads `LiveSimulation.history` with a timer); the UI adapts them to signals.
* The one allowed exception is a lazy import inside a function where Qt does the work (the PDF of
  a report, `lab/nodes/report.py`), with a comment saying so.

## Consequences

* The CLI, the API, flows run headless (`openscilab run --report`) and all helper processes work
  without a display and start fast.
* Domain logic is tested without Qt; only `tests/` of the UI need `QT_QPA_PLATFORM=offscreen`.
* Some things are written twice in spirit: a Qt-free observable in `core` and its signal adapter
  in `ui`.

## Alternatives considered

* Qt's non-GUI modules (`QtCore`) in the domain for signals and timers: still a PySide6 dependency
  for the CLI and the device processes.

## Enforced by

`tests/test_startup.py::test_the_core_the_drivers_and_the_lab_load_without_qt` imports the
headless modules in a fresh interpreter and fails when any PySide6 module was loaded;
`tests/test_plugins.py` loads plugins without their UI. A new headless module belongs in that
test's list.
