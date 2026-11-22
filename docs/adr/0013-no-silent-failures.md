# 0013. No silent failures

* Status: accepted
* Date: 2026-11-22 (recorded; the rule is in CONTRIBUTING.md)

## Context

openSciLab talks to hardware, other processes, the network and native libraries; much can fail and
much is retried or tolerated on purpose. An `except: pass` that hides such a failure turns a clear
error into "the capture is empty" or "the device does not answer", which is very hard to diagnose on
someone else's bench.

## Decision

* Every `except` either **reports** the error (a message for people, a banner, a badge, the result of
  the call) or **logs** it - at least `log.debug(..., exc_info=True)`, so `--debug-driver` and the
  console show what was ignored and why.
* Bare `except:` is not used; catch the narrowest exception that is expected.
* Errors for people are raised as the project's own exceptions with a message written for them
  (`DeviceConnectionError` when opening, `InstrumentError` for facets); `start_capture()` returns a
  `CaptureError` instead of raising. The UI shows their message.
* Errors nobody caught reach `crash.log` (`core/crashes.py`); a helper process that dies or hangs is
  reported (ADR 0006).

## Consequences

* Problems reported by users come with a log that says what happened.
* Slightly more code in handlers; a deliberately ignored error carries a comment and a debug log.

## Alternatives considered

* Letting everything propagate: a device that unplugs during a capture would end the application.

## Enforced by

Review (CONTRIBUTING.md, *Development setup*); `tests/test_crashes.py`, `tests/test_device_reliability.py`,
`tests/test_widget_robustness.py`. Candidate for a lint rule (ruff `S110`/`BLE001`) once the existing
code passes it.
