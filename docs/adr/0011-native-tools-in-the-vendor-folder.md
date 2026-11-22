# 0011. Native tools are installed on demand into the vendor folder and run in a process

* Status: accepted
* Date: 2026-11-22 (recorded; with *Circuits*, October 2026)

## Context

*Circuits* needs ngspice (shared library, code models), simavr for emulated microcontrollers and
KiCad's symbol libraries. Bundling them would grow every download for a feature not everyone uses,
tie releases to their builds on three systems and mix licences into the package. ngspice keeps its
state in the library (one simulator per process) and calls back for every accepted point, up to a
hundred thousand times a second, each call needing the GIL.

## Decision

* Programs and libraries of others are described in `core/vendor.py` (name, licence, where it is
  found) and are either found on the computer or installed into openSciLab's vendor folder when first
  needed (`core/vendor_build.py`, `tools/build_vendor.py`). They are not bundled with the package.
* ngspice runs in an **engine process** of its own (`circuits/engines/process.py`): one ngspice per
  process, several circuits at once, a crash ends only that process. Idle processes are kept for the
  next run.
* ngspice's callbacks are short and never touch Qt (ADR 0002); the UI reads a snapshot of the history
  with a timer.
* The ctypes layer (`circuits/engines/ngspice.py`) is the only place that talks to the library.

## Consequences

* Downloads stay small; the native tools are updated independently; licences stay separate.
* The first use of *Circuits* needs a build or download step and can fail on a computer without a
  compiler; that failure must be reported clearly (ADR 0013).
* CI builds ngspice and simavr from source (cached) to test *Circuits*.

## Alternatives considered

* Bundling the binaries: larger downloads, licence and platform work on every release.
* ngspice in the application process: starved by the GIL and able to crash the application.

## Enforced by

`tests/test_circuit_simulation.py`, `tests/test_avr.py`, `tests/test_circuit_devices.py`; CI step
*Build ngspice and the AVR emulator* in `.github/workflows/build.yml`.
