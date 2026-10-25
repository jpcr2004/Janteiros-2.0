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
- LLM key: only LLM_API_KEY (from .env or the shell); a shell ANTHROPIC_API_KEY is ignored (it may bill someone else). LLM eval waits for that key.

## M6 measured (LLM, opus 5.5 effort low, sim2 quick 32)
- no LLM 0.7439 | writer+reader 0.7439 | reader only 0.7439. sim2 suppliers ignore wording, so this
  shows safety, not persuasion: writer 88/88 paragraphs accepted by the guard, median 3.3 s, max
  9.9 s; reader 80 calls, 1 timeout fallback, median 2.0 s. Kept on (neutral and safe); switch off
  with JT_NO_WRITER=1 / JT_NO_READER=1 / JT_NO_LLM=1. The real arena decides.
- Final rehearsal (real time, arena-sim, 2 waves, all cases parallel): passed twice in a row on
  2018fd2 (6/6 deals each, no timeouts).

## First real practice run (10 hosted cases, acc46bc, LLM on): mean 0.670
P01 1.000 · P03 0.936 · P04 0.860 · P05 0.834 · P07 0.714 · P10 0.694 · P11 0.710 · P18 0.485 ·
P08 0.471 · P02 0.000. No timeouts, no crashes, all claims verified. Fixes from `jt report`:
- P02 = 0: relationship charge 180 498. Our margin estimate counted the copper clause as cash; the
  arena counts clauses and contract length as security. `value(cash=True)` for the margin.
- P08/P18: stopped after a close-out while the distributor still conceded 5 %/round: keep going.
- P18: approval capped room at 4 % (it gave 13 %), and the anchor sat above its first counter
  (5.66 vs 5.70): no room cap; anchor >= expected first drop + 3 points.
- Full-cost prior without should-cost 0.92 x quote (real 0.91-0.995); gate also with
  max(1.04 x fc, 0.95 x quote) (stress found a deal above the alternative otherwise).
- sim2 after the fixes: 0.7507 (64, no LLM). Not yet re-run on the real arena.

## Real practice run 2 (6b9ce35): mean 0.742 (run 1 0.670)
P02 0.000 -> 0.854 (margin fix works). P08 0.471 -> 0.438, P18 0.485 -> 0.386: worse. Other cases
identical to the cent (suppliers answer identical sheets identically).
- Distributors: the old agent's best real sessions (P18 5.00, P08 5.24) used 36 months, an anchor
  ~21 % under the quote, steps of 4/2/1.5 %; its first counters came 5.30-5.52 vs our 5.70-5.90 at
  24 months. Changes: contract length valued 2 %/year for raw material (security, not cash: the
  margin ignores it), supplier-friendly tie-break at equal C (longer contract, more volume, later
  delivery, looser ppm, smaller chargeback), anchor share 0.55 for raw material, keep going after a
  close-out while it concedes >= 0.15 %, no hedge sheet 8 % under the main one.
- sim2 0.7507 -> 0.7661 (personas now value contract length for raw material too). Stress clean.

## Real practice run 3 (6d6082f, P04 P07 P08 P18)
P08 0.438 -> 0.626, P18 0.386 -> 0.542 (36 months: first counters 5.52/5.20 vs 5.86/5.90),
P04 0.860 =, P07 0.714 -> 0.707. Latest score per case over the 10 hosted: mean 0.776.
- P18: a held anchor got no move at all (5.20, 5.20, final). Hold only for founders now (it came
  from P05 Dralveck). sim2 0.7661 -> 0.7722.
- Relationship gate: pessimistic full cost >= 0.97 x quote and a 0.25 % cushion under the
  alternative (stress P04 variant: frozen supplier at its quote, true full cost ~0.99 x quote, deal
  237 EUR above the alternative). 0.99 refused good deals (stress -0.03); 0.97 clean on 3 seeds.
  sim2 0.7722 -> 0.7694 (only P04g: thin deal 0.184 -> walk 0.092).

## Lab `testes-consult-lab` (25 Oct, worktree ~/IdeaProjects/Janteiros-2.0-lab) - plan in docs/plano-lab.md
- Merge of testes-consult (a20e28c) + agente-v2 (235821c). New: RESCUE (never give up without a valid
  standing package; rescue before the ceiling sheet), MIRROR (send back its own cheaper alternative,
  by C), sim3 (OOD suppliers, independent judge), tail metrics in jt eval, 4-5 bidder clock test.
- GENERIC prior and ROBUST_FC measured worse / undecided offline: off unless JT_X_<NAME>=1.
- Offline (no LLM): arena-sim 0.708, sim2 0.775 (p10 0.373), sim3 v2 0.566 (p10 0.157), = lab base.
- Final rehearsal 1 on 9d7d6f6: 6/6 deals, 0 timeouts, 0 errors, 0 validator blocks.
- Final rehearsal 2 on 4a5cbee: 6/6 deals, 0 timeouts, 0 errors. Stress 200 (seed 3): 0.802, no problems.
- Real T1 (10208d8, no LLM): 0.7895 (main best 0.7946). P18 -0.067 from PROBE -> PROBE off (f118f3c);
  P08 = main with identical sheets; P02 +0.083 (wording). Next: P18 alone to confirm.
- Real P18 alone (ef5b11a, PROBE off, no LLM): 0.685 (T1 0.610, main best 0.677). Lab latest per case: mean 0.797.
- Head-to-head on the real arena, final config, 10 cases in parallel, run back to back (25 Oct ~02:12):
  lab 9c661bb 0.797 (10 deals, 0 timeouts) vs main 57683be 0.701 (9 deals). Lab >= main on every case:
  P02 +0.100, P07 +0.593, P08 +0.188, P18 +0.075, the rest equal. main P07: its message came late in
  round 3 (LLM writer ~6 s per call x 10 parallel cases), the supplier went final, main pushed again and
  it walked (0.121); main's own events counted 0 timeouts (the arena's trace shows the buyer timeout).
- Merged main 22f7c26 (release). Final candidate: tag final-lab. 115 tests, stress clean, rehearsal 6/6.
