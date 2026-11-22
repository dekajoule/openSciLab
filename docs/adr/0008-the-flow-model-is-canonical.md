# 0008. The flow model is canonical; YAML, editor and Python are views of it

* Status: accepted
* Date: 2026-11-22 (recorded)

## Context

A flow (a measurement program) can be drawn in the visual editor, written as `*.flow.yaml` or
written in Python (`openscilab.lab.dsl`), and run from the application, `openscilab run` or a
script. If each of these had its own representation, features would exist in one and not in the
others, and a flow saved from the editor could change meaning when loaded by the CLI.

## Decision

* `lab/model.py` (`Flow`, nodes, ports, edges) is the one canonical form. `lab/yaml_io.py`, the
  editor (`ui/flow/`) and `lab/dsl.py` read and write that model only.
* Node parameters stay as written (`"10 ms"`, `"1 MHz"`) in the model and are interpreted by the node
  type; a round trip through YAML keeps them.
* Devices are nodes (`device.device`) whose output is wired to every node that uses the instrument.
* Node types are declared once with `@node` (`lab/nodes/registry.py`): inputs, outputs, typed
  parameters. The palette, the inspector, validation and the node reference are derived from it.

## Consequences

* A feature of flows is added to the model and the engine once; the editor, YAML and the DSL get it
  by construction.
* Flow files are user data: a model change keeps reading old files (and the examples in
  `examples/library/` are the regression set).
* The editor cannot keep state the model does not have; layout data lives in the model too.

## Alternatives considered

* The editor's scene graph as the source, YAML as an export: the CLI would need the editor.

## Enforced by

`tests/test_lab_model.py`, `tests/test_lab_dsl.py`, `tests/test_node_schema.py`,
`tests/test_examples.py` (every example loads and runs); `tests/test_fuzz_parsers.py` for the YAML.
