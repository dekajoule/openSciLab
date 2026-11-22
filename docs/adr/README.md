# Architecture decision records

An ADR records one decision that shapes the code: the situation, what was decided, what follows
from it and how it is checked. Together with [ARCHITECTURE.md](../../ARCHITECTURE.md) they answer
"why is it like this?" before someone (a person or a coding agent) changes it.

ADRs 0002–0015 were written down in November 2026 for decisions that the code already followed;
their dates are the dates they were recorded, the decisions themselves are older (the git history
has them).

## Index

| ADR | Decision | Status |
| --- | --- | --- |
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions | accepted |
| [0002](0002-qt-only-in-the-ui-layer.md) | Qt only in the user interface layer | accepted |
| [0003](0003-facets-not-device-kinds.md) | Ask for facets, never for the kind of device | accepted |
| [0004](0004-every-device-comes-from-a-plugin.md) | Every device comes from a plugin, addressed by its kind | accepted |
| [0005](0005-protocols-md-is-the-capability-spec.md) | docs/protocols.md is the single source of the capability vocabulary | accepted |
| [0006](0006-usb-devices-in-a-device-process.md) | Devices on USB are read in a device process; samples through shared memory | accepted |
| [0007](0007-sigrok-decoders-on-an-own-runtime.md) | Unmodified sigrok decoders on an own numpy runtime, in a decode process | accepted |
| [0008](0008-the-flow-model-is-canonical.md) | The flow model is canonical; YAML, editor and Python are views of it | accepted |
| [0009](0009-one-engine-real-and-virtual-time.md) | One asyncio engine runs flows in real or virtual time | accepted |
| [0010](0010-simulator-first.md) | Simulator first; tests never touch hardware | accepted |
| [0011](0011-native-tools-in-the-vendor-folder.md) | Native tools are installed on demand into the vendor folder and run in a process | accepted |
| [0012](0012-design-rules-enforced-by-tests.md) | Look and words follow design rules that tests enforce | accepted |
| [0013](0013-no-silent-failures.md) | No silent failures | accepted |
| [0014](0014-plugins-get-a-services-object.md) | Plugins and documents get narrow contracts, not the main window | accepted |
| [0015](0015-firmware-versions-with-the-application.md) | Firmware lives in the repository and versions with the application | accepted |
| [0016](0016-architecture-rules-are-tests.md) | The architecture rules are tests | accepted |
| [0017](0017-large-windows-are-split-into-parts.md) | Large windows are split into parts; plugins run extensions | accepted |

## When to write one

Write an ADR in the pull request that

* adds or removes a layer, a process, a registry or an extension point;
* breaks or changes a rule of ARCHITECTURE.md or of an accepted ADR;
* chooses one of several reasonable ways that later contributors would otherwise re-discuss
  (a library, a file format, a protocol, a threading model);
* makes something hard to undo (a file format users keep, a plugin API, a firmware protocol).

A bug fix, a new node, a new device that follows [docs/drivers.md](../drivers.md) or a refactoring
inside a module needs none.

## How

1. Copy [0000-template.md](0000-template.md) to the next free number with a short slug:
   `NNNN-what-was-decided.md`.
2. Fill it in; keep it short (one screen is plenty). Name what checks the decision (a test, a
   review rule) under *Enforced by*: an unchecked rule erodes.
3. Add it to the index above and, if it changes a rule, update [ARCHITECTURE.md](../../ARCHITECTURE.md).
4. An accepted ADR is not rewritten. A new decision *supersedes* it: the new ADR says
   `Supersedes 00NN`, the old one gets the status `superseded by 00MM` and nothing else changes.
   Typos and broken links may be fixed at any time.

Statuses: `proposed` (under discussion in a pull request), `accepted`, `deprecated` (no longer
followed, nothing replaces it), `superseded by NNNN`.
