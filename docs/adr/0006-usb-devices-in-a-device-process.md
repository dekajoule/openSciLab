# 0006. Devices on USB are read in a device process; samples through shared memory

* Status: accepted
* Date: 2026-11-22 (recorded; device processes since October 2026)

## Context

A Pico streams from a ring of 128 KiB and overflows when the computer reads 22 ms late (at 6 MB/s).
In one Python process the GIL lets one thread run at a time: drawing, flows, decoders, scripts and
the garbage collector can hold the reading thread longer than that (`tools/benchmark.py stall`
measured up to about 90 ms). A crash or hang of a driver also took the whole application down.

## Decision

* Kinds registered with `process=True` (devices on USB or serial links that lose what is not read in
  time) are opened in a **device process** (`driver/process/`): driver and facets live there, the
  application gets a proxy (`proxy.ProcessDriver`, `RemoteObject`) with the same interface.
* Samples are allocated through `core/sample_store`; in a device process they are shared memory
  (`core/shared_arrays`) and never go through the pipe. Plain values travel by value, drivers,
  facets and functions by reference (`driver/process/wire.py`).
* Progress and completion come back in order; a newer progress replaces one not yet sent, so a busy
  application lags but never slows the reading down.
* A process that dies fails its capture with a message and disconnects the instrument; one that
  misses its heartbeat (`proxy.BEAT_TIMEOUT`) or a call (`proxy.CALL_TIMEOUT`) is killed. If the
  application goes away, the device process stops the device.
* Network devices (remote devices, the Rigol) stay in the application: TCP loses nothing. The
  preference `devices.process` can switch it off.
* The same rule for other heavy Python next to streaming: decoding (ADR 0007) and ngspice (ADR 0011)
  run in processes of their own.

## Consequences

* Streams do not overflow because the UI is busy; a driver crash does not end the application.
* Everything a driver returns must be picklable or a reference; drivers must allocate samples through
  `core/sample_store` and raise events through `_raise_capture_progress`/`_raise_capture_completed`.
* Debugging crosses a process boundary; `--debug-driver` and the logs of the device process help.
* Tests open fake drivers in the test process (`tests/conftest.py` switches device processes off);
  `tests/test_device_process.py` and `tests/test_simulator_process.py` test the processes on purpose.

## Alternatives considered

* Threads only, with a larger buffer on the computer: the GIL, not the buffer, is the bottleneck.
* Reading in C or Rust: a second language for every driver and for plugin authors.

## Enforced by

`tests/test_device_process.py`, `tests/test_simulator_process.py`, `tests/test_shared_arrays.py`,
`tests/test_driver_races.py`; the soak test (`pytest -m soak`) watches memory over long streams.
