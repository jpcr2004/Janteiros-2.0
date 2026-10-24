"""One writer and one reader call against Claude with a real supplier e-mail: latency, output,
guard verdict. Needs LLM_API_KEY in .env: uv run python scripts/llm_smoke.py"""

import time

from janteiros.cli import load_env

load_env()
from janteiros.llm import client  # noqa: E402
from janteiros.llm.reader import Reader  # noqa: E402
from janteiros.llm.writer import Writer  # noqa: E402
from janteiros.policy.base import Move  # noqa: E402
from janteiros.runner.state import Thread  # noqa: E402

BODY = (
    "Guten Tag,\nYour requested terms change our costs; our Angebot for them follows.\n"
    "Preis €139.80 per unit, Zahlungsziel 14 Tage netto, valid from 2027-04-01.\n"
    "LCI-C rose 2.7 % in twelve months and is 30 % of our cost. That remains.\n"
    "Faster payment helps our cash flow; that is reflected in the price.\nViele Grüße aus der Gießerei"
)
if not client.enabled():
    raise SystemExit("no LLM_API_KEY or JT_NO_LLM is set")
print("model", client.model(), "effort", client.effort())
t = Thread("sup:70110")
t.messages = [{"from": "sales@70110", "body": BODY, "status": "open", "term_sheets": []}]
for _ in range(3):
    t0 = time.monotonic()
    p = Writer().paragraph({"title": "Volute casings: a price increase"}, t, Move("send", tone="counter"), budget=20.0)
    print(f"writer {time.monotonic() - t0:5.1f}s ok={p is not None}: {p}")
    t0 = time.monotonic()
    r = Reader().read(BODY, timeout=8.0)
    print(f"reader {time.monotonic() - t0:5.1f}s: {r}")
print(client.STATS)
