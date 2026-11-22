## What and why

<!-- What does this change, and why is it needed? Link the issue if there is one. -->

## How it was tested

<!-- Tests added or changed; the simulator used (sim:uno, sim:pico, ...). -->

- [ ] `QT_QPA_PLATFORM=offscreen pytest` passes
- [ ] `ruff check .` passes

**On hardware** (if the change touches a driver or firmware): board, firmware version and what was
wired - or "not tried on hardware".

## Checklist

- [ ] One focused change; no unrelated reformatting
- [ ] User-visible changes are in `CHANGELOG.md` (*Unreleased*) and the relevant `docs/` page
- [ ] Follows [ARCHITECTURE.md](../ARCHITECTURE.md); a rule it changes comes with a new
      [ADR](../docs/adr/README.md)
- [ ] New capabilities or firmware commands are in `docs/protocols.md`, the driver, the firmware and
      the simulator together
- [ ] UI texts in English with the words of the glossary (`docs/design.md`)
