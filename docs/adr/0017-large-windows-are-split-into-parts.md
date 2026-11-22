# 0017. Large windows are split into parts; plugins run extensions

* Status: accepted
* Date: 2026-11-22

## Context

`ShellWindow` (`ui/shell/main_window.py`) and `DataView` (`ui/documents/dataview.py`) had grown to
about 2900 lines each, `ShellWindow` with 167 methods. Almost every feature touched one of them: pull
requests and parallel agents conflicted there, and an agent had to read thousands of lines to change
one menu. The shell also knew things that belong to plugins: it started and stopped the server of
remote devices and read the serial ports through the Pico driver.

## Decision

* A large widget class is split into **parts**: mixin classes in modules of their own next to it,
  one per concern, combined by the class (`class ShellWindow(ShellBuildMixin, ShellDocumentsMixin,
  ShellDevicesMixin, ShellProjectsMixin, ShellLayoutMixin, QMainWindow)`).
  * `ui/shell/`: `window_build.py` (header, sidebar, docks, menus), `window_documents.py`,
    `window_devices.py`, `window_projects.py`, `window_layout.py`; `devices_section.py`;
    `window_common.py` (constants and helpers they share).
  * `ui/documents/`: `dataview_build.py`, `dataview_menus.py`, `dataview_source.py`,
    `dataview_capture.py`, `dataview_files.py`, `dataview_samples.py`; `dataview_common.py`.
* The class itself keeps its Qt signals (they must be declared on the `QObject` subclass), its
  `__init__`, its contract (the document protocol, the run state) and what concerns it as a whole.
  Parts share state through `self` and do not import each other; what they share lives in the
  `*_common.py` module.
* The split moved methods unchanged: names, signatures and behaviour stayed, so callers did not
  change. Names other code imported from the old module are re-exported there.
* New behaviour goes into the part it belongs to, or into a new part; `tests/test_architecture.py`
  keeps every module under 1500 lines (ADR 0016).
* Something a plugin runs while a window is open is an **extension** (`register_extension` in
  `ui/sections.py`): the window builds it with `services`, tells it `preferences_changed(changes)` and
  `close()`. The server of remote devices is the extension `RemoteServer` of `plugins/remote.py`
  (`ui/devices/remote.py`); the shell no longer knows it.

## Consequences

* Each module is under 750 lines; a change of the device handling and one of the menus touch
  different files.
* Mixins are a first step, not the end: a part is still coupled to the attributes of the window
  through `self`. A part that grows a clear interface of its own (the Devices section did) becomes a
  class of its own that the window holds, like `DevicesSection`.
* Tests that replaced a name in the old module (`monkeypatch.setattr(dataview, "AlignDialog", ...)`)
  name the part that uses it now.
* A window's parts are found by their module names; ARCHITECTURE.md lists them.

## Alternatives considered

* Controllers (objects the window holds) from the start: cleaner, but every one of ~300 methods would
  have needed new call paths and a test of its own, with many callers outside (tests, documents,
  plugins) - a rewrite instead of a move. Mixins split the files now without changing behaviour.
* Leaving the classes as they were with a "do not grow" rule: the conflicts and the reading cost stay.

## Enforced by

`tests/test_architecture.py` (line limit, the main window only imported by the shell, device drivers
only from their own code); `tests/test_sections.py`, `tests/test_remote.py` (the extension).
