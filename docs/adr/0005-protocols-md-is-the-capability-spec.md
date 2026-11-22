# 0005. docs/protocols.md is the single source of the capability vocabulary

* Status: accepted
* Date: 2026-11-22 (recorded)

## Context

A capability string (`GPIO`, `PWM`, `ANALOG`, ...) is reported by the firmware in C, parsed by the
drivers in Python, unlocks facets in `core/instrument.py` and is played by the simulators. Three
languages and four places drift apart as soon as one is changed alone: a firmware reports what no
driver understands, or a facet is offered that no firmware backs.

## Decision

* The tables of [docs/protocols.md](../protocols.md) (*Capabilities*, *Pins*) are the specification of
  the capability strings, the pin capabilities and the facets they unlock.
* A new capability is added there first; then `driver/base.py`, the facets, the firmware headers
  (`firmware/pico/capabilities.h`, `firmware/arduino/capabilities.h`) and the simulators follow, in the
  same pull request.
* The firmware protocols (Pico protocol 8, Arduino protocol 2, the bridge protocol 1, HT008) are
  specified in the same document; a command changes there and in the firmware, the driver and the
  simulator shell together.

## Consequences

* The document is read by tests, so editing its tables is a code change (CI runs on it, see
  `.github/workflows/build.yml`).
* Contributors writing firmware for a new board have one place to look.

## Alternatives considered

* Constants in Python as the source, documentation generated from them: the C headers would still
  need their own copy, and the documentation would lose its explanations.

## Enforced by

`tests/test_protocol_spec.py`: the table is well formed, Python constants, facets, pin capabilities
and the C headers agree with it, and the CAPS line the Pico firmware builds on the host is what the
driver understands. `tests/test_protocol_vectors.py` checks frames against fixtures.
