"""Every checkable claim for a supplier, with the exact figure the arena checks (from facts.json,
within its tolerance), ranked by what moved suppliers of this type. One claim per sentence, the
figure and its IDs in the same sentence, numbers written plainly. An ID is never cited twice in a
case (duplicate), a rising index never (it "confirms their case"). A rival's offer may go to every
other bidder, once per supplier per offer."""

import math

from ..world.index import month_diff

# What moved prices (learnings): should-cost, a falling driver, notice period, forecast and
# rival offers were taken into account; groups and distributors shrugged off scorecard and orders.
WEIGHT = {"rival": 1.0, "cost": 0.9, "driver": 0.85, "notice": 0.8, "forecast": 0.6, "scorecard": 0.5, "orders": 0.45}
COLD = {"group", "distributor"}  # "does not change our calculation" for sc: and po:


def pct(x):
    return f"{x:.1f}".replace("-0.0", "0.0")


def eur(x):
    return f"{x:.2f}"


class ClaimBank:
    def __init__(self, case, world):
        self.case, self.w = case, world
        self.used: set = set()  # IDs cited in this case (rival offers: (supplier, id))
        self.bank: dict = {}  # supplier -> [claim]
        self.sent: dict = {}  # supplier -> count of claims sent

    # -- building -----------------------------------------------------------------------------
    def prepare(self, sid, t):
        _, st = t.standing
        price = (st or {}).get("unit_price")
        prof = self.w.profile(sid, self.case)
        claims = []
        claims += self.drivers(prof)
        claims += self.should_cost(sid, prof, price)
        claims += self.notice(sid)
        claims += self.forecast()
        claims += self.scorecard(sid, prof)
        claims += self.orders(sid, price)
        owner = prof.get("owner_type")
        for c in claims:
            w = WEIGHT[c["kind"]]
            if owner in COLD and c["kind"] in ("scorecard", "orders"):
                w *= 0.4
            c["rank"] = round(w * c.get("strength", 1.0), 4)
        claims.sort(key=lambda c: -c["rank"])
        self.bank[sid] = claims
        self.sent.setdefault(sid, 0)
        # messages already sent (a resume): their IDs count as used
        for m in t.ours:
            for r in m.get("refs") or []:
                self.used.add(r)

    def summary(self, sid):
        return [{"kind": c["kind"], "sentence": c["sentence"], "rank": c["rank"]} for c in self.bank.get(sid, [])]

    def drivers(self, prof):
        """The largest fall of each of its cost drivers over any window of up to 12 months that
        ends within the last 12 months of the series."""
        out = []
        seen = set()
        for series, weight in sorted(prof.get("drivers") or [], key=lambda x: -x[1]):
            if not series or series in seen:
                continue
            seen.add(series)
            pts = self.w.series(series)
            if len(pts) < 2:
                continue
            last = pts[-1][0]
            best = None
            for j in range(len(pts)):
                mj, vj = pts[j]
                if month_diff(mj, last) > 10:  # safely inside "the last 12 months"
                    continue
                for i in range(j):
                    mi, vi = pts[i]
                    if month_diff(mi, mj) > 12 or vi <= 0:
                        continue
                    ch = (vj / vi - 1) * 100
                    if best is None or ch < best[0]:
                        best = (ch, mi, mj)
            if best and best[0] <= -1.0:
                ch, mi, mj = best
                a, b = f"idx:{series}@{mi}", f"idx:{series}@{mj}"
                if not (self.w.known(a) and self.w.known(b)):
                    continue
                out.append({"kind": "driver", "refs": [a, b], "strength": min(1.5, abs(ch) / 5 * (0.5 + weight)),
                            "sentence": f"{series} changed {pct(ch)}% from {mi} to {mj} ({a}, {b}).",
                            "figure": round(ch, 1)})  # fmt: skip
        return out[:2]

    def should_cost(self, sid, prof, price):
        part = (self.case.get("parts") or [None])[0]
        if not part or len(self.case.get("parts") or []) > 1:
            return []
        ref = f"cost:{part.split(':', 1)[1]}"
        f = self.w.fact(ref)
        if not f or f.get("unit_cost") is None or not price:
            return []
        uc = float(f["unit_cost"])
        if uc >= price:
            return []
        return [{"kind": "cost", "refs": [ref], "strength": min(1.5, (price - uc) / price * 10),
                 "sentence": f"Our should-cost estimate for this part is EUR {eur(uc)} per unit ({ref}).",
                 "figure": uc}]  # fmt: skip

    def notice(self, sid):
        doc = self.case.get("trigger_doc")
        if not doc:
            return []
        d = self.w.fact(doc) or {}
        given = d.get("days")
        if given is None or d.get("supplier") not in (None, sid):
            return []
        ctrs = [k for k, v in self.w.w.facts.items() if k.startswith("ctr:") and "#" not in k
                and isinstance(v, dict) and v.get("supplier") == sid and v.get("days")]  # fmt: skip
        ctrs += [k for k in self.case.get("refs") or [] if k.startswith("ctr:") and k not in ctrs and "#" not in k]
        for c in ctrs:
            need = (self.w.fact(c) or {}).get("days")
            if need and given < need:
                return [{"kind": "notice", "refs": [doc, c], "strength": 1.0,
                         "sentence": f"Your letter gives {int(given)} days of notice ({doc}), while our contract "
                         f"requires {int(need)} days ({c}).", "figure": int(given)}]  # fmt: skip
        return []

    def forecast(self):
        part = (self.case.get("parts") or [None])[0]
        if not part:
            return []
        pid = part.split(":", 1)[1]
        rows = sorted(k for k in list(self.w.w.facts) + list(self.w.case_facts) if k.startswith(f"fc:{pid}@"))
        if not rows:
            return []
        ref = rows[-1]
        ch = (self.w.fact(ref) or {}).get("change_pct")
        if ch is None or ch < 2:
            return []
        return [{"kind": "forecast", "refs": [ref], "strength": min(1.3, ch / 10),
                 "sentence": f"Our forecast for this part is up {pct(ch)}% ({ref}).", "figure": ch}]  # fmt: skip

    def scorecard(self, sid, prof):
        key = sid.split(":", 1)[1]
        rows = sorted(k for k in list(self.w.w.facts) + list(self.w.case_facts) if k.startswith(f"sc:{key}@"))
        if not rows:
            return []
        ref = rows[-1]
        f = self.w.fact(ref) or {}
        otd, ppm = f.get("otd_pct"), f.get("ppm")
        out = []
        if otd is not None and otd < 92:
            out.append({"kind": "scorecard", "refs": [ref], "strength": min(1.3, (95 - otd) / 10),
                        "sentence": f"Your on-time delivery was {pct(otd)}% over the last twelve months ({ref}).",
                        "figure": otd})  # fmt: skip
        elif ppm is not None and ppm > 800:
            out.append({"kind": "scorecard", "refs": [ref], "strength": min(1.3, ppm / 2000),
                        "sentence": f"Your quality was {int(round(ppm))} ppm over the last twelve months ({ref}).",
                        "figure": ppm})  # fmt: skip
        return out

    def orders(self, sid, price):
        part = (self.case.get("parts") or [None])[0]
        if not part or not price:
            return []
        rows = [(k, v) for k, v in self.w.w.facts.items() if k.startswith("po:") and isinstance(v, dict)
                and v.get("supplier") == sid and v.get("part") == part and v.get("unit_price")]  # fmt: skip
        if len(rows) < 2:
            return []
        rows.sort(key=lambda kv: kv[1].get("date") or "")
        last = rows[-1][1]["unit_price"]
        same = [k for k, v in rows if v["unit_price"] == last][-3:]
        if len(same) < 2 or last >= price:
            return []
        refs = sorted(same)
        return [{"kind": "orders", "refs": refs, "strength": min(1.2, (price - last) / price * 8),
                 "sentence": f"Our last {len(refs)} orders for this part were at EUR {eur(last)} per unit "
                 f"({', '.join(refs)}).", "figure": last}]  # fmt: skip

    def rival(self, t, threads):
        """The cheapest rival offer in this session below this supplier's standing price."""
        _, st = t.standing
        price = (st or {}).get("unit_price")
        best = None
        for o in threads.values():
            if o is t or not o.live:
                continue
            oid, terms = o.standing
            p = (terms or {}).get("unit_price")
            if oid and p and price and p < price and (t.sid, oid) not in self.used:
                if best is None or p < best[1]:
                    best = (oid, p)
        if not best:
            return None
        oid, p = best
        return {"kind": "rival", "refs": [oid], "rank": 1.0, "rival": True,
                "sentence": f"Another supplier offers EUR {eur(p)} per unit for this part ({oid}).", "figure": p}  # fmt: skip

    # -- choosing -----------------------------------------------------------------------------
    def next(self, t, threads, round_=1):
        """Claims for this message: up to 2 in round 1, then 1 per round, best first; in an RFQ
        the cheapest rival offer each round. Never more than 3, never a used ID."""
        out = []
        if (r := self.rival(t, threads)) is not None:
            out.append(r)
        n = 2 if round_ == 1 else 1
        for c in self.bank.get(t.sid, []):
            if len(out) >= min(3, n + (1 if out else 0)):
                break
            if any(ref in self.used for ref in c["refs"]):
                continue
            out.append(c)
        for c in out:
            for ref in c["refs"]:
                self.used.add((t.sid, ref) if c.get("rival") else ref)
        self.sent[t.sid] = self.sent.get(t.sid, 0) + len(out)
        return [{k: v for k, v in c.items()} for c in out]


def figure_ok(asserted, actual, kind):
    """The arena's tolerance: +-0.5 pp for %, else +-0.5 % (at least 0.01)."""
    if asserted is None or actual is None:
        return False
    if kind in ("driver", "forecast", "scorecard_pct"):
        return abs(asserted - actual) <= 0.5
    return abs(asserted - actual) <= max(0.01, abs(actual) * 0.005) or math.isclose(asserted, actual)
