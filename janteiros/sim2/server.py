"""sim2: the kit's arena-sim API (same paths, fields, statuses, errors, clocks, PDFs: it subclasses
`arena_kit.sim.Sim`) with calibrated supplier personas instead of the one fixed script, and a
seeded benchmark of the practice and sandbox case files plus synthetic variants.

    jt sim2 --port 8001 --seed 1          (then ARENA_URL=http://localhost:8001)
"""

import copy
import json
import math
import random
from pathlib import Path

from arena_kit.sim import Sim, create_app, structured

from ..econ.cost import breakdown, c_alt, closing, cost, musts_ok
from ..econ.supplier import value
from . import judge
from .persona import CAPS, CLAIM_EFFECT, best_c_at_floor, build

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "data" / "cases"
PATHS = ROOT / "data" / "sim2" / "paths.json"
DELAY = {"founder": 4, "family": 2, "private": 2, "group": 1, "distributor": 1}


class Sim2(Sim):
    def __init__(self, cases, facts, world, seed=1, recorded=None, clock=None, no_clock=False):
        import time

        super().__init__(cases, facts, clock=clock or time.time, no_clock=no_clock)
        self.world, self.seed, self.recorded = world, seed, recorded or {}
        self.personas = {}  # (session id, supplier) -> Persona

    def case_for(self, cid):
        return self.cases[cid]

    # -- opening -------------------------------------------------------------------------------
    def open(self, team, case_id):
        r = super().open(team, case_id)
        s = self.sessions[r["session_id"]]
        case = s["case"]
        cw = self.world.for_case(case) if self.world else None
        base = case.get("base_case_id") or case["case_id"]
        for sup, th in s["threads"].items():
            rng = random.Random(f"{self.seed}|{case['case_id']}|{sup}")
            rec = self.recorded.get(f"{base}|{sup}") if not case.get("synthetic_suppliers") else None
            if rec and case.get("goal_variant"):  # its paths still fit; its c_best belongs to the base goal
                rec = {"sessions": [{**x, "c_best": None} for x in rec["sessions"]]}
            p = build(case, sup, cw, rng, rec)
            self.personas[(s["id"], sup)] = p
            th["standard"] = dict(p.standard)
            th["opening"] = p.quote["unit_price"]
            th["status"], th["stated"] = "open", dict(p.quote)
            msg = th["messages"][0]
            s["offers"].pop(msg["term_sheets"][0]["term_sheet_id"], None)
            s["offers"][th["offer_id"]] = dict(p.quote)
            msg["term_sheets"] = [{"term_sheet_id": th["offer_id"], "terms": dict(p.quote)}]
            msg["missing_fields"] = sorted(set(case["negotiable"]) - set(p.quote))
            msg["signals"] = {"reply_delay_days": DELAY.get(p.owner, 2), "validity_days": 30}
            msg["body"] = self.quote_text(case, p)
            msg["from"] = f"sales@{th['key']}"
            for att in msg["attachments"]:
                self.files_patch(s, att, th)
        ps = [self.personas[(s["id"], sup)] for sup in s["threads"]]
        target = min((p.recorded_c_best for p in ps if p.recorded_c_best), default=None)
        if len(ps) > 1 and target and target < c_alt(case) - 1:
            fit_rfq(case, ps, target)
        r["opening"] = {k: structured(th["messages"][0], 0) for k, th in s["threads"].items()}
        r["threads"] = {k: list(th["messages"]) for k, th in s["threads"].items()}
        return r

    def files_patch(self, s, att, th):
        kind, _, _ = s["files"][att["attachment_id"]]
        s["files"][att["attachment_id"]] = (kind, dict(th["stated"]), dict(th["standard"]))

    def quote_text(self, case, p):
        lines = ["Dear Sir or Madam,", "", "thank you for your enquiry. Please find our quote attached."]
        if "payment" in p.asks:
            lines.append("Faster payment would help our cash flow.")
        if p.frozen:
            lines.append("Our price list applies to all customers; there is no room on price.")
        if p.distributor:
            lines.append("As a distributor we price alloy at the mill price and follow the index.")
        lines += ["", "Kind regards", "Sales"]
        return "\n".join(lines)

    # -- the persona answers -------------------------------------------------------------------
    def _answer(self, s, th, msg, sheets):
        case = s["case"]
        p = self.personas[(s["id"], f"sup:{th['key']}")]
        offers = {k: {"unit_price": v.get("unit_price")} for k, v in s["offers"].items()}
        quotes = [r for r in case.get("refs") or [] if r.startswith("quote:")]
        alt_ref = (case["buyer"].get("alternative") or {}).get("ref")
        if alt_ref:
            quotes.append(alt_ref)
        facts = {**self.facts, **(case.get("facts") or {})}
        results = judge.check(msg.get("body", ""), msg.get("refs", []), facts, offers, f"sup:{th['key']}", s["used"],
                              quotes)  # fmt: skip
        price = th["stated"]["unit_price"]
        cold = p.owner in ("group", "distributor")
        bonus, words = 0.0, []
        for res in results:
            if res["result"] == "verified":
                eff = CLAIM_EFFECT.get(res["kind"], (0.0, 0.0))[1 if cold else 0]
                if p.calibrated:
                    eff *= 0.3  # its recorded first drops already carry our old claims
                if res["kind"] == "driver" and res.get("figure") is not None and res["figure"] > 0:
                    eff = 0.0
                    words.append("Your figures confirm our case.")
                elif res["kind"] == "rival" and res.get("figure"):
                    gap = max(0.0, (price - res["figure"]) / price)
                    eff = min(eff, 0.5 * gap)
                bonus += eff
                words.append("We checked the data you cited; it holds and we took it into account." if eff
                             else "We checked the data you cited; it holds, but it does not change our calculation.")  # fmt: skip
            elif res["result"] == "contradicted":
                p.cum_gap += 0.04
                words.append("The figures you cite do not match our records.")
        if p.frozen:
            bonus = 0.0
        out = [{"index": r["index"], "result": r["result"]} for r in results]
        if not sheets:
            reply = self._mail(s, th, th["status"], th["stated"], self.words(p, "question", words))
            return self.finish_reply(s, th, reply, out, p)
        vals = []
        for sh in sheets:
            v = value(case, sh, p.est)
            if not p.accepts(case, sh):
                v -= 0.05 * price  # terms it refuses: worth much less to it
            vals.append((v, sh))
        best_v, best_sheet = max(vals, key=lambda x: x[0])
        noise = 1 + p.rng.uniform(-p.noise, p.noise)
        rnd = th["round"]
        if p.status == "final_offer":
            if best_v >= p.standing_v - 1e-9 and p.accepts(case, best_sheet):
                return self.agree(s, th, p, best_sheet, best_v, out, words)
            if best_v < p.standing_v - 0.0005 * price and p.rng.random() < p.walk_p:
                th["closed"] = "walked_away"
                reply = self._mail(s, th, "walked_away", th["stated"], self.words(p, "walk", words))
                return self.finish_reply(s, th, reply, out, p)
            reply = self._mail(s, th, "final_offer", th["stated"], self.words(p, "final", words), ["quote.pdf"])
            return self.finish_reply(s, th, reply, out, p)
        if p.last_ours_v is None:
            conc = p.first_drop * price * noise + bonus * price
        else:
            move = best_v - p.last_ours_v
            if move <= 0.001 * price:
                p.stall += 1
                conc = bonus * price
            else:
                cap = CAPS[min(len(CAPS) - 1, max(0, rnd - 2))] * p.cap_scale * price * noise
                conc = min(p.ratio * move, cap) + bonus * price
        if p.frozen:
            conc = 0.0
        v_next = max(p.floor_v, p.standing_v - conc)
        p.cum_gap += max(0.0, (p.standing_v - best_v) / price)
        p.last_ours_v = best_v
        keen = p.owner == "founder" and rnd == 2 and best_v >= p.floor_v and p.rng.random() < 0.5
        if (best_v >= v_next - 1e-9 or keen) and p.accepts(case, best_sheet):
            return self.agree(s, th, p, best_sheet, best_v, out, words)
        terms = p.fix_terms(case, best_sheet)
        new_price = p.price_at(case, terms, v_next)
        if not new_price:
            terms, new_price = dict(th["stated"]), price
        stated = {**terms, "unit_price": new_price}
        if value(case, stated, p.est) > p.standing_v + 1e-6:  # never worse for it than before
            stated, v_next = dict(th["stated"]), p.standing_v
        p.standing_v = value(case, stated, p.est)
        final = v_next <= p.floor_v + 1e-6 or rnd >= p.final_round or p.cum_gap >= p.patience or p.stall >= 2
        status = "final_offer" if final else "open"
        p.status = status
        files = ["quote.pdf"] if final else []
        reply = self._mail(s, th, status, stated, self.words(p, status, words), files)
        return self.finish_reply(s, th, reply, out, p)

    def agree(self, s, th, p, sheet, v, out, words):
        p.status, p.standing_v = "agreed", v
        reply = self._mail(s, th, "agreed", dict(sheet), self.words(p, "agreed", words), ["quote.pdf"])
        return self.finish_reply(s, th, reply, out, p)

    def words(self, p, status, extra):
        lines = ["Dear Sir or Madam,", ""] + extra
        if status == "agreed":
            lines.append("We can accept your terms and await your order.")
        elif status == "final_offer":
            lines.append("This is our final offer; we cannot go further.")
        elif status == "walk":
            lines.append("We are not able to continue on this basis and withdraw our offer.")
        elif status == "open":
            if "payment" in p.asks and p.rng.random() < 0.5:
                lines.append("Faster payment would help our cash flow; that is reflected in the price.")
            if "volume" in p.asks and p.rng.random() < 0.4:
                lines.append("A binding higher volume would help us. Which volume can you commit to?")
            lines.append("Our counter-offer follows.")
        else:
            lines.append("Our offer stands.")
        lines += ["", "Kind regards", "Sales"]
        return "\n".join(lines)

    def finish_reply(self, s, th, reply, out, p):
        reply["claim_results"] = out
        reply["signals"] = {"reply_delay_days": DELAY.get(p.owner, 2), "validity_days": 30}
        s["trace"].append({"round": th["round"], "actor": f"sup:{th['key']}", "move": structured(reply, th["round"]),
                           "reasons": [f"persona {p.owner}: cum gap {p.cum_gap:.2f} of {p.patience:.2f}"]})  # fmt: skip
        return reply

    # -- the result ----------------------------------------------------------------------------
    def _end(self, s, reason, sup):
        case, ths = s["case"], s["threads"]
        ps = {k: self.personas[(s["id"], k)] for k in ths}

        def c(terms, std, p=None):
            return cost(case, terms, std, p.est if p is not None else None)

        opens = [c(p.quote, p.standard, p) for p in ps.values() if musts_ok(case, p.quote, p.standard)]
        c_open = min(opens) if opens else min(c(p.quote, p.standard, p) for p in ps.values())
        alt = c_alt(case)
        c_best = min([alt] + [best_c_at_floor(case, p) for p in ps.values()])
        r = {"ended": True, "deal": False, "supplier_id": None, "offer_id": None, "terms": None, "c_open": c_open,
             "c_deal": None, "c_alt": alt if math.isfinite(alt) else None, "c_best": c_best, "must_haves_ok": None,
             "cost_breakdown": None, "trace": []}  # fmt: skip
        r["score"] = score(c_open, alt, c_best) if math.isfinite(alt) else 0.0
        if sup:
            p = ps[sup]
            t = closing(case, ths[sup]["stated"], ths[sup]["standard"])
            r.update(deal=True, supplier_id=sup, offer_id=ths[sup]["offer_id"], terms=t, c_deal=c(t, {}, p),
                     must_haves_ok=musts_ok(case, t), cost_breakdown=breakdown(case, t, {}, p.est))  # fmt: skip
            r["score"] = score(c_open, r["c_deal"], c_best) if r["must_haves_ok"] else 0.0
        s.update(status="ended", end_reason=reason, result=r)
        for th in ths.values():
            th["closed"] = th["closed"] or reason


def fit_rfq(case, ps, target):
    """RFQ: scale every bidder's floor by one factor so the best of them meets the arena's c_best."""
    base = [p.floor_v for p in ps]
    lo, hi = 0.6, 1.1
    for _ in range(30):
        f = (lo + hi) / 2
        for p, b in zip(ps, base, strict=True):
            p.floor_v = b * f
        if min(best_c_at_floor(case, p) for p in ps) < target:
            lo = f
        else:
            hi = f
    for p, b in zip(ps, base, strict=True):
        p.floor_v = b * (lo + hi) / 2
        p.est.full_cost = min(p.est.full_cost, p.floor_v * 0.995) if p.est.full_cost else p.est.full_cost


def score(c_open, c_deal, c_best):
    if c_deal is None or not math.isfinite(c_deal):
        return 0.0
    if c_open - c_best <= 0.01:
        return 1.0 if c_deal <= c_best + 0.01 else 0.0
    return min(1.0, max(0.0, (c_open - c_deal) / (c_open - c_best)))


def base_cases():
    out = {}
    for f in ("practice", "sandbox"):
        for c in json.loads((CASES / f"{f}.json").read_text(encoding="utf-8")):
            out[c["case_id"]] = c
    return out


def variants(cases, world):
    """New suppliers and other goals on the same case structures (hidden cases repeat no
    practice case: mostly new suppliers, same world and rules)."""
    out = {}
    sups = world.w.table("suppliers") if world else []
    by_cat = {}
    for r in sups:
        if r.get("detail") == "full":
            by_cat.setdefault(r.get("category"), []).append(r["id"])
    rng = random.Random(7)
    for cid in ("case:P01", "case:P03", "case:P04", "case:P07", "case:P11", "case:P13", "case:P16", "case:P10"):
        base = cases.get(cid)
        if not base:
            continue
        sid = base["suppliers"][0]
        cat = next((r.get("category") for r in sups if r["id"] == sid), None)
        pool = [x for x in by_cat.get(cat, []) if x != sid]
        if not pool:
            continue
        new = rng.choice(pool)
        c = copy.deepcopy(base)
        c.update(case_id=f"{cid}n", base_case_id=cid, suppliers=[new], trigger_doc=None, synthetic_suppliers=True,
                 refs=[r for r in base.get("refs") or [] if not r.startswith(("doc:", "ctr:", "sc:"))],
                 title=base["title"] + " (new supplier)")  # fmt: skip
        out[c["case_id"]] = c
    for cid, goal in (("case:P04", "stabilise"), ("case:P08", "routine"), ("case:P05", "secure_supply")):
        base = cases.get(cid)
        if not base:
            continue
        c = copy.deepcopy(base)
        b = c["buyer"]
        b["category_goal"] = goal
        b["fallback_risk_pct"] = {"stabilise": 10, "secure_supply": 25, "routine": 5}[goal]
        if goal in ("stabilise", "secure_supply"):
            b["sustainable_margin_pct"] = 4.0 if goal == "stabilise" else 6.0
            b["relationship_eur_per_point"] = round(
                0.012 * b["need_per_year"] * (b.get("current_unit_price") or b["fallback_unit_price"]), 0
            )
        else:
            b.pop("sustainable_margin_pct", None)
            b.pop("relationship_eur_per_point", None)
        c.update(case_id=f"{cid}g", base_case_id=cid, title=base["title"] + f" ({goal})", goal_variant=True)
        out[c["case_id"]] = c
    return out


def load_world():
    from ..world.index import World

    return World.load(ROOT / "data" / "package")


def build_sim(seed=1, quick=False, clock=None, no_clock=False):
    world = load_world()
    cases = base_cases()
    cw = world.for_case({"parts": []})
    allc = {**cases, **variants(cases, cw)}
    seeds = [1] if quick else [1, 2]
    suite = {}
    for s in seeds:
        for cid, c in allc.items():
            cc = copy.deepcopy(c)
            cc.setdefault("base_case_id", c.get("base_case_id") or cid)
            if s != 1:
                cc["case_id"] = f"{cid}~{s}"
            suite[cc["case_id"]] = cc
    recorded = json.loads(PATHS.read_text()) if PATHS.is_file() else {}
    sim = Sim2(suite, world.facts, world, seed=seed, recorded=recorded, clock=clock, no_clock=no_clock)
    return sim


def suite(seed=1, quick=False):
    """(sim, app, [(case_id, seed)]) for the in-process benchmark (a frozen clock)."""
    sim = build_sim(seed, quick, clock=lambda: 1_000_000.0)
    return sim, create_app(sim), [(cid, seed) for cid in sorted(sim.cases)]


def serve(port=8001, seed=1, no_clock=False):
    import uvicorn

    sim = build_sim(seed, no_clock=no_clock)
    print(f"sim2: {len(sim.cases)} cases, seed {seed}, http://localhost:{port}", flush=True)
    uvicorn.run(create_app(sim), host="127.0.0.1", port=port)
