# 0009. One asyncio engine runs flows in real or virtual time

* Status: accepted
* Date: 2026-11-22 (recorded)

## Context

Flows mix streaming data (blocks of samples) with control (timers, sweeps, sequences, state
machines, Python code that waits). With real devices time is the wall clock. With simulators a flow
of minutes should finish in milliseconds and give the same result every time, so it can be a test
(`openscilab run --report`) and so examples can be checked in CI.

## Decision

* `lab/engine/engine.py` runs a flow on one asyncio event loop with two planes: a **data plane**
  (values travel along wires into per-node mailboxes, handled in arrival order; a full mailbox holds
  the sender back) and a **control plane** (coroutine nodes: `async def ... (ctx)`).
* A node's time is the flow's time: `ctx.now()`, `ctx.sleep()`, `ctx.sleep_until()`, never
  `time.time()` or `asyncio.sleep(t)` for waiting. In **virtual time** the clock jumps to the next
  wake-up as soon as every node waits. The wall clock (`time.monotonic()`) is only used to translate
  a device's time stamps into flow time in real time (guarded by `ctx.fast`).
* Simulated devices take the engine's clock (and a `seed`), so a simulated flow is deterministic.
* A node calls a device through `ctx.device_call()` and other blocking work through `ctx.external()`:
  in real time it runs in a thread (one call at a time per device) while the other nodes, *Pause* and
  *Stop* go on; simulators and virtual time are called directly to stay deterministic
  (`lab/engine/runtime.py`).

## Consequences

* Flows are testable and reproducible; examples run in the test suite in virtual time.
* A node that sleeps or reads the clock on its own breaks virtual time and determinism; reviewers
  look for that.
* Back pressure means a slow node slows its senders instead of growing memory without limit.

## Alternatives considered

* A thread per node: no determinism, the GIL makes it no faster.
* Qt's event loop: would tie the engine to Qt (ADR 0002).

## Enforced by

`tests/test_lab_engine.py`, `tests/test_engine_reliability.py`, `tests/test_timing_flows.py`,
`tests/test_node_audit.py` (virtual captures and streams keep the flow's time), `tests/test_examples.py`.
