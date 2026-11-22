# 0001. Record architecture decisions

* Status: accepted
* Date: 2026-11-22

## Context

openSciLab grew fast from a logic analyzer to a lab with flows, panels, circuits, many devices and
firmware for several boards. Its rules live in the code, in tests and in the topic documents, but the
reasons for them mostly live in commit messages and in the head of its author. Contributors and
coding agents change code without knowing why it is shaped as it is; they reintroduce what was
removed on purpose, or re-discuss settled questions in pull requests.

## Decision

* Decisions that shape the architecture are recorded as ADRs in `docs/adr/`, numbered, in the
  format of `0000-template.md` (context, decision, consequences, alternatives, enforced by).
* [ARCHITECTURE.md](../../ARCHITECTURE.md) is the map (what is where, which rules hold); ADRs are the
  reasons. Topic documents in `docs/` explain how a part works.
* An accepted ADR is not rewritten; a new ADR supersedes it.
* A pull request that changes a recorded rule brings the ADR that changes it.

## Consequences

* Reviews can point at an ADR instead of explaining a rule again; agents get the rules in a form
  they read before they change code (`AGENTS.md` points here).
* Writing an ADR costs a little time on decisions that matter; the [README](README.md) says when
  one is needed, so small changes stay small.

## Alternatives considered

* Only ARCHITECTURE.md: it would grow into a history; the reasons would crowd out the map.
* GitHub discussions or issues: not versioned with the code, not available offline or to agents.

## Enforced by

Review: a pull request that breaks a rule of ARCHITECTURE.md without an ADR is asked for one
(`.github/pull_request_template.md` asks the question).
