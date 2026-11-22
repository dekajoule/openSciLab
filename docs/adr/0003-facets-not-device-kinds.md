# 0003. Ask for facets, never for the kind of device

* Status: accepted
* Date: 2026-11-22 (recorded)

## Context

openSciLab works with very different devices: Pico and Arduino boards, DSLogic analyzers, a Rigol
oscilloscope, an RF source, remote devices, circuits and simulators, plus devices that plugins
bring. Code that branches on the kind of device (`if driver is a Pico ...`) has to be changed for
every new device, breaks for plugin devices nobody in the project knows, and lets simulators drift
from the devices they stand for.

## Decision

* A device is an `Instrument` (`core/instrument.py`) with **facets**: `CaptureFacet`, `GpioFacet`,
  `MonitorFacet`, `AnalogInFacet`, `AnalogOutFacet`, `GeneratorFacet`, `CacheFacet`,
  `SimulationFacet`.
* What a device can do follows from the capabilities its driver reports (`CAPABILITY_*` in
  `driver/base.py`, `facets_of()`) and from the facets it returns from `instrument_facets()`.
* Documents, nodes, dialogs, the CLI and the API ask for a facet or a capability and show what it
  allows. They never test the class, the `driver_type` or the `driver_id` of a device.
* Code that is specific to one device stays in its folder (`driver/<device>/`,
  `ui/devices/<device>.py`).

## Consequences

* A new device that implements the facets works everywhere without changes elsewhere, including
  devices from plugins.
* The simulators implement every facet (`driver/simulated/facets.py`) and are the reference
  implementation for a new device.
* A feature only one device has needs a facet method or a capability first, which is more work
  than an `if`; that is the price of not breaking the others.

## Alternatives considered

* A class hierarchy per device family with `isinstance` checks: closed to plugins.
* One large driver interface with optional methods everyone implements: unclear which are real.

## Enforced by

Review: no `isinstance(..., <ConcreteDriver>)`, `driver_id ==` or `driver_type ==` outside the
device's own folder. `tests/test_device_backends.py` and `tests/test_plugins.py` open a minimal
driver through every path.
