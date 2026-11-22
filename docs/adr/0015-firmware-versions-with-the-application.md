# 0015. Firmware lives in the repository and versions with the application

* Status: accepted
* Date: 2026-11-22 (recorded)

## Context

The Pico firmware, the Arduino firmware and the Rigol bridge app speak protocols that the drivers and
the simulators must match exactly (ADR 0005). Kept in separate repositories, a driver change and its
firmware change would land at different times, and users would combine versions nobody tested.

## Decision

* The firmware sources are in `firmware/` (Pico: C with the Pico SDK; Arduino: PlatformIO; bridge:
  Android) and change in the same pull request as the driver, the simulator and `docs/protocols.md`.
* Each firmware reports its protocol version; the driver checks it and refuses a firmware it does
  not speak (`FirmwareOutdatedError` of the Pico driver), and the application offers the update
  (`core/firmware.py`) instead of misbehaving.
* A release tag `v<version>` must match `pyproject.toml`, `openscilab/__init__.py` and
  `firmware/pico/CMakeLists.txt`, and `CHANGELOG.md` must have a section for it; CI refuses the release
  otherwise. Every push to `main` builds the firmware into the *latest-build* pre-release when
  `firmware/` changed.
* Firmware changes say in the pull request on which boards they were tested, with real signals if
  possible.

## Consequences

* One version number describes a working set of application and firmware.
* The repository needs C, PlatformIO and Android tool chains for full builds (CI has them;
  contributors to the Python side do not need them).

## Alternatives considered

* Separate firmware repositories with their own versions: a compatibility matrix nobody maintains.

## Enforced by

`.github/workflows/build.yml` (job *Plan*, version check); `tests/test_firmware.py`,
`tests/test_protocol_spec.py` (firmware headers against the spec); the PR template.
