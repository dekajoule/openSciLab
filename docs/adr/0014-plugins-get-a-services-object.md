# 0014. Plugins and documents get narrow contracts, not the main window

* Status: accepted
* Date: 2026-11-22 (recorded; with plugin sections, October 2026)

## Context

`ui/shell/main_window.py` is the largest module of the project and changes often. Plugins (sections,
document kinds) and the documents themselves need things from the window: open a file, show a
message, the active project, the hub. If they reached into the window (`ShellWindow`), every change of it
would break plugins outside the repository, and documents would depend on each other through it.

## Decision

* A document is a `DocumentWidget` and follows the contract of `ui/documents/base.py`: the shell asks
  it for title, path, dirty state, menus, actions, tool bar, inspector content and run state, and
  drives what it runs through the run protocol. Documents do not build shell UI of their own.
* Sections and document kinds of plugins get `services` (`ui/shell/services.py`) instead of the
  window: `open_file`, `add_document`, `active_document`, `active_project`, `notify`, `hub`, ... and
  `window` only as the parent of dialogs.
* A new need of a plugin is met by a new member of `services` (documented in
  [docs/drivers.md](../drivers.md)), not by handing out the window.
* `services`, `register_section`, `register_document_type`, `kinds.register` and `@node` form the
  public plugin API: a change that breaks it is named in the CHANGELOG.

## Consequences

* The main window can be refactored without breaking plugins; it can also be split, because its
  users depend on small interfaces.
* Some indirection: a document asks the shell through a signal or `services` rather than calling
  another document.

## Alternatives considered

* Handing plugins the main window: quickest, but every refactoring becomes a breaking change.

## Enforced by

`tests/test_sections.py` (a plugin section and document kind through `services`),
`tests/test_view_documents.py`, `tests/test_shell.py`; `tests/test_architecture.py`: the main window is
imported by `app.py` and `ui/shell/` only.
