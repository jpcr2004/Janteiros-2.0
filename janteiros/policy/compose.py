"""The e-mail around the numbers: code writes every figure, ID, claim sentence and the proposal;
the optional writer adds one human paragraph (no digits, no IDs), else a template does."""

import time

LABEL = {
    "unit_price": "price EUR {v} per unit",
    "payment_days": "payment {v} days net",
    "lead_time_days": "lead time {v} days",
    "volume_per_year": "{v} units a year",
    "contract_months": "contract term {v} months",
    "effective_date": "new price valid from {v}",
    "incoterm": "delivery {v}",
    "tooling_eur": "tooling EUR {v}",
    "ppm_commitment": "quality commitment {v} ppm",
    "chargeback_eur_per_complaint": "chargeback EUR {v} per complaint",
}

OPENERS = {
    "first": "Thank you for your offer and for the work your team has put into it. We value working with you "
    "and would like to find terms that work for both sides.",
    "counter": "Thank you for your reply and for moving with us. We have looked at your offer carefully.",
    "hold": "Thank you for your reply. We appreciate the step, and we would like to understand what would help "
    "you go a little further.",
    "trade": "Thank you for your reply. We are happy to work on the terms that matter to you.",
    "close": "Thank you for your reply. We are close, and we would like to settle this with you.",
    "keepalive": "Thank you for your offer, which we are reviewing.",
    "question": "Thank you for your message.",
}


def fmt(field, v):
    if v is None:
        return "no index clause, fixed price" if field == "index_clause" else f"{field} none"
    if field == "index_clause":
        share = round(v.get("material_share", 0) * 100)
        db = v.get("deadband_pct", 0)
        db = int(db) if float(db).is_integer() else db
        return f"price follows {v.get('series')} on {share}% of the price with a {db}% deadband"
    if field in ("unit_price", "tooling_eur", "chargeback_eur_per_complaint"):
        return LABEL[field].format(v=f"{float(v):.2f}")
    return LABEL.get(field, field + " {v}").format(v=v)


def sheet_line(terms, order):
    parts = [fmt(f, terms[f]) for f in order if f in terms]
    return ", ".join(parts)


def subject_of(case, t):
    subj = (t.last.get("subject") or case.get("title") or "Your offer").strip()
    if not subj.lower().startswith("re:"):
        subj = "Re: " + subj
    return subj[:300]


def compose(case, t, move, writer=None, budget=0.0, log=None):
    kind = "keepalive" if move.keepalive else (move.tone if move.tone in OPENERS else "counter")
    if not t.ours and not move.keepalive:
        kind = "first"
    para = None
    if writer is not None and budget > 2.0 and not move.keepalive:
        t0 = time.monotonic()
        try:
            para = writer.paragraph(case, t, move, budget)
        except Exception:  # noqa: BLE001 - the template is always there
            para = None
        if log:
            log.emit("llm_call", supplier_id=t.sid, role="writer", seconds=round(time.monotonic() - t0, 2),
                     fallback=para is None, budget=round(budget, 1))  # fmt: skip
    lines = ["Dear Sir or Madam,", "", para or OPENERS[kind], ""]
    for c in move.claims:
        lines.append(c["sentence"])
    if move.claims:
        lines.append("")
    for p in move.points:
        lines.append(p)
    if move.points:
        lines.append("")
    order = list(case["negotiable"])
    if len(move.sheets) == 1:
        lines += ["We propose: " + sheet_line(move.sheets[0], order) + ".", ""]
    elif move.sheets:
        lines.append("We propose one of these packages:")
        for i, s in enumerate(move.sheets):
            lines.append(f"Option {'ABC'[i]}: " + sheet_line(s, order) + ".")
        lines.append("")
    if move.ask:
        lines += [move.ask, ""]
    lines += ["Kind regards", "Hollerau Pumpenwerke GmbH, Purchasing"]
    refs = []
    for c in move.claims:
        for r in c.get("refs", []):
            if r not in refs:
                refs.append(r)
    return {
        "subject": subject_of(case, t),
        "body": "\n".join(lines),
        "term_sheets": [{"terms": dict(s)} for s in move.sheets],
        "refs": refs[:20],
        **({"attachments": list(move.attachments)} if move.attachments else {}),
    }
