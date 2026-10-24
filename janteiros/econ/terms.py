"""Valid term sheets: exactly the negotiable fields, every non-price value allowed."""

import itertools

from arena_kit.cost import closing_terms


def allowed(case, field, std=None):
    """Values `field` may take in a sheet: its term_options, plus the supplier's standard value."""
    opts = list((case.get("term_options") or {}).get(field) or [])
    if std and field in std and std[field] not in opts and opts:
        opts.append(std[field])
    return opts


def complete(case, terms, std=None, price=None):
    """A sheet with exactly the negotiable fields: `terms` first, then the supplier's standard
    terms, then the closing default; a value outside the options snaps to the closest allowed."""
    base = closing_terms(case, {}, std or {})
    out = {}
    for f in case["negotiable"]:
        if f == "unit_price":
            v = price if price is not None else terms.get("unit_price")
            out[f] = round(float(v), 2) if v is not None else None
            continue
        v = terms[f] if f in terms else base.get(f)
        opts = allowed(case, f, std)
        if opts and v not in opts:
            v = snap(opts, v)
        out[f] = v
    return out


def snap(options, value):
    if value in options:
        return value
    nums = [o for o in options if isinstance(o, int | float) and not isinstance(o, bool)]
    if isinstance(value, int | float) and nums:
        return min(nums, key=lambda o: (abs(o - value), o))
    if value is None and None in options:
        return None
    return options[0]


def combos(case, std=None, fixed=None):
    """Every allowed combination of the non-price negotiable fields: [{field: value}].
    `fixed`: {field: value} held constant."""
    fixed = fixed or {}
    fields = [f for f in case["negotiable"] if f != "unit_price"]
    choices = []
    for f in fields:
        if f in fixed:
            choices.append([fixed[f]])
            continue
        opts = allowed(case, f, std)
        if not opts:
            base = closing_terms(case, {}, std or {})
            opts = [base.get(f)]
        choices.append(opts)
    return [dict(zip(fields, c, strict=True)) for c in itertools.product(*choices)]


def same_terms(a, b, case):
    return all(a.get(f) == b.get(f) for f in case["negotiable"] if f != "unit_price")
