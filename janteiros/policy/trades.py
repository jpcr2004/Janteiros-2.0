"""Ask-driven, economically gated alternatives.

Supplier requests select fields to explore.  They do not change an estimate.  Every alternative
preserves the estimated supplier value, improves buyer cost, and still passes the normal outbound
validator afterwards.
"""

import json
import math

from ..econ.cost import cost, musts_ok
from ..econ.optimise import Package
from ..econ.supplier import price_for_value, value
from ..econ.terms import allowed, complete


def signature(terms):
    return json.dumps(terms, sort_keys=True, separators=(",", ":"), default=str)


def requested_fields(asks, traits=()):
    asks, traits = set(asks or ()), set(traits or ())
    out = []
    if asks & {"faster_payment", "payment"} or "cash_need" in traits:
        out.append(("payment_days", "lower"))
    if asks & {"volume_commitment", "volume"} or traits & {"spare_capacity", "wants_volume"}:
        out.append(("volume_per_year", "higher"))
    if "longer_contract" in asks:
        out.append(("contract_months", "higher"))
    if "longer_lead_time" in asks or "capacity_full" in traits:
        out.append(("lead_time_days", "higher"))
    if "index_clause" in asks:
        out.append(("index_clause", "different"))
    return list(dict.fromkeys(out))


def candidates(case, std, supplier_est, cost_est, main, asks=(), traits=(), seen=(), limit=2):
    """Return cheaper one-field alternatives at the main sheet's estimated supplier value."""
    if not main or main.get("unit_price") is None or limit <= 0:
        return []
    base = complete(case, main, std)
    base_v = value(case, base, supplier_est)
    base_c = cost(case, base, std, cost_est)
    if not (math.isfinite(base_v) and math.isfinite(base_c)):
        return []
    seen = set(seen or ()) | {signature(base)}
    need = case.get("buyer", {}).get("need_per_year")
    rows = []
    for field, direction in requested_fields(asks, traits):
        if field not in case.get("negotiable", []) or field == "unit_price":
            continue
        current = base.get(field)
        for option in allowed(case, field, std):
            if option == current:
                continue
            if direction in ("lower", "higher"):
                if not isinstance(option, int | float) or not isinstance(current, int | float):
                    continue
                if direction == "lower" and option >= current:
                    continue
                if direction == "higher" and option <= current:
                    continue
            if field == "volume_per_year" and need is not None and option > need:
                continue
            terms = complete(case, {**base, field: option}, std)
            price = price_for_value(case, terms, supplier_est, base_v)
            if price is None or not math.isfinite(price) or price <= 0:
                continue
            terms["unit_price"] = round(price, 2)
            sig = signature(terms)
            if sig in seen or not musts_ok(case, terms, std):
                continue
            c = cost(case, terms, std, cost_est)
            improvement = max(1.0, abs(base_c) * 0.001)
            if not math.isfinite(c) or c >= base_c - improvement:
                continue
            rows.append((c, field, repr(option), Package(terms, c, value(case, terms, supplier_est))))
    rows.sort(key=lambda row: (row[0], row[1], row[2]))
    out = []
    used_fields = set()
    for _, field, _, package in rows:
        if field in used_fields:
            continue
        out.append(package)
        used_fields.add(field)
        if len(out) >= limit:
            break
    return out
