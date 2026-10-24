"""The one gate right before every send. Returns a list of problems; an empty list means the
message may go. The runner never sends a message with problems: it falls back to a safer one."""

import math
import re

from ..econ.cost import c_alt, cost, musts_ok

MAX_SUBJECT, MAX_BODY, MAX_SHEETS, MAX_REFS, MAX_CLAIMS, MAX_ATTACH = 300, 8000, 3, 20, 3, 5
ID_RE = re.compile(r"\b(?:idx|quote|offer|sc|qn|po|cost|fc|news|doc|ctr|rfq|sup|part|gr|inv):[\w.:@#/-]*\w")
THOUSANDS = re.compile(r"\d,\d{3}\b")
# Words that leak our position or read as pressure (the suppliers answer pressure with "no").
FORBIDDEN = re.compile(
    r"\b(urgent\w*|deadline|asap|immediately|budget|walk(ing)? away|no alternative|no other option|"
    r"target price|last chance|final warning|we must have|desperate|ultimatum|time is running out|"
    r"our limit|maximum we can pay|cannot go above)\b",
    re.I,
)


def _num_ok(v):
    return isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v)


def check_sheet(case, terms, std, standard_ok=True):
    out = []
    neg = case["negotiable"]
    if set(terms) != set(neg):
        out.append(f"sheet fields {sorted(terms)} != negotiable {sorted(neg)}")
        return out
    p = terms.get("unit_price")
    if not _num_ok(p) or p <= 0:
        out.append(f"unit_price {p!r} not > 0")
    elif round(p, 2) != p:
        out.append(f"unit_price {p} has more than 2 decimals")
    opts = case.get("term_options") or {}
    for f, v in terms.items():
        if f == "unit_price":
            continue
        options = opts.get(f)
        if options and v not in options and not (standard_ok and std and std.get(f) == v):
            out.append(f"{f}={v!r} not in term_options")
    if not out and not musts_ok(case, terms, std):
        out.append("breaks a must-have on its closing terms")
    return out


def validate(case, msg, thread=None, *, claims=(), known_ids=None, allow_above_alt=False, allow_at_standing=False):
    """`claims`: the checkable claims the composer put in (each {"refs": [...]}). `known_ids`: a
    predicate for data IDs we may cite. `thread`: the target thread (standing sheet, standard)."""
    problems = []
    action = msg.get("action")
    if action:
        if action.get("type") == "accept":
            sid = thread.standing[0] if thread else None
            if not action.get("term_sheet_id") or action.get("term_sheet_id") != sid:
                problems.append("accept of a sheet that is not the standing one")
        elif action.get("type") != "walk_away":
            problems.append(f"unknown action {action!r}")
        return problems
    subject, body = msg.get("subject") or "", msg.get("body") or ""
    if not subject.strip():
        problems.append("empty subject")
    if len(subject) > MAX_SUBJECT:
        problems.append("subject too long")
    if len(body) > MAX_BODY:
        problems.append("body too long")
    if THOUSANDS.search(body):
        problems.append("number with a thousands separator")
    if m := FORBIDDEN.search(body):
        problems.append(f"forbidden phrase: {m.group(0)!r}")
    sheets = msg.get("term_sheets") or []
    if len(sheets) > MAX_SHEETS:
        problems.append("more than 3 term sheets")
    std = thread.std if thread else {}
    alt = c_alt(case)
    standing_c = math.inf
    if thread is not None:
        sid, st = thread.standing
        if sid and st.get("unit_price"):
            standing_c = cost(case, st, std)
    for i, sh in enumerate(sheets):
        terms = sh.get("terms") if isinstance(sh, dict) else None
        if not isinstance(terms, dict):
            problems.append(f"sheet {i} has no terms")
            continue
        problems += [f"sheet {i}: {p}" for p in check_sheet(case, terms, std)]
        c = cost(case, terms, std)
        if not allow_above_alt and math.isfinite(alt) and not c < alt:
            problems.append(f"sheet {i}: C {c:.2f} not below the alternative {alt:.2f}")
        if not allow_at_standing and math.isfinite(standing_c) and c >= standing_c - 0.005:
            problems.append(f"sheet {i}: C {c:.2f} not below their standing sheet {standing_c:.2f}")
    refs = msg.get("refs") or []
    if len(refs) > MAX_REFS:
        problems.append("more than 20 refs")
    if len(msg.get("attachments") or []) > MAX_ATTACH:
        problems.append("more than 5 attachments")
    if len(claims) > MAX_CLAIMS:
        problems.append("more than 3 checkable claims")
    claimed = {r for c in claims for r in c.get("refs", [])}
    for ref in set(ID_RE.findall(body)) | set(refs):
        if ref.startswith(("sup:", "part:")):
            continue
        if ref not in claimed:
            problems.append(f"ID {ref} in the message is not one of our checked claims")
        elif known_ids is not None and not known_ids(ref):
            problems.append(f"unknown ID {ref}")
    return problems
