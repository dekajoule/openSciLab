# 0016. The architecture rules are tests

* Status: accepted
* Date: 2026-11-22

## Context

ARCHITECTURE.md lists which layer may import which, and ADRs 0002–0014 add rules (no Qt in the
headless layers, ask for facets, plugins import UI in `setup_ui()`, documents do not use the main
window). Only "no Qt" was checked (`tests/test_startup.py`); everything else relied on review. Coding
agents and contributors who do not know the rules broke them without any test failing - the first
check found the shell importing a Pico module to list serial ports, the flows depending on the
scripting API above them, and the shell running the server of remote devices itself.

## Decision

* `tests/test_architecture.py` reads the imports of the source with `ast` (nothing is imported, the
  test takes a few seconds) and checks:
  * Qt only in `ui/`, `app.py`, `qt_plugins.py`; the headless layers never reach `ui/` or `app`;
  * module-level imports follow the layer table of ARCHITECTURE.md;
  * lazy imports across headless layers are listed in `LAZY_EXCEPTIONS` with their reason;
  * plugins import `ui/` inside `setup_ui()` only;
  * the drivers of real devices (`pico`, `arduino`, `dslogic`, `rigoldho`, `htool`) are used by their
    folder, their plugin and `ui/devices/` only, other uses are listed in `DEVICE_EXCEPTIONS`;
  * the main window is imported by `app.py` and `ui/shell/` only;
  * `openscilab_device` needs the standard library only;
  * no import cycles between modules at module level;
  * no module grows past 1500 lines; the existing large ones are listed with their limit.
* Every exception has a reason, and an exception that is no longer needed fails the test too: the
  lists only shrink.
* `driver.simulated` and `driver.remote` are not device packages in this sense: simulators and remote
  devices are features of the whole application (ADR 0010, docs/remote.md) and may be used where they
  are needed. Asking for the type of a facet (`RemoteFacet`) is asking for a facet.
* The capture vocabulary `driver.models`, `driver.base` and `driver.software_trigger` may be used by
  `core` (they import only leaf modules of `core`, so there is no cycle).
* The PDF of a report stays a lazy import of `ui/report_pdf.py` from `lab/nodes/report.py`: Qt lays the
  PDF out, the command line needs it too, and a registry would only hide the same import.

## Consequences

* A broken rule fails in CI with the file, the line and what to do instead; reviewers and agents need
  not know the rules by heart.
* Changing a rule means changing the test in the same pull request - visible in the diff, with an ADR.
* The line limit makes "split the module" the cheap path; a hotspot that shrinks below the limit must
  leave the list, so the limits follow the code down.

## Alternatives considered

* [import-linter](https://github.com/seddonym/import-linter): a good tool, but it counts lazy imports
  like module-level ones (every allowed lazy import becomes an ignore entry), cannot say "only inside
  `setup_ui()`", and is another dependency; the rules above are about 300 lines of test.

## Enforced by

`tests/test_architecture.py` (part of the normal test run).
