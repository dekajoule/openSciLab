# 0007. Unmodified sigrok decoders on an own numpy runtime, in a decode process

* Status: accepted
* Date: 2026-11-22 (recorded)

## Context

The original application embedded CPython through Python.NET and reimplemented the decoder API in
C#, evaluating wait conditions one sample at a time. libsigrokdecode itself is a C library that is
hard to ship on three systems. The sigrok project maintains well over a hundred decoders as plain
Python (`pd.py`); users expect them to behave as in PulseView. A long decode in a thread holds the
GIL for milliseconds and stalls streaming (ADR 0006).

## Decision

* `openscilab/sigrok/runtime.py` is a `sigrokdecode`-compatible runtime in Python: installed under
  that module name, it runs the **unmodified** `pd.py` files; wait conditions are evaluated with
  numpy over blocks of samples.
* The decoders are shipped as source in `decoders/` (sigrok's, the LogicAnalyzer 6.5 set and own
  ones such as `c64bus`), with their licences in [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md).
  Decoders of sigrok are kept as they come from upstream; a fix goes upstream, or into the runtime
  when the runtime is at fault, or into a decoder of its own.
* Decoding runs in a decode process (`sigrok/worker.py`, `DecodeService`): started once, decoders
  kept loaded, samples via shared memory, cancel through a shared flag.
* Decoders are loaded when they are looked at, not at start-up.

## Consequences

* New sigrok decoders can be dropped in; behaviour matches PulseView; no C library to ship.
* The runtime must follow the sigrokdecode API, including its quirks; a decoder that needs an API
  part not yet implemented fails until the runtime grows.
* The packaged build must contain every module a decoder imports.

## Alternatives considered

* Bundling libsigrokdecode: a native build per platform, and its Python embedding conflicts with ours.
* Rewriting decoders natively: loses the sigrok ecosystem.

## Enforced by

`tests/test_sigrok.py`, `tests/test_sigrok_session.py`, `tests/test_decode_worker.py`;
`tests/test_packaging.py` checks the packaged build includes every module the decoders import;
`tests/test_startup.py` checks the palette loads decoders on demand.
