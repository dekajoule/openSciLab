# 0012. Look and words follow design rules that tests enforce

* Status: accepted
* Date: 2026-11-22 (recorded; rules from the UX overhaul of October 2026)

## Context

Many documents, dialogs and node types were written at different times, by hand and with coding
agents. They drifted: the same thing had three names, colours were written as literals, documents
had their own run buttons, shortcuts collided. Written rules alone did not stop the drift.

## Decision

* [docs/design.md](../design.md) holds the seven design rules, the glossary, the theme and the rules
  for nodes. It is binding for the user interface, the node reference and the messages.
* Colours, fonts, icon sizes and spacing come from `openscilab/ui/theme.py` (`token()`, `qcolor`,
  `space()`, `ICON`); every token has a light and a dark value. No literal colour, font or size
  elsewhere.
* User-visible texts are English and use the words of the glossary.
* *Run*, *Pause*, *Stop* belong to the shell; documents implement the run protocol of
  `ui/documents/base.py` instead of having buttons of their own. Shortcuts are unique.
* A rule that can be checked gets a test; a new rule goes into design.md and its test together.

## Consequences

* Drift is caught in CI, not in review; contributors and agents get a precise failure message.
* Adding a colour or a word means adding a token or a glossary entry first.
* The tests check strings and source lines, so they may need an allowance for a legitimate
  exception; that allowance is explicit in the test.

## Alternatives considered

* A style guide without tests: it had already drifted.

## Enforced by

`tests/test_theme_tokens.py`, `tests/test_terminology.py`, `tests/test_commands.py`,
`tests/test_node_audit.py`, `tests/test_accessibility.py`.
