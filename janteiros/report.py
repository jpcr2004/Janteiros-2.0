"""jt report [run_id]: a diagnosis of a run for tuning. Reads only our event file and the arena
sessions saved next to it (runs/<run>/sessions/). Per case: the price path, what each claim got,
why we stopped and closed, the supplier's reasons in words, what was left on the table; across
cases: what the suppliers did against our priors (first drop, concession ratio, patience).
Writes runs/<run>/report.md and prints it."""

import json
import statistics
from pathlib import Path

from .events.log import RUNS, read_events


def latest_run(modes=("play", "run")):
    for p in sorted(RUNS.glob("*/events.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            first = json.loads(p.open(encoding="utf-8").readline())
        except ValueError:
            continue
        if first.get("mode") in modes:
            return p.parent.name
    return None


def fmt(x, d=2):
    return "–" if x is None or x != x else f"{x:,.{d}f}".replace(",", " ")


def pct(x, d=1):
    return "–" if x is None or x != x else f"{100 * x:.{d}f} %"


def cases_of(events):
    out = {}
    for e in events:
        cid = e.get("case_id")
        if not cid or e["kind"] == "http":
            continue
        out.setdefault(cid, []).append(e)
    return out


def thread_paths(evs):
    paths = {}
    for e in evs:
        sid = e.get("supplier_id")
        if not sid:
            continue
        p = paths.setdefault(sid, {"steps": [], "plan": None, "stop": None, "claims": [], "model": {}})
        if e["kind"] == "plan" and e.get("round") is None:
            p["plan"] = e
        elif e["kind"] == "quote":
            p["steps"].append(("quote", (e.get("terms") or {}).get("unit_price"), e.get("c"), e.get("status"), None))
        elif e["kind"] == "send":
            prices = [s.get("unit_price") for s in e.get("sheets") or []]
            cs = [c for c in e.get("c") or [] if c is not None]
            p["steps"].append(("us", prices, min(cs) if cs else None, e.get("reason"), e.get("claims")))
            for c in e.get("claims") or []:
                p["claims"].append([c, None])
        elif e["kind"] == "reply":
            p["steps"].append(("them", (e.get("terms") or {}).get("unit_price"), e.get("c"), e.get("status"),
                               e.get("claim_results")))  # fmt: skip
            res = e.get("claim_results") or []
            pending = [x for x in p["claims"] if x[1] is None]
            for r, x in zip(res, pending, strict=False):
                x[1] = r.get("result")
            for x in pending[len(res) :]:
                x[1] = "not read"
        elif e["kind"] == "model_update":
            p["model"].update(
                {k: v for k, v in e.items() if k in ("ratio", "cum_gap", "floor_c", "concessions", "rate")}
            )
            if e.get("stop"):
                p["stop"] = e["stop"]
    return paths


def diagnose(run_id):
    events = read_events(run_id)
    sessions = {}
    for f in sorted((RUNS / run_id / "sessions").glob("*.json")) if (RUNS / run_id / "sessions").is_dir() else []:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            sessions[(d.get("session") or {}).get("session_id")] = d
        except ValueError:
            continue
    by_case = cases_of(events)
    lines = [f"# Run {run_id}", ""]
    first = events[0] if events else {}
    lines += [f"commit {first.get('commit')} · mode {first.get('mode')} · arena {first.get('arena')}", ""]
    rows, obs = [], []
    detail = []
    for cid, evs in by_case.items():
        res = next((e.get("result") for e in reversed(evs) if e["kind"] == "result"), None) or {}
        reasons = next((e.get("reasons") for e in reversed(evs) if e["kind"] == "result"), None)
        opened = next((e for e in evs if e["kind"] == "case_open"), {})
        case = opened.get("case") or {}
        sid_session = opened.get("session_id")
        timeouts = sum(e["kind"] == "timeout" for e in evs)
        blocks = sum(e["kind"] == "validate" and not e.get("ok") for e in evs)
        errors = [e for e in evs if e["kind"] == "error"]
        llm = [e for e in evs if e["kind"] == "llm_call"]
        span = evs[-1]["t"] - evs[0]["t"] if evs else 0
        c_open, c_best, c_deal = res.get("c_open"), res.get("c_best"), res.get("c_deal")
        left = (c_deal - c_best) if (c_deal is not None and c_best is not None) else None
        rows.append((cid, case.get("type"), (case.get("buyer") or {}).get("category_goal"), res.get("score"),
                     res.get("deal"), res.get("rounds"), timeouts, span, left))  # fmt: skip
        d = [f"## {cid} · {case.get('title', '')}", ""]
        d.append(f"{case.get('type')} · {(case.get('buyer') or {}).get('category_goal')} · session {sid_session} · "
                 f"{span:.0f} s · score **{fmt(res.get('score'), 3)}** · {'deal with ' + str(res.get('supplier_id')) if res.get('deal') else 'NO DEAL (' + str(res.get('end_reason')) + ')'}")  # fmt: skip
        d.append("")
        d.append(f"C_open {fmt(c_open, 0)} · C_deal {fmt(c_deal, 0)} · C_best {fmt(c_best, 0)} · C_alt {fmt(res.get('c_alt'), 0)} · "
                 f"left on the table vs C_best: {fmt(left, 0)} EUR")  # fmt: skip
        if res.get("terms"):
            d.append(f"deal terms: `{json.dumps(res['terms'])}`")
        if res.get("cost_breakdown"):
            b = {k: round(v) for k, v in res["cost_breakdown"].items() if abs(v) >= 1}
            d.append(f"cost breakdown: `{json.dumps(b)}`")
        flags = []
        if timeouts:
            flags.append(f"{timeouts} timeout(s)")
        if blocks:
            flags.append(f"{blocks} validator block(s)")
        if errors:
            flags.append(
                "errors: "
                + "; ".join(f"{e.get('where')}: {str(e.get('error') or e.get('detail'))[:80]}" for e in errors[:3])
            )
        if llm:
            fb = sum(bool(e.get("fallback")) for e in llm)
            flags.append(f"LLM {len(llm)} calls, {fb} fallbacks, {sum(e.get('seconds') or 0 for e in llm):.0f} s")
        for sid, p in thread_paths(evs).items():
            plan = p["plan"] or {}
            d += [
                "",
                f"### {sid}  ({plan.get('owner')}, traits {', '.join(plan.get('traits') or []) or '–'}, prior room {pct(plan.get('room'))}, first drop {pct(plan.get('first_drop'))}, patience {fmt(plan.get('patience'))})",
                "",
            ]
            d.append("| step | price(s) | our C | status / why |")
            d.append("|---|---|---|---|")
            quote = None
            them_prices = []
            for kind, price, c, what, _extra in p["steps"]:
                if kind == "quote":
                    quote = price
                if kind in ("quote", "them"):
                    them_prices.append(price)
                pr = ", ".join(fmt(x) for x in price) if isinstance(price, list) else fmt(price)
                d.append(f"| {kind} | {pr} | {fmt(c, 0)} | {what or ''} |")
            if p["stop"]:
                d.append(f"\nstopped: {p['stop']}")
            if p["claims"]:
                d.append("\nclaims: " + "; ".join(f"{(c or '')[:70]}… → **{r}**" for c, r in p["claims"]))
            m = p["model"]
            if m:
                d.append(
                    f"\nmodel: ratio {fmt(m.get('ratio'))}, patience used {fmt(m.get('cum_gap'))}, concessions {m.get('concessions')}"
                )
            if quote and len(them_prices) >= 2 and them_prices[1]:
                fd = 1 - them_prices[1] / quote
                total = 1 - min(x for x in them_prices if x) / quote
                obs.append({"type": case.get("type"), "owner": plan.get("owner"), "first_drop": fd, "total": total,
                            "prior_fd": plan.get("first_drop"), "prior_room": plan.get("room"),
                            "cum_gap": m.get("cum_gap"), "patience": plan.get("patience"),
                            "end": next((s[3] for s in reversed(p["steps"]) if s[0] == "them"), None)})  # fmt: skip
            us = [s for s in p["steps"] if s[0] == "us"]
            them = [s for s in p["steps"] if s[0] == "them"]
            if us and them and them[0][3] == "agreed":
                flags.append(f"{sid} agreed to our FIRST sheet: the anchor was above its first counter (anchor deeper)")
            if any(s[3] == "walked_away" for s in them):
                flags.append(f"{sid} WALKED: patience spent (check cum gap and anchor)")
            if any(s[3] == "final_offer" for s in them) and len(them) <= 2:
                flags.append(f"{sid} went final early (round {len(them)})")
        if flags:
            d += ["", "**Flags:** " + " · ".join(flags)]
        if reasons:
            d += ["", "**The supplier's reasons (trace):**"]
            for r in reasons:
                d.append(
                    f"- {r.get('actor')} r{r.get('round')} {r.get('status')}: " + " ".join(r.get("reasons") or [])[:400]
                )
        bo = (sessions.get(sid_session) or {}).get("backoffice")
        if bo:
            d += ["", "**Back office:**"]
            for x in bo[:25]:
                d.append(f"- r{x.get('round')} {x.get('from')} → {x.get('to')}: {str(x.get('text'))[:300]}")
        detail += d + [""]
    scores = [r[3] for r in rows if r[3] is not None]
    lines += [
        "| case | type | goal | score | deal | rounds | timeouts | seconds | EUR above C_best |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for cid, typ, goal, sc, deal, rnd, to, span, left in rows:
        lines.append(
            f"| {cid} | {typ} | {goal} | {fmt(sc, 3)} | {'yes' if deal else 'NO'} | {rnd} | {to} | {span:.0f} | {fmt(left, 0)} |"
        )
    if scores:
        lines += ["", f"**mean score {statistics.mean(scores):.3f}** over {len(scores)} cases", ""]
    if obs:
        lines += ["## What the suppliers did against our priors", "",
                  "| type | owner | first drop seen / prior | total concession seen / prior room | patience used / prior | ended |",
                  "|---|---|---|---|---|---|"]  # fmt: skip
        for o in obs:
            lines.append(f"| {o['type']} | {o['owner']} | {pct(o['first_drop'])} / {pct(o['prior_fd'])} | {pct(o['total'])} / {pct(o['prior_room'])} | "
                         f"{fmt(o['cum_gap'])} / {fmt(o['patience'])} | {o['end']} |")  # fmt: skip
        lines += ["", "Tuning hints:"]
        for o in obs:
            if o["prior_fd"] is not None and o["first_drop"] > o["prior_fd"] + 0.03:
                lines.append(
                    f"- {o['type']}/{o['owner']}: first counter {pct(o['first_drop'])} deeper than our prior {pct(o['prior_fd'])}: anchor deeper (TYPE first drop in model/profile.py)"
                )
            if o["prior_room"] is not None and o["total"] > o["prior_room"] + 0.03:
                lines.append(
                    f"- {o['type']}/{o['owner']}: gave {pct(o['total'])}, more than our prior room {pct(o['prior_room'])}: raise TYPE_ROOM / OWNER_ROOM"
                )
            if (
                o["end"] in ("final_offer", "walked_away")
                and o["cum_gap"] is not None
                and o["patience"]
                and o["cum_gap"] < o["patience"] * 0.6
            ):
                lines.append(
                    f"- {o['type']}/{o['owner']}: ended {o['end']} at patience {fmt(o['cum_gap'])}, under our prior {fmt(o['patience'])}: lower its patience prior"
                )
        lines.append("")
    text = "\n".join(lines + detail)
    out = RUNS / run_id / "report.md"
    out.write_text(text, encoding="utf-8")
    return text, out


def main(run_id=None):
    run_id = run_id or latest_run()
    if not run_id or not (Path(RUNS) / run_id / "events.jsonl").is_file():
        print("no run found (play or run a case first)")
        return 2
    text, out = diagnose(run_id)
    print(text)
    print(f"\n(saved to {out})")
    return 0
