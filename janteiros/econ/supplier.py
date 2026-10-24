"""Our estimate of a supplier: what a sheet is worth to it per unit (in cash at its standard terms)
and its full cost. The kit's `supplier_value` covers price, payment at its cost of money, freight
leg, tooling and chargebacks at its real defect rate; we add what the kit leaves to us: overtime
for faster delivery, the cost of a tighter ppm commitment, and what an index clause is worth."""

from dataclasses import dataclass, field

from arena_kit.cost import supplier_value

from .cost import closing, safe_std, safe_terms

CLAUSE_VALUE_MAX = 0.08  # the most a clause was ever worth to a supplier, share of price (P02)


@dataclass
class Est:
    standard: dict
    rate: float = 0.07  # its cost of money per year
    full_cost: float | None = None  # EUR per unit, for the relationship charge
    ppm: float | None = None  # its real defect rate
    lead: int | None = None  # the lead time it quoted
    overtime: float = 0.0005  # share of price per day faster than `lead`
    ppm_tight: float = 0.012  # share of price for a commitment at zero ppm vs its standard
    clause_moves: dict = field(default_factory=dict)  # series -> expected move it prices in
    volume_value: float = 0.0  # share of price per +100 % volume (spare capacity > 0)
    security: float = 0.0  # share of price per extra year of contract (security, not cash)
    hedged: bool = False

    def kit(self):
        d = {"standard": self.standard, "interest_rate": self.rate}
        if self.full_cost:
            d["full_cost"] = self.full_cost
        if self.ppm is not None:
            d["ppm"] = self.ppm
        return d

    def kit_for(self, case, terms):
        """The kit's estimate with our extras for these terms folded into `extra`."""
        d = self.kit()
        if not self.full_cost or not terms or terms.get("unit_price") is None:
            return d
        try:
            base = supplier_value(case, safe_terms(case, terms), d)
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            return d
        extra = value(case, terms, self) - base
        if extra == extra and abs(extra) > 1e-9:
            d["extra"] = extra
        return d


def make_est(case, std, **kw):
    standard = safe_std(case, closing(case, {}, std))
    return Est(standard=standard, **kw)


def clause_share(clause, est: Est):
    if not clause or est.hedged:
        return 0.0
    move = est.clause_moves.get(clause.get("series"))
    if move is None:
        return 0.0
    exposed = max(0.0, abs(move) - (clause.get("deadband_pct") or 0) / 100) * (1 if move > 0 else -1)
    return max(-CLAUSE_VALUE_MAX, min(CLAUSE_VALUE_MAX, (clause.get("material_share") or 0) * exposed))


def value(case, terms, est: Est) -> float:
    """What `terms` are worth to the supplier per unit."""
    t = safe_terms(case, terms)
    try:
        v = supplier_value(case, t, est.kit())
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return float("nan")
    full = closing(case, t, est.standard)
    price = full.get("unit_price") or 0.0
    v += clause_share(full.get("index_clause"), est) * price
    lead, quoted = full.get("lead_time_days"), est.lead or est.standard.get("lead_time_days")
    if lead is not None and quoted and lead < quoted:
        v -= est.overtime * price * (quoted - lead)
    ppm, std_ppm = full.get("ppm_commitment"), est.standard.get("ppm_commitment")
    if ppm is not None and std_ppm and ppm < std_ppm:
        v -= est.ppm_tight * price * (std_ppm - ppm) / std_ppm
    vol, std_vol = full.get("volume_per_year"), est.standard.get("volume_per_year") or case["buyer"]["need_per_year"]
    if vol and std_vol and est.volume_value:
        v += est.volume_value * price * (vol - std_vol) / std_vol
    months, std_m = full.get("contract_months"), est.standard.get("contract_months") or 12
    if months is not None and est.security:
        v += est.security * price * (months - std_m) / 12
    return v


def price_for_value(case, terms, est, target):
    """The unit price at which `terms` are worth `target` to the supplier (value is linear in price)."""
    v1 = value(case, {**terms, "unit_price": 10.0}, est)
    v2 = value(case, {**terms, "unit_price": 20.0}, est)
    slope = (v2 - v1) / 10.0
    if not slope > 0:
        return None
    return 10.0 + (target - v1) / slope


def margin(case, terms, est):
    if not est.full_cost:
        return None
    v = value(case, terms, est)
    return (v - est.full_cost) / v * 100 if v else None
