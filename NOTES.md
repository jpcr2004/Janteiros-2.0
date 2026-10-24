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

## M3 (runner hardening) + M4 (world, claims, model)
- Clocks injectable; tests run the kit sim in fake time (supplier latency 19-45 s) incl. 3 bidders.
- A thread waits (N-1) sends before its turn: prune the least promising bidder when that exceeds 52 s.
- Panic close after 2 timeouts in a case; policy exceptions -> plain cheapest-valid close; network
  errors -> re-read the arena's thread views and continue; crash -> new process resumes on the 409.
- World = SQLite + release CSVs (same table names) + facts.json + release facts + case facts.
- Claim bank: falling drivers (window ends <= 10 months before series end), should-cost, notice,
  forecast, scorecard, order prices, rival offers; 2 in round 1 then 1 per round.
- arena-sim with world: 0.841 (S01 0.869, S02 0.866, S03 0.788): kit sim ignores payment value, our
  founder profile trades payment for price there for nothing. Not tuned away: sim artifact.

## M5 sim2 (calibrated simulator)
- Subclass of the kit `Sim`: same API. Personas: first counter independent of anchor; later
  concession min(0.8 x move, cap 1.6/0.8/0.6/0.4/0.2 %); `agreed` when our sheet reaches its next
  counter (freezes); patience = cumulative gap budget (0.36-0.40; full capacity 0.18; approval 0.25);
  walk risk after final; claim effects by kind/owner; distributors refuse no-clause/deadband/long fixed.
- Calibration: floor fitted so C_best = arena c_best (single supplier), joint factor for RFQ, full
  cost fitted for relationship goals. C_best within 0.2 % on 11/12 recorded cases (P02 -6 %, its
  clause valuation). Replay of 102 real sessions: counter price error 1.17 %, first counter 0.98 %.
- Old agent vs sim2 vs real practice tracks on P05/P07/P08/P10/P11; P03 (0 vs 0.92) and P02 off.
- Known sim artifacts: kit arena-sim concedes a fixed 2 % per message regardless of our move.

## Planner findings (sim2, measured)
- Under min(0.8 m, cap) the deal lands at about anchor + gap/1.8: anchor depth sets the outcome;
  moves of cap/0.8 collect every cap; never retract (a lower offer reads as a step back).
- Close-out: move m = max(gap/1.8, gap - cap) (gap - cap when it gives more than we move).
- First counter is not a concession: exclude it from next-counter predictions.
- Keep our sheet structure when the supplier substitutes terms (distributor clause).
- Relationship goals: C-min guard in round 1 only when below the quote; gate with a 4 % higher
  full cost too (stress test found deals above the alternative otherwise).

## Results (no LLM)
- sim2 full (64): jt 0.754 vs old agent 0.715. arena-sim: jt 0.743 vs old 0.804 (S01 0.869 =,
  S02 0.761 < 0.851, S03 0.629 < 0.717): the fixed-step kit supplier punishes our sized moves.
- Stress 350 mutated cases: no crash, no error, no validator block, no bad deal.

## Open
- LLM writer/reader not measured (no key in .env).
- Promotion rule fails on arena-sim S02/S03 (kit artifact, see above): humans decide.
