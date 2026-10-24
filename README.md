# Janteiros 2.0

Buyer agent for the Tacto Negotiation Arena (team 🍜Janteiros).

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
