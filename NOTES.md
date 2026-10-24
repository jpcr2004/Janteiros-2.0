# Janteiros 2.0 notes

## Decisions
- Kit `arena_kit.total_cost` is the only C. `sim2` subclasses the kit's `Sim` so the API is identical.
- Forbidden paths refused in `Arena._call` and in an httpx request hook (both levels tested).
- `jt play/run` refuse a non-local ARENA_URL unless `--real` (humans only).
- Policy derives everything from thread messages, so a resume after a crash is exact.

## M1 plan (skeleton + safety)
repo, uv, hook, guard+test, wrapper, event log, runner, validator, trivial policy on S01-S03.

## M2 (cost engine, optimiser, validator) - done
- Cost engine reproduces all 199 recorded real deals component by component (relationship aside).
- Planner moves in the supplier's value (`econ.supplier.value`), picks sheets by our C; caps every
  sheet under the supplier's predicted next counter in price and value (else `agreed` freezes us
  at our own price); close-out in round 5 at its next counter on our best terms.
- arena-sim: naive 0.571 -> jt 0.870 (S01 0.917, S02 0.906, S03 0.788). Old agent 0.869/0.859/0.812.
- Open: S03 below the old agent (renewal, 24 months covers the 2-year horizon).
