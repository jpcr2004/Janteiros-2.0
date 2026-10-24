"""The deterministic per-thread planner. Moves are sized in the supplier's own value (what it
compares), sheets are chosen by our C (what scores):

- round 1: offer a value below its quote by the room its type usually gives (the anchor);
- each round: move our value toward its standing value by a share of the gap, never as far as the
  counter it would make next (it would answer `agreed` at our price, which then freezes); hold
  once after a deep first drop;
- at that value the optimiser picks the package that costs us least; a second sheet on its own
  terms at the same C hedges our value model and shows what it prefers;
- a frozen supplier gets packages worth the same to it but cheaper for us (a trade on terms);
- the close-out: in round 5, or when its concessions get small or its patience runs low, offer its
  predicted next counter on our best terms: it agrees with our terms instead of its standard ones;
- stop when the gap is small, its floor is reached, or (relationship goals) its standing sheet
  reaches our C-minimum; the close takes the cheapest standing sheet that passes the gate; RFQ
  bidders that cannot beat the best standing sheet are pruned."""

import math
import re

from ..econ.cost import c_alt, cost, gate, musts_ok, price_for_cost, price_slope, relationship_goal
from ..econ.optimise import Package, at_cost, at_value, relationship_optimum
from ..econ.supplier import make_est, value
from ..econ.terms import complete, same_terms
from ..model.profile import prior
from ..model.supplier import next_concession, view
from .base import Move, Policy

ASK_PAYMENT = re.compile(r"faster payment|pay(ing)? earlier|shorter payment|payment (term|target)s? of|cash", re.I)
ASK_VOLUME = re.compile(r"volume|commit", re.I)
ASK_FORECAST = re.compile(r"forecast", re.I)
MIN_MOVE = 0.0015  # our smallest move, share of price (a move under 0.1 % made suppliers go final)
STAND_OFF = 0.002  # stay this far under its standing price
STOP_GAP = 0.004  # accept its standing sheet once our gap is under this share of price
CLOSEOUT_ROUND = 5  # by then suppliers go final: offer its next counter on our best terms
KEEP_OURS = 3  # rounds we keep our structure when it substitutes its own terms
STEP = 0.25  # share of the gap we move per round (30 % beat 55 % and 1 %)
FIRST_CAP = 0.016  # its first capped concession after the first counter, share of price (D01)


class Side:
    def __init__(self, p, est):
        self.prior, self.est = p, est
        self.view = None
        self.held = False
        self.traded = False
        self.closing = False
        self.mirrored = False
        self.asks = set()
        self.c_min = None
        self.v_min = None  # relationship goals: its value at our C-minimum on its terms
        self.stop_reason = None
        self.kept = 0  # rounds we kept our structure after it put its own terms back


class Negotiator(Policy):
    def __init__(self, case, world=None, writer=None, learn=False, reader=None, claims=None):
        super().__init__(case, world, writer)
        self.learn, self.reader, self.claim_bank = learn, reader, claims
        self.sides: dict[str, Side] = {}
        self.alt = c_alt(case)

    # -- setup --------------------------------------------------------------------------------
    def setup(self, threads, log, resumed=False):
        super().setup(threads, log, resumed)
        if self.claim_bank is None and self.world is not None:
            try:
                from ..claims.bank import ClaimBank

                self.claim_bank = ClaimBank(self.case, self.world)
            except Exception as e:  # noqa: BLE001
                log.emit("error", where="claims", error=repr(e)[:300])
        for sid, t in threads.items():
            opening = next((m for m in t.messages if m.get("from") != "buyer"), None)
            wp = None
            if self.world is not None:
                try:
                    wp = self.world.profile(sid, self.case)
                except Exception as e:  # noqa: BLE001
                    log.emit("error", where="profile", supplier_id=sid, error=repr(e)[:300])
            p = prior(self.case, sid, opening, wp)
            _, st = t.standing
            est = make_est(self.case, t.std, rate=p.rate, ppm=p.ppm_real, lead=(st or {}).get("lead_time_days"),
                           overtime=p.overtime, clause_moves=p.clause_moves, volume_value=p.volume_value,
                           security=p.security,
                           hedged="hedged" in p.traits and p.owner != "distributor"
                           and self.case.get("type") != "raw_material")  # fmt: skip
            side = self.sides[sid] = Side(p, est)
            self.refresh(t)
            if resumed:
                side.held = len(side.view.ours) >= 2
                side.closing = len(side.view.ours) >= CLOSEOUT_ROUND
            if self.claim_bank is not None:
                try:
                    self.claim_bank.prepare(sid, t)
                except Exception as e:  # noqa: BLE001 - no claims rather than no case
                    log.emit("error", where="claims", supplier_id=sid, error=repr(e)[:300])
            v = side.view
            log.emit("plan", supplier_id=sid, owner=p.owner, traits=sorted(p.traits), rate=p.rate, room=p.room,
                     first_drop=p.first_drop,
                     patience=p.patience, full_cost=est.full_cost, should_cost=p.should_cost, quote_c=v.quote_c,
                     quote_v=v.quote_v, c_alt=self.alt, floor_c=self.floor_c(t), c_min=side.c_min, notes=p.notes,
                     claims=self.claim_bank.summary(sid) if self.claim_bank is not None else None)  # fmt: skip

    def full_cost(self, t):
        """Relationship goals: our estimate of its full cost. Should-cost overshoots the true full
        cost by 1-3 %; without one, assume the quote leaves the sustainable margin plus 3 points.
        Bounded by what its own sheets are worth to it (it rarely sells below full cost)."""
        if not relationship_goal(self.case):
            return
        side = self.sides[t.sid]
        est = side.est
        vals = []
        for m in t.theirs:
            for s in m.get("term_sheets") or []:
                terms = s.get("terms") or {}
                if terms.get("unit_price"):
                    x = value(self.case, terms, est, cash=True)
                    if x == x:
                        vals.append(x)
        if not vals:
            return
        # full cost against the quote seen on real cases: D01 0.91, P03 0.91, P01 0.94, P02 0.995
        fc = vals[0] * 0.92
        if side.prior.should_cost:
            fc = min(vals[0] * 0.995, 0.98 * side.prior.should_cost)
        fc = min(fc, min(vals))
        est.full_cost = round(fc, 4) if fc > 0 else None
        _, st = t.standing
        if est.full_cost and st and st.get("unit_price"):
            terms = complete(self.case, st, t.std)
            p_opt, c_opt = relationship_optimum(self.case, terms, t.std, est, st["unit_price"] * 0.7,
                                                st["unit_price"])  # fmt: skip
            side.c_min = c_opt
            side.v_min = value(self.case, {**terms, "unit_price": p_opt}, est) if p_opt else None

    def est(self, t):
        side = self.sides.get(t.sid)
        return side.est if side and side.est.full_cost else None

    def pessimistic_est(self, t):
        """Relationship goals with an alternative: our estimate with a full cost 4 % higher."""
        e = self.est(t)
        if e is None or not math.isfinite(self.alt):
            return None
        import copy

        side = self.sides.get(t.sid)
        quote_cash = None
        for m in t.theirs:
            for sh in m.get("term_sheets") or []:
                if (sh.get("terms") or {}).get("unit_price"):
                    quote_cash = value(self.case, sh["terms"], e, cash=True)
                    break
            if quote_cash:
                break
        p = copy.copy(e)
        p.full_cost = max(e.full_cost * 1.04, 0.97 * quote_cash if quote_cash and side else 0.0)
        return p

    def known_id(self, ref):
        if ref.startswith("offer:"):
            return any(s.get("term_sheet_id") == ref for t in self.threads.values() for m in t.theirs
                       for s in m.get("term_sheets") or [])  # fmt: skip
        return self.world.known(ref) if self.world is not None else False

    # -- state --------------------------------------------------------------------------------
    def refresh(self, t):
        side = self.sides[t.sid]
        self.full_cost(t)
        side.view = view(self.case, t, side.est, self.est(t))
        return side.view

    def slope(self, t):
        _, st = t.standing
        return max(1e-9, price_slope(self.case, complete(self.case, st, t.std), t.std)) if st else 1.0

    def floor_c(self, t):
        """Its floor in our C (its standing sheet moved down to its extrapolated floor value)."""
        v = self.sides[t.sid].view
        if not math.isfinite(v.standing_c) or v.floor_v != v.floor_v:
            return math.inf
        return v.standing_c - max(0.0, v.standing_v - v.floor_v) * self.slope(t)

    def nxt(self, t):
        """Its next concession in value; at least its last (concessions rarely grow)."""
        side, v = self.sides[t.sid], self.sides[t.sid].view
        n = next_concession(v, side.prior.ratio, side.prior.room)
        return max(0.0, n, v.concessions[-1] if len(v.concessions) >= 2 else 0.0)

    def stop(self, t):
        side, v = self.sides[t.sid], self.sides[t.sid].view
        if t.pruned:
            return "pruned"
        if not t.live:
            return "ended"
        if t.status in ("agreed", "final_offer"):
            return t.status
        if t.moves_left(self.case) <= 0:
            return "no rounds left"
        if v.standing_v != v.standing_v:
            return "no price"
        if v.last_ours is not None:
            gap = v.rel(v.standing_v - v.last_ours)
            gap_c = (v.standing_c - v.ours_c[-1]) / abs(v.standing_c) if v.ours_c and v.standing_c else 0.0
            if gap <= STOP_GAP and gap_c <= 0.002:
                return f"gap {gap:.4f} small"
            real_move = len(v.our_moves) >= 1 and v.rel(v.our_moves[-1]) >= 0.003
            if len(v.concessions) >= 3 and real_move and v.rel(v.standing_v - v.floor_v) < 0.0015:
                return "its floor is reached"
            if v.cum_gap >= side.prior.patience * 0.9:
                return f"patience used ({v.cum_gap:.2f})"
            if v.frozen and side.traded:
                return "frozen after a trade"
            if side.closing and _answered_last(t):
                still = v.concessions and v.rel(v.concessions[-1]) >= 0.0015
                if still and t.moves_left(self.case) > 1 and v.cum_gap < side.prior.patience * 0.8:
                    side.closing = False  # it answered with a real step down: keep going (P08 5.47 -> 5.42)
                else:
                    return "answered our close-out"
        if t.ours and side.c_min is not None and v.standing_c <= side.c_min * (1 + 0.0005):
            return "its standing sheet is at our C-minimum"
        return None

    def wants_message(self, t):
        if t.standing[0] is None or t.sid not in self.sides:
            return False
        side = self.sides[t.sid]
        if side.view is None:
            self.refresh(t)
        why = self.stop(t)
        if why != side.stop_reason:
            side.stop_reason = why
            if why and self.log:
                v = side.view
                self.log.emit("model_update", supplier_id=t.sid, stop=why, standing_c=v.standing_c,
                              floor_c=self.floor_c(t), cum_gap=v.cum_gap, ratio=v.ratio)  # fmt: skip
        if why is None:
            return True
        # one last sheet at our ceiling when its standing sheet does not beat the alternative
        ok, _, _ = gate(self.case, t.standing[1], t.std, self.est(t))
        return (not ok and math.isfinite(self.alt) and not side.mirrored and t.live and t.status == "open"
                and t.moves_left(self.case) > 0 and why != "pruned")  # fmt: skip

    def priority(self, t):
        return self.floor_c(t) if t.sid in self.sides else math.inf

    def keep_alive(self, t):
        best = self.best_standing(self.threads)
        return bool(best and best[1] is t) and not self.wants_message(t)

    # -- moves --------------------------------------------------------------------------------
    def anchor_v(self, t):
        side, v = self.sides[t.sid], self.sides[t.sid].view
        p = side.prior
        # a deep anchor spends patience (the sum of our gaps) fast; founders in an RFQ drop deep
        # on their first counter, so a held deep anchor works there (P05: 0.828)
        rfq = len(self.case.get("suppliers") or []) > 1
        # founders in an RFQ and raw-material distributors took deep anchors without walking
        # (P05 0.226 under its quote, P08/P18 0.21)
        deep = (rfq and p.owner == "founder") or self.case.get("type") == "raw_material"
        share = 0.55 if deep else 0.3
        # below the room it usually gives, and well below its expected first counter (a sheet at
        # or above that counter gets `agreed` and freezes)
        # it concedes about min(0.8 x our move, a shrinking cap) after its first counter: a gap of
        # about 8 % behind that counter lets it give all its caps before the gap closes
        rel = max(0.03, 1.1 * p.room + 0.01, p.first_drop + 0.085)
        rel = min(0.25, rel, p.patience * share)
        # never above its expected first counter: it would agree at once and freeze (P18 real:
        # anchor 5.66, first counter 5.70)
        rel = max(rel, p.first_drop + 0.03)
        return v.quote_v - rel * v.price, f"anchor {rel:.3f} under its quote"

    def target_v(self, t):
        side, v = self.sides[t.sid], self.sides[t.sid].view
        if v.last_ours is None:
            return self.anchor_v(t)
        last = v.last_ours
        gap = v.standing_v - last
        hold_gap = v.rel(v.standing_v - last)
        # only job shops agreed to a held anchor (P05 Dralveck); a distributor answered a held
        # sheet with no move at all and went final (P18 run 3: 5.20, 5.20, final)
        if (not side.held and len(v.concessions) == 1 and v.first_drop >= 0.06 and side.prior.owner == "founder"
                and v.cum_gap + hold_gap < 0.8 * side.prior.patience):  # fmt: skip
            side.held = True
            return last, f"hold the anchor after a deep first drop ({v.first_drop:.3f})"
        side.held = True
        # size the move to its concessions: it gives about 0.8 x our move up to a cap that shrinks
        # by round, so move its last concession / 0.8 (a 1.6 % cap before we know); while it gives
        # more than we move (ratio > 1), move as little as we may
        r = v.ratio
        if len(v.concessions) >= 2 and r is not None and r >= 1.0:
            move = MIN_MOVE * v.price
        elif len(v.concessions) >= 2:
            move = max(0.0, v.concessions[-1]) / 0.8
        else:
            move = FIRST_CAP * v.price / 0.8
        move = min(move, 0.45 * gap)
        move = max(move, MIN_MOVE * v.price)
        ceiling = gap - 1.15 * self.nxt(t)  # stay under the counter it would make next
        if ceiling < MIN_MOVE * v.price:
            return None, "no room under its next counter"
        move = min(move, ceiling)
        return last + move, f"move {v.rel(move):.4f} of price (gap {v.rel(gap):.4f})"

    def agreeable(self, t):
        """The least we must offer to be agreed: it agrees once our sheet reaches the counter it
        would make next, standing - min(0.8 x our move, its cap); so move m with
        m >= gap / 1.8 and m >= gap - cap (just gap - cap while it gives more than we move)."""
        v = self.sides[t.sid].view
        last = v.last_ours
        if last is None:
            return v.standing_v - self.nxt(t)
        gap = max(0.0, v.standing_v - last)
        cap = self.nxt(t)
        r = v.ratio
        m = gap - cap if (r is not None and r >= 1.0) else max(gap / 1.8, gap - cap)
        m = max(m * 1.05, MIN_MOVE * v.price)
        return min(last + m, v.standing_v - STAND_OFF * v.price)

    def caps(self, t, target):
        side, v = self.sides[t.sid], self.sides[t.sid].view
        target = min(target, v.standing_v - STAND_OFF * v.price, v.standing_v - 1.1 * self.nxt(t))
        if side.v_min is not None and side.v_min < v.standing_v - STAND_OFF * v.price:
            target = max(target, side.v_min)  # below our C-minimum a lower price costs us more
        return target

    def packages(self, t, target_v, close=False):
        """Packages worth `target_v` to it, cheapest for us first, that pass our hard limits."""
        side, v = self.sides[t.sid], self.sides[t.sid].view
        lim = v.standing_c - 1e-6
        if math.isfinite(self.alt):
            lim = min(lim, self.alt - max(1.0, 0.0002 * self.alt))
        out = []
        cands = at_value(self.case, t.std, side.est, target_v)
        for k in cands:
            if not close:
                k = self.under_next(t, k)
            if k.c < lim:
                out.append(k)
        if not out and not t.ours and relationship_goal(self.case):
            # our C-minimum sits above its quote: the full-cost estimate is off; judge the anchor
            # without the relationship charge until its counters bound that estimate
            base = cost(self.case, t.standing[1], t.std)
            ceiling = min(base, self.alt - max(1.0, 0.0002 * self.alt)) if math.isfinite(self.alt) else base
            out = [k for k in (self.under_next(t, k) for k in cands) if cost(self.case, k.terms, t.std) < ceiling]
        out.sort(key=lambda k: k.c)
        return out

    def under_next(self, t, k):
        """Keep a sheet's price under the counter it would make next too (our value model may be
        off: a supplier that weighs payment less than we think would agree at our price)."""
        side, v = self.sides[t.sid], self.sides[t.sid].view
        p_cap = round(min(v.price * (1 - STAND_OFF), v.price - 1.1 * self.nxt(t)), 2)
        if k.price <= p_cap or p_cap <= 0.01:
            return k
        terms = {**k.terms, "unit_price": p_cap}
        return Package(terms, cost(self.case, terms, t.std, self.est(t)), value(self.case, terms, side.est))

    def our_main_terms(self, t):
        """The non-price terms of our last main sheet."""
        for m in reversed(t.ours):
            sheets = m.get("term_sheets") or []
            if sheets and sheets[0].get("terms"):
                return sheets[0]["terms"]
        return None

    def sheets(self, t, pkgs, target_v, close=False):
        """After round 1 the main sheet keeps the supplier's current terms at our target value (a
        genuine move in its eyes, whatever our value model says); the package cheapest for us at
        that value rides along when it is clearly cheaper. Round 1: cheapest first, then its own
        terms at the same C (that hedges our value model and shows what it prefers)."""
        side = self.sides[t.sid]
        _, st = t.standing
        full = complete(self.case, st, t.std)
        best = pkgs[0]
        if t.ours:
            mine = self.our_main_terms(t)
            if mine is not None and not same_terms(mine, full, self.case) and side.kept < KEEP_OURS:
                # it put its own terms back (a distributor's clause): keep our structure, it keeps
                # countering on its terms at lower prices (P18 5.00 against 5.10 when we took them)
                side.kept += 1
                k = next((k for k in pkgs if same_terms(k.terms, mine, self.case)), None)
                if k is not None:
                    return [k]
            own = next((k for k in pkgs if same_terms(k.terms, full, self.case)), None)
            if own is None:
                return [best]
            out = [own]
            if not same_terms(best.terms, full, self.case) and best.c < own.c * (1 - 0.003):
                out.append(best)
            return out
        out = [best]
        if not same_terms(best.terms, full, self.case):
            p = price_for_cost(self.case, full, t.std, best.c + 0.005, self.est(t))
            if p:
                k = Package({**full, "unit_price": p}, 0.0, 0.0)
                terms = k.terms if close else self.under_next(t, k).terms
                c = cost(self.case, terms, t.std, self.est(t))
                # a hedge far under the main price reads as an insult, not as an option
                sane = terms["unit_price"] >= 0.92 * best.price
                if c <= best.c + 0.5 and sane and musts_ok(self.case, terms, t.std):
                    out.append(Package(terms, c, value(self.case, terms, side.est)))
        return out[:3]

    def next_move(self, t, budget):
        self.refresh(t)
        side, v = self.sides[t.sid], self.sides[t.sid].view
        why = self.stop(t)
        if why is not None:  # only the last sheet at the ceiling gets here
            side.mirrored = True
            return self.ceiling_move(t, why)
        if v.frozen and not side.traded and t.ours:
            mv = self.trade(t)
            if mv:
                return mv
        close = False
        if due := self.closing_due(t):
            side.closing = close = True
            target, reason = self.agreeable(t), f"close-out at its next counter ({due})"
            if side.v_min is not None:
                target = max(target, side.v_min)
        else:
            target, reason = self.target_v(t)
            if target is None:  # no room to move without being agreed: move just enough to be
                side.closing = close = True
                target = self.agreeable(t)
                reason = f"close-out at its next counter ({reason})"
            else:
                target = self.caps(t, target)
        if v.last_ours is not None and target < v.last_ours:
            target = v.last_ours  # never retract: a worse offer than our last reads as a step back
        pkgs = self.packages(t, target, close=close)
        if not pkgs and math.isfinite(self.alt):
            return self.ceiling_move(t, "no package below the alternative at that value")
        if not pkgs:
            return None
        sheets = self.sheets(t, pkgs, target, close)
        if v.last_ours is not None:
            sheets = [k for k in sheets if k.v >= v.last_ours - 1e-4 * v.price]
            if not sheets:  # nothing fits under its next counter without stepping back: hold
                prev = [s_.get("terms") for s_ in t.ours[-1].get("term_sheets") or [] if s_.get("terms")]
                prev = prev or [s_.get("terms") for m in reversed(t.ours) for s_ in m.get("term_sheets") or []][:1]
                if not prev:
                    return None
                reason = f"hold our last offer ({reason})"
                sheets = [Package(dict(x), cost(self.case, x, t.std, self.est(t)), value(self.case, x, side.est))
                          for x in prev[:2]]  # fmt: skip
                sheets = [k for k in sheets if k.c < self.alt]
                if not sheets:
                    return None
        mv = Move("send", t.sid, sheets=[k.terms for k in sheets], claims=self.claims_for(t), reason=reason,
                  tone="first" if not t.ours else ("close" if close else "counter"), points=self.answers(t))  # fmt: skip
        if len(t.ours) == 1:
            mv.ask = "What would you need from us to move further?"
        self.log.emit("plan", supplier_id=t.sid, round=t.rounds_used + 1, target_v=target, reason=reason,
                      sheets=[{"terms": k.terms, "c": k.c, "v": k.v} for k in sheets], standing_c=v.standing_c,
                      standing_v=v.standing_v, floor_c=self.floor_c(t), cum_gap=v.cum_gap, ratio=v.ratio,
                      c_min=side.c_min)  # fmt: skip
        return mv

    def ceiling_move(self, t, why):
        """One sheet just under the alternative, worth most to it: the most we can offer."""
        side = self.sides[t.sid]
        if not math.isfinite(self.alt):
            return None
        pkgs = at_cost(self.case, t.std, side.est, self.alt - max(1.0, 0.0005 * self.alt),
                       ref_price=side.view.price)  # fmt: skip
        pkgs = [k for k in pkgs if k.c < side.view.standing_c]
        if not pkgs:
            return None
        self.log.emit("plan", supplier_id=t.sid, round=t.rounds_used + 1, reason=f"ceiling sheet ({why})",
                      sheets=[{"terms": pkgs[0].terms, "c": pkgs[0].c, "v": pkgs[0].v}])  # fmt: skip
        return Move("send", t.sid, sheets=[pkgs[0].terms], claims=self.claims_for(t), tone="close",
                    reason=f"ceiling sheet ({why})", points=self.answers(t))  # fmt: skip

    def closing_due(self, t):
        side, v = self.sides[t.sid], self.sides[t.sid].view
        if side.closing or not t.ours:
            return None
        if t.rounds_used + 1 >= CLOSEOUT_ROUND:
            return f"round {t.rounds_used + 1}"
        if t.moves_left(self.case) <= 1:
            return "last round"
        if len(v.concessions) >= 2 and v.rel(next_concession(v, side.prior.ratio, side.prior.room)) < 0.002:
            return "its concessions are small"
        if v.cum_gap >= side.prior.patience * 0.75:
            return f"patience {v.cum_gap:.2f}"
        return None

    def trade(self, t):
        """A frozen supplier: packages worth what its standing sheet is worth to it, cheaper for us."""
        side, v = self.sides[t.sid], self.sides[t.sid].view
        side.traded = True
        pkgs = [k for k in self.packages(t, v.standing_v, close=True) if k.c < v.standing_c * (1 - 0.0015)]
        if not pkgs:
            return None
        sheets = [pkgs[0]] + [k for k in pkgs[1:2] if not same_terms(k.terms, pkgs[0].terms, self.case)]
        self.log.emit("plan", supplier_id=t.sid, round=t.rounds_used + 1, reason="trade on terms: frozen price",
                      sheets=[{"terms": k.terms, "c": k.c, "v": k.v} for k in sheets])  # fmt: skip
        return Move("send", t.sid, sheets=[k.terms for k in sheets], claims=self.claims_for(t),
                    reason="trade on terms: frozen price", tone="trade", points=self.answers(t),
                    ask="We can talk about the terms instead: would one of these packages work for you?")  # fmt: skip

    def claims_for(self, t):
        if self.claim_bank is None:
            return []
        try:
            out = self.claim_bank.next(t, self.threads, round_=len(t.ours) + 1)
            if out:
                self.log.emit("claims", supplier_id=t.sid, claims=out)
            return out
        except Exception as e:  # noqa: BLE001
            self.log.emit("error", where="claims", supplier_id=t.sid, error=repr(e)[:300])
            return []

    def answers(self, t):
        side = self.sides[t.sid]
        body = t.last.get("body") or ""
        out = []
        fixed = self.case.get("fixed_terms") or {}
        if ASK_VOLUME.search(body) and "volume_per_year" in fixed:
            side.asks.add("volume")
            out.append(f"We confirm our full need of {fixed['volume_per_year']} units a year, all placed with you.")
        if ASK_PAYMENT.search(body):
            side.asks.add("payment")
            if "payment_days" in self.case["negotiable"]:
                out.append("We have looked at the payment terms and reflected them in our proposal.")
        if ASK_FORECAST.search(body):
            side.asks.add("forecast")
            out.append(f"Our planning covers {self.case['buyer']['need_per_year']} units a year over the term.")
        return out

    def keepalive_move(self, t):
        return Move("send", t.sid, ask="We are reviewing your offer with our team and will come back to you "
                    "shortly. Could you confirm that it stands as sent?", keepalive=True,
                    reason="keepalive", allow_at_standing=True, tone="keepalive")  # fmt: skip

    def observe(self, t, msg, reply, move):
        side = self.sides.get(t.sid)
        if side is None or not reply:
            return
        body = reply.get("body") or ""
        read = self.read(t, body)
        asks = set((read or {}).get("asks") or [])
        if (
            ASK_PAYMENT.search(body) or "faster_payment" in asks or (read or {}).get("cash_short")
        ) and side.est.rate < 0.2:
            side.est.rate = round(min(0.2, side.est.rate + 0.03), 4)
        if ASK_VOLUME.search(body) or "volume_commitment" in asks:
            side.est.volume_value = min(0.04, side.est.volume_value + 0.005)
        if read and read.get("capacity_tight"):
            side.est.overtime = max(side.est.overtime, 0.003)
        if read:
            side.asks |= asks
        self.refresh(t)
        v = side.view
        self.log.emit("model_update", supplier_id=t.sid, standing_c=v.standing_c, standing_v=v.standing_v,
                      floor_c=self.floor_c(t), ratio=v.ratio, cum_gap=v.cum_gap, status=v.status,
                      rate=side.est.rate, full_cost=side.est.full_cost, c_min=side.c_min,
                      concessions=[round(v.rel(x), 5) for x in v.concessions])  # fmt: skip

    def read(self, t, body):
        """The reader on a reply, when the clocks allow it (one or two live threads)."""
        if self.reader is None or not body or sum(o.live for o in self.threads.values()) > 2:
            return None
        import time

        t0 = time.monotonic()
        out = None
        try:
            out = self.reader.read(body, timeout=6.0)
        except Exception:  # noqa: BLE001
            out = None
        self.log.emit("llm_call", supplier_id=t.sid, role="reader", seconds=round(time.monotonic() - t0, 2),
                      fallback=out is None, read=out)  # fmt: skip
        return out

    # -- the close ----------------------------------------------------------------------------
    def close_decision(self, threads, final=False):
        for t in threads.values():
            if t.sid in self.sides and self.sides[t.sid].view is None:
                self.refresh(t)
        best = self.best_standing(threads)
        live = [t for t in threads.values() if t.live]
        if final:
            if best:
                return Move("accept", best[1].sid, reason="close: cheapest valid standing sheet")
            return Move("walk", live[0].sid if live else None, reason="close: nothing passes the gate")
        active = [t for t in live if self.wants_message(t)]
        if best:
            c_best, tb = best
            for o in active:
                if o is tb or len(self.sides[o.sid].view.theirs) < 2:
                    continue  # job shops drop 12-15 % on their first counter: hear it first
                fo = self.floor_c(o)
                if fo >= c_best - 1e-6:
                    o.pruned = True
                    self.log.emit("model_update", supplier_id=o.sid, pruned=True, floor_c=fo, best_c=c_best)
            active = [t for t in active if not t.pruned]
            if not active:
                why = self.sides[tb.sid].stop_reason if tb.sid in self.sides else None
                return Move("accept", tb.sid, reason=f"cheapest valid standing sheet ({why})")
            if tb.status in ("agreed", "final_offer") and all(o is tb for o in active):
                return Move("accept", tb.sid, reason=f"{tb.status} and no rival can beat it")
            return None
        if not active:
            return Move("walk", live[0].sid if live else None, reason="nothing passes the gate")
        return None


def _answered_last(t):
    """The supplier's message answers a sheet of ours (our last message carried sheets)."""
    return (
        len(t.messages) >= 2
        and t.messages[-1].get("from") != "buyer"
        and bool(t.messages[-2].get("from") == "buyer" and t.messages[-2].get("term_sheets"))
    )
