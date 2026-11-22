# 0010. Simulator first; tests never touch hardware

* Status: accepted
* Date: 2026-11-22 (recorded)

## Context

Contributors and CI do not have the hardware openSciLab supports, and even the author has only some
boards (see the device table of the README). Features that can only be tried on hardware are
untestable in a pull request, and regressions on boards nobody has at hand go unnoticed.

## Decision

* Every function works with a simulator before it works with hardware. A new device brings a
  simulator profile (`driver/simulated/`, `simulation=` of its kind) or a fake transport.
* Simulators behave like the real devices, including limits, wiring, faults and USB timing; for the
  Arduino firmware and the Rigol bridge they speak the real wire protocol
  (`driver/simulated/arduino_shell.py`, `scpi_shell.py`), so the real driver code is exercised.
* Tests never open real devices: `tests/conftest.py` hides USB devices, USBTMC instruments and
  network beacons. Drivers are tested against a faked transport (`tests/test_driver.py`,
  `tests/test_dslogic_driver.py`).
* What has been tried on real hardware is stated in the README's device table and in the pull
  request (board, firmware, what was wired); it is not assumed.

## Consequences

* The whole application, flows and examples run in CI without hardware.
* A simulator is extra work for every device and must be kept in step with the firmware; a simulator
  that drifts gives false confidence, so protocol changes update the simulator shell in the same
  pull request (ADR 0005).
* Hardware-only behaviour (signal integrity, real timing) still needs a test on the bench; the PR
  template asks for it.

## Alternatives considered

* Hardware-in-the-loop CI: costly, and still only for the boards on the rack.

## Enforced by

`tests/conftest.py` (autouse fixtures); `tests/test_simulated_*.py`, `tests/test_arduino_driver.py`
(against the Arduino shell), `tests/test_rigoldho.py`; review asks for a simulator path for new features.
