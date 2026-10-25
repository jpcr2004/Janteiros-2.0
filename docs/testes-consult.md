# Branch `testes-consult`

This branch is an isolated consultation prototype.  It starts at the same commit as
`experimentos-final`; no live arena run was made while building it.

## Changes under test

- Supplier text is delivered to optional models with bounded, provenance-preserving session context.
- Reader classifications are qualitative: they cannot mutate rate, volume value, overtime or full cost.
- Supplier requests can generate one-field trade alternatives only when estimated supplier value is
  preserved and total buyer cost improves.
- Writer paragraphs that contain acceptance, award, guarantee or commitment language are rejected.
- Template text is the default.  An LLM paragraph requires `JT_LLM_WRITER=1`.

## Feature switches

- `JT_ASK_TRADES=0`: disable ask-driven trade alternatives.
- `JT_CONTEXT_SNAPSHOT=0`: disable delivered-history context for reader and writer.
- `JT_LLM_WRITER=1`: opt in to the optional non-binding paragraph.
- `JT_NO_READER=1`: disable the qualitative reader.
- `JT_NO_LLM=1` or `--no-llm`: disable every model call.

## Suggested manual sequence

Run these yourself from the repository after selecting this branch:

```bash
git switch testes-consult
uv sync
uv run pytest -q
uv run ruff check janteiros tests
uv run jt eval --quick --suite all --no-llm --note "testes-consult no-llm"
uv run jt eval --quick --suite all --note "testes-consult reader"
```

The second evaluation still uses template text unless `JT_LLM_WRITER=1` is explicitly set.

For a local single-case inspection:

```bash
uv run jt play case:S01 --sim --no-llm -v
uv run jt report
```

Do not use `--real` until the offline result and trace have been reviewed.
