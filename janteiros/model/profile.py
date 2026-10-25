"""Priors for a supplier from the data (world profile) and its own e-mails: owner type, traits,
cost of money, how much room its price has, how fast it concedes, how patient it is.
Numbers are the old agent's measurements (docs/learnings.md): priors, not laws."""

import re
from dataclasses import dataclass, field

TRAITS = {
    "cash_need": r"pay(ing)? earlier|faster payment|bank is watching|cash and the banks|within 10 days|cash flow"
    r"|restructur\w*|short-time work|lower\w* (its |their )?rating|cash is tight|fast payers|liquidity"
    r"|negotiat\w+ with (its|their) banks|waiting for cash|insolven\w*|prepayment|credit insurer|skonto",
    "spare_capacity": r"second shift|take additional parts|new customers|free (machine )?capacity"
    r"|keep (the|our) machines (busy|running)|spare capacity|utili[sz]ation is low",
    "capacity_full": r"order intake is very strong|fully booked|cannot confirm your full forecast|allocat\w+"
    r"|super busy|high capacity utili[sz]ation|prioriti[sz]e customers|at full capacity|overtime",
    "open_book": r"walk (us|you) through (their|our) calculation|open book",
    "stale_costing": r"not followed (his|its|their) material|own spreadsheet",
    "approval": r"above \d ?% need the approval|pricing committee|approval of (our|the) (board|management)",
    "deprioritising": r"reviewing small accounts|not strategic|reorganising our range|supply from stock only"
    r"|sharpens its focus|focus on customers with volume",
    "price_frozen": r"no room on price|not discounted|price list (\d{4} )?applies to all|discounts not possible"
    r"|frozen all price reductions|individual price reductions|prices? (are|will be) set centrally",
    "hedged": r"bought (their|our) [\w ,-]{1,60} forward|covered at fixed prices|material covered until"
    r"|secures power prices|hedged|bought forward|secured [\w ,-]{1,60} at fixed prices",
    "distributor": r"distribut\w+|stockist|from stock|mill price|alloy surcharge",
    "wants_volume": r"volume commitment|binding (higher )?volume|commit to|which volume",
}
OWNER_RATE = {"group": 0.05, "private": 0.07, "family": 0.075, "founder": 0.13, "distributor": 0.01}
# share of its quote price it has given in the end (seen: groups 0-12 %, family 1.6-13 %, founders to 22 %)
OWNER_ROOM = {"group": 0.045, "private": 0.06, "family": 0.065, "founder": 0.16, "distributor": 0.05}


# Total concession from the quote seen per case type (records: P18 -18 %, P08 -13 %, P05 -23 %,
# D01 -8 %, P01 -6 %, P07 -7 %, P11 -3 %, P10 -2 %, renewals 0.5-1.5 %).
TYPE_ROOM = {"rfq": 0.15, "raw_material": 0.13, "bundling": 0.08, "price_increase": 0.06, "renewal": 0.02,
             "capacity": 0.03, "quality": 0.04}  # fmt: skip
# Its first counter, share of its quote (distributors 10 %, job shops 13-17 %, others 2-4 %).
FIRST_DROP = {"rfq": 0.06, "raw_material": 0.09, "bundling": 0.035, "price_increase": 0.03, "renewal": 0.01,
              "capacity": 0.015, "quality": 0.02}  # fmt: skip


def type_room(case, opening):
    """A price letter overshoots the cost drivers: most of the asked increase is room."""
    room = TYPE_ROOM.get(case.get("type"), 0.06)
    cur = case["buyer"].get("current_unit_price")
    terms = ((opening or {}).get("term_sheets") or [{}])[0].get("terms") or {}
    q = terms.get("unit_price")
    if case.get("type") == "price_increase" and cur and q and q > cur:
        room = max(0.03, 0.75 * (q / cur - 1) / (q / cur))
    return room


@dataclass
class Prior:
    owner: str | None = None
    traits: set = field(default_factory=set)
    rate: float = 0.07
    room: float = 0.06  # expected total concession, share of quote price
    first_drop: float = 0.025  # expected first counter, share of quote price
    ratio: float = 0.5  # its move / our move
    patience: float = 0.36  # cumulative gap at which it goes final
    overtime: float = 0.0005  # share of price per day faster
    volume_value: float = 0.0
    # a longer contract is security to it, not cash; distributors priced 36 months well under 24
    # (P08/P18 first counters 5.30-5.52 at 36 months against 5.70-5.90 at 24)
    security: float = 0.0
    should_cost: float | None = None
    ppm_real: float | None = None
    clause_moves: dict = field(default_factory=dict)
    drivers: list = field(default_factory=list)
    delay: int | None = None
    notes: list = field(default_factory=list)


def traits_in(text):
    return {k for k, pat in TRAITS.items() if re.search(pat, text or "", re.I)}


def prior(case, sid, opening=None, world_profile=None):
    p = Prior()
    wp = world_profile or {}
    p.owner = wp.get("owner_type")
    p.traits |= set(wp.get("traits") or [])
    p.should_cost = wp.get("should_cost")
    p.ppm_real = wp.get("ppm")
    p.clause_moves = dict(wp.get("clause_moves") or {})
    p.drivers = list(wp.get("drivers") or [])
    if opening:
        p.traits |= traits_in(opening.get("body") or "")
        p.delay = (opening.get("signals") or {}).get("reply_delay_days")
    if p.owner is None and p.delay is not None:
        # reply delay predicts shop size: 4-5 days founder job shops, 2-3 family, 1 group
        p.owner = "founder" if p.delay >= 4 else "family" if p.delay >= 2 else "group"
        p.notes.append(f"owner guessed from reply delay {p.delay}")
    if "distributor" in p.traits and p.owner in (None, "group", "private"):
        p.owner = "distributor"
    p.rate = OWNER_RATE.get(p.owner, 0.07)
    if wp.get("rate"):
        p.rate = max(p.rate, wp["rate"])
    if "cash_need" in p.traits:
        p.rate += 0.05
    if case.get("type") == "raw_material":
        p.security = 0.02  # seen on the distributors only (P08/P18); elsewhere no evidence
    rfq = case.get("type") == "rfq" and len(case.get("suppliers") or []) > 1
    owner_room = OWNER_ROOM.get(p.owner, 0.06)
    if p.owner == "founder" and not rfq:
        owner_room = 0.09  # the 16 % were job shops competing in an RFQ
    p.room = max(owner_room, type_room(case, opening))
    p.first_drop = FIRST_DROP.get(case.get("type"), 0.025)
    if p.owner == "founder" and case.get("type") == "rfq":
        p.first_drop = 0.13
    if "price_frozen" in p.traits:
        p.room, p.ratio = 0.005, 0.05
    if "approval" in p.traits:
        p.patience = 0.25  # Nordmark (pricing committee) walked at a cumulative gap of 19 %
    if "stale_costing" in p.traits:
        p.room += 0.02
    if "capacity_full" in p.traits:
        p.overtime, p.volume_value, p.patience = 0.0045, -0.01, 0.2
        p.room = min(p.room, 0.04)
    if "spare_capacity" in p.traits:
        p.volume_value, p.room = 0.02, p.room + 0.02
    if case.get("type") == "rfq" and len(case.get("suppliers") or []) > 1:
        p.room += 0.03
    if p.owner == "founder":
        p.ratio = 0.6
        if "capacity_full" not in p.traits and "approval" not in p.traits:
            p.patience = 0.4
    elif p.owner in ("group", "distributor"):
        p.ratio = 0.4
    return p
