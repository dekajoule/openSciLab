# 0004. Every device comes from a plugin, addressed by its kind

* Status: accepted
* Date: 2026-11-22 (recorded; built-in devices became plugins on 2026-10-06)

## Context

Devices are opened from the device list, flows (`address:` of a `device.device` node), the CLI
(`-d`), scripts (`api.open`) and device processes. When each of them knew the devices itself, a new
device needed changes in five places, and devices outside the project could not be added at all.

## Decision

* A device is addressed as `kind:rest` (`pico:/dev/cu.usbmodem1`, `sim:uno`, `circuit:amp.circuit.yaml`).
  An address without a kind is one of Pico boards (compatibility with the original software).
* A kind is registered with `driver.kinds.register(kind, open, detect=..., process=..., simulation=...)`
  by a plugin. `driver/discovery.py` (`list_devices`, `open_device`) is the only way in.
* openSciLab's own devices are plugins too (`openscilab/plugins/`, `BUILT_IN`, which also orders the
  device list); a user's plugin uses the same API: a file in the plugins folder, `OPENSCILAB_PLUGINS`
  or an entry point `openscilab.plugins`.
* A plugin registers its UI (device backend, sections, document kinds) in `setup_ui()` only.
* A plugin that fails is reported (status bar, *Help → Plugins…*, `openscilab-cli plugins`) and does
  not stop the others. A plugin cannot replace a built-in kind or node type.

## Consequences

* A new device is a driver, an optional backend and one `register` call; every path opens it.
* The built-in plugins are the living example of the plugin API: changing that API means changing
  them, which keeps it honest.
* The plugin API (`kinds.register`, `@node`, `register_section`, `register_document_type`,
  `ui/shell/services.py`) is public: changes need care and a CHANGELOG entry ([ADR 0014](0014-plugins-get-a-services-object.md)).
* Plugins run with the rights of the application; the documentation says to install only trusted ones.

## Alternatives considered

* A fixed list of drivers in the application: closed to others, five places to change.

## Enforced by

`tests/test_plugins.py` loads `examples/plugins/` and opens their device in the device list, a flow,
the CLI, the API and a device process; `tests/test_device_backends.py`.
