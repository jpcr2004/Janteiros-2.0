# Janteiros 2.0

Buyer agent for the Tacto Negotiation Arena (team 🍜Janteiros).

## How it works

Janteiros is an AI buyer agent for the Tacto Negotiation Arena: it reads every supplier like a seasoned negotiator, prices every offer on total cost, and closes only deals that beat the alternative.

- **Prepares:** reads the case file and the data package plus the Sunday release (letters, news, scorecards, orders, should-cost, cost drivers, contracts) and builds a profile of each supplier: owner type, cash need, spare or full capacity, a frozen price list, approval ladders.
- **Decides what to offer:** every package of terms is priced with the arena's own total-cost function (`arena_kit.total_cost`). It anchors below the quote, then moves round by round from the supplier's own concessions, staying under its next counter. It trades what the supplier values (faster payment for a cash-short supplier, volume for spare capacity) and sends back a supplier's own cheaper alternative package when it has one. Up to three term sheets per e-mail, each argument a checkable fact with its data ID.
- **Closes or walks:** accepts the cheapest standing offer that beats our alternative on total cost. Otherwise it makes one last offer at our ceiling, then walks away. Without an alternative it first makes sure a valid offer is on the table.
- **Safety:** code writes every number; e-mails use fixed templates, and an optional LLM only labels supplier replies and never sets a price. A validator checks every message before it is sent. It plays up to 10 cases in parallel within the 60 s clock, resumes after a crash, and never reads the live feed or the leaderboard.
- **Kept general, not tuned to the practice cases:** no decision depends on a case or supplier ID. Every new rule sits behind its own switch. It is tested with 115 tests, 200 mutated cases, and an independent simulator (sim3) with 9 supplier behaviours.

## Install

```bash
uv sync
cp .env.example .env    # then fill in the values
uv run arena data --out data   # the data package (and the Sunday release once public)
```

Environment variables (in `.env` or the shell): `ARENA_URL`, `ARENA_API_KEY`, `ARENA_DATA`,
`LLM_MODEL` (default `claude-opus-5-5`), `LLM_API_KEY` (only this one; `ANTHROPIC_API_KEY` is ignored), `LLM_EFFORT`
(default `low`). Without an LLM key the agent plays with templates.

## Run

```bash
uv run jt run --set final --real          # Live Final: waits for the waves, plays every case
uv run jt play case:P01 --real            # one practice case on the arena
uv run jt play case:S01 --sim             # one case on a local arena-sim (localhost:8000)
uv run jt eval                            # offline benchmark (arena-sim + sim2), appends results/history.jsonl
uv run jt ui                              # monitor at http://localhost:8765
```

`--real` is required for any non-local arena. Tests: `uv run pytest`.

## Team Janteiros

- Andre Thiele
- Rafael Curi
- João Pedro Rezende

## Repository layout

| Folder | What is in it |
|---|---|
| `janteiros/` | The agent (Python package, command `jt`) |
| `janteiros/runner/` | Plays a case end to end: one arena session, 60 s clocks per supplier, keepalives, crash resume; `waves.py` runs the Live Final wave by wave |
| `janteiros/policy/` | The negotiator: anchor, moves, close-out, trades, rescue and mirror rules (`negotiator.py`), ask-driven trades (`trades.py`), the e-mail text (`compose.py`) |
| `janteiros/model/` | The supplier model: starting profile from the data (`profile.py`), live view of its concessions and floor (`supplier.py`), the evidence the LLM reader may see (`evidence.py`) |
| `janteiros/econ/` | Economics: our total cost C via the kit's `total_cost`, the supplier's value of a sheet, the optimiser over all allowed term packages |
| `janteiros/claims/` | The claim bank: checkable facts with their data IDs (cost drivers, should-cost, notice, forecast, scorecard, orders, rival offers) |
| `janteiros/world/` | Loads the data package, the Sunday release and the case's own facts into one index |
| `janteiros/validate/` | The validator every message passes before it is sent |
| `janteiros/arena/` | HTTP client for the arena (forbidden endpoints refused in code) and the quote-PDF reader |
| `janteiros/llm/` | Optional LLM writer (off by default) and reader (labels replies only) |
| `janteiros/events/` | The event log: every decision written to `runs/<id>/events.jsonl` |
| `janteiros/sim2/`, `janteiros/sim3.py` | Offline simulators: sim2 calibrated on practice, sim3 independent (9 supplier behaviours) |
| `janteiros/*.py` | `cli.py` (the `jt` command), `flags.py` (the `JT_X_*` switches), `evaluate.py` (`jt eval`), `report.py` (`jt report`), `ui_server.py` (`jt ui`) |
| `tests/` | 115 tests: economics, validator, safety, clocks, many bidders, release scenario, golden round-1 test, lab rules |
| `scripts/` | Tools: stress test (`stress.py`), Final rehearsal (`rehearse.sh`), LLM smoke test, golden-test recorder, simulator calibration |
| `data/` | Practice and sandbox case files (`cases/`), recorded paths for sim2 (`sim2/`); the data package and Sunday release are downloaded here (not in git) |
| `docs/` | Work and test plans of the lab branch (in Portuguese) |
| `results/` | `history.jsonl`: every evaluation and real-arena run with its commit and scores |
| `ui/` | The local monitor page served by `jt ui` |
| `.githooks/` | Pre-commit hook that blocks keys and `.env` files |
