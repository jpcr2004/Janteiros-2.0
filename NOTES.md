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

## agente-v2 (branch `agente-v2`, from baseline-r4 = main 57683be, real 0.7946)
Every change has a switch, on by default: `JT_X_<NAME>=0` turns one off, `JT_X_ALL=0` gives exactly
the baseline (tests/test_golden.py: round 1 on all 15 recorded real openings is byte-identical).
Runs record the switch state (`flags` in run_start and in history).

| Switch | What | Evidence |
|---|---|---|
| WRITER_GUARD | the writer gets the list of what our sheet changes; its paragraph may not accept/agree/commit/confirm/guarantee, promise exclusivity, or say the proposal "reflects/includes/addresses/took into account" anything | P11 r4: "we have tried to reflect your wish for faster payment in our proposal" (payment not negotiable) -> "you said your proposal includes payment terms and I don't see any", price frozen at 45.50 for 2 rounds; P02 same with volume. The guard blocks 28/185 of all real paragraphs sent so far, all of that kind |
| HONEST_ANSWERS | code answers: "Our need under this contract is N units a year" (not "all placed with you"); a payment ask on a fixed payment term gets "fixed at N days, not part of this negotiation"; "reflected in our proposal" only when our sheet does shorten payment | same P11/P02 traces |
| FORECAST | when a supplier asks to see the volume data, attach `demand-forecast.csv` once (latest forecast of the case's parts, monthly, from the data package, no IDs) + one fixed sentence | 5/10 r4 cases asked for it (P02, P07, P08, P11, P18); P07: "without this verified data, we can make a further step to 5.38" |
| READER_NO_ECON | the LLM reader's output is logged and kept as asks, never moves a number (rate, volume value, overtime) | zero measured gain; principle (supplier text should not set economics) |
| TWINS | in an RFQ, a bidder whose quote is within 3 % of the leader's is not pruned before its 3rd reply nor unless its floor is 1.5 % above the best standing sheet | announced for the final; sim2 twin variants (P05t/P06t/D02t x 5 seeds): prunes 5 -> 0, mean 0.878 -> 0.882, no case worse |
| PROBE | round 1: an extra sheet with one cash field (lead time, payment, quality) at the value best for us, priced to cost us the same C; plus every sheet must pass the validator's own C test before sending | P10 r4: c_best uses lead 42; our capacity model (overtime 0.45 %/day) kept 56 and paid 33 600 late cost |

Tried and dropped: full cost as 3 scenarios with minimax regret (E5). Against the true full cost of
D01/P01/P02/P03 its C-minimum price was worse on all four (P02 regret 13 318 vs 2 871 EUR).

Offline checks on 235821c (no LLM): 94 tests; stress 120 x seeds 1-4 clean (0.799 / 0.831 / 0.810 /
0.753, same as switches off); sim2 70 cases 0.7750, 0 crashes, 0 validator blocks (sim2 judges
bugs, not tactics). Writer replayed with the real LLM on 29 points of the r4 threads: 28 accepted,
median 3.2 s, max 6.8 s (with a "no mention of unchanged terms" rule: 9/29, dropped).
The real arena decides: see docs/plano-experimentos-final.md and the commands in the README.

## Supplier full cost (FC_V2, 7cdd61d +)
True full cost recovered from the relationship charge of 61 real deals (formula inverted; spread
<= 0.34 % per supplier): D01 0.909 x quote cash (no should-cost); P01/P02/P03 0.992/0.987/0.986 x
their own-plant should-cost. company.md: own-plant rows ~5 % error, cost-base rows 5-15 % plus a
country factor (AT/CH 1.0-1.1, CZ/PL/HU 0.8-0.85, TR 0.75, CN 0.7) - the old code ignored both.
- own row: fc = 0.988 x sc (leave-one-out error <= 0.55 %; old 0.98: -0.6 to -1.2 %);
- cost-base row: sc x country factor, averaged with 0.92 x quote;
- no should-cost: 0.92 x quote stays: no fixed ratio works (0.88-0.98 all leave >= 58 kEUR worst case);
- pessimistic gate: +2 % on an own-plant row (was +4 %, still 4x the observed error), +4 % else.
Regret of our C-minimum price under the true full cost (4 real suppliers): 8321 -> 2122 EUR.
sim2 stress (true fc drawn sc x U(0.95, 1.02) now, was U(0.97, 0.995): our own old guess) on 3
seeds: switches on 0.784/0.824/0.801 vs off 0.790/0.829/0.799. The loss sits in P01 variants, where
sim2's calibrated full cost (130.27) is 1 % under the real one (131.56), and one knife-edge variant
(P02x89: our C-minimum at its quote). Real data favour FC_V2; the arena (P01, P02, P03) decides.
