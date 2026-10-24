"""The arena's claim checks, as the API documents them: per sentence that cites IDs, the figure
in that sentence against the data (±0.5 pp for %, else ±0.5 %), at most 3 checks per message,
each ID once per case (a rival's quote or offer once per supplier)."""

import re

ID_RE = re.compile(r"\b(?:idx|quote|offer|sc|qn|po|cost|fc|news|doc|ctr):[\w.:@#/-]*\w")
NUM = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?")
CONTEXT = ("sup:", "part:")


def sentences(body):
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", body or "") if s.strip()]


def numbers(text):
    clean = ID_RE.sub(" ", text)
    clean = re.sub(r"\b\d{4}-\d{2}(-\d{2})?\b", " ", clean)  # dates are not figures
    return [float(x) for x in NUM.findall(clean)]


def kind_of(ids):
    heads = {i.split(":", 1)[0] for i in ids}
    if heads == {"idx"}:
        return "driver"
    if "doc" in heads and "ctr" in heads:
        return "notice"
    if heads <= {"po"}:
        return "orders"
    if heads <= {"sc", "qn"}:
        return "scorecard"
    if heads <= {"quote", "offer"}:
        return "rival"
    if heads == {"cost"}:
        return "cost"
    if heads == {"fc"}:
        return "forecast"
    if heads == {"news"}:
        return "news"
    return "other"


def check(body, refs, facts, offers, sid, used, case_quotes=()):
    """[(index, result, kind, ids, figure)] for the arguments of one message."""
    out = []
    seen_ids = set()
    for s in sentences(body):
        ids = [i for i in ID_RE.findall(s) if not i.startswith(CONTEXT)]
        if not ids:
            continue
        seen_ids |= set(ids)
        out.append((s, ids))
    extra = [r for r in refs or [] if r not in seen_ids and not r.startswith(CONTEXT) and ID_RE.fullmatch(r)]
    for r in extra:
        out.append(("", [r]))
    results = []
    for i, (s, ids) in enumerate(out[:3]):
        kind = kind_of(ids)
        res = judge(s, ids, kind, facts, offers, sid, case_quotes)
        key = [(sid, x) if x.startswith(("quote:", "offer:")) else x for x in ids]
        if res == "verified" and any(k in used for k in key):
            res = "duplicate"
        if res in ("verified", "irrelevant"):
            used.update(key)
        nums = numbers(s)
        results.append({"index": i, "result": res, "kind": kind, "ids": ids, "figure": nums[0] if nums else None})
    return results


def judge(s, ids, kind, facts, offers, sid, case_quotes):
    def fact(i):
        return offers.get(i) if i.startswith("offer:") else facts.get(i)

    if any(fact(i) is None for i in ids):
        return "contradicted"
    nums = numbers(s)
    if kind == "driver":
        if len(ids) != 2:
            return "irrelevant"
        a, b = sorted((facts[i] for i in ids), key=lambda f: f["month"])
        if a["series"] != b["series"] or not a["value"]:
            return "irrelevant"
        ya, ma = map(int, a["month"].split("-"))
        yb, mb = map(int, b["month"].split("-"))
        if (yb - ya) * 12 + (mb - ma) > 12:
            return "irrelevant"
        ch = (b["value"] / a["value"] - 1) * 100
        return "verified" if not nums or any(abs(n - ch) <= 0.5 for n in nums) else "contradicted"
    if kind == "notice":
        doc = next(facts[i] for i in ids if i.startswith("doc:"))
        if doc.get("supplier") not in (None, sid):
            return "irrelevant"
        days = doc.get("days")
        return "verified" if days is None or not nums or any(abs(n - days) < 0.5 for n in nums) else "contradicted"
    if kind == "rival":
        f = fact(ids[0])
        price = f.get("unit_price") if isinstance(f, dict) else None
        if price is None:
            return "irrelevant"
        if ids[0].startswith("quote:") and ids[0] not in case_quotes:
            return "irrelevant"
        return (
            "verified" if not nums or any(abs(n - price) <= max(0.01, price * 0.005) for n in nums) else "contradicted"
        )
    f = fact(ids[0])
    if kind in ("orders", "scorecard", "news") and f.get("supplier") not in (None, sid):
        return "irrelevant"
    if kind == "cost":
        v = f.get("unit_cost")
        return "verified" if not nums or any(abs(n - v) <= max(0.01, v * 0.005) for n in nums) else "contradicted"
    if kind == "forecast":
        v = f.get("change_pct")
        return "verified" if not nums or any(abs(n - v) <= 0.5 for n in nums) else "contradicted"
    if kind == "scorecard":
        vals = [facts[i] for i in ids]
        otd = sum(x.get("otd_pct") or 0 for x in vals) / len(vals)
        ppm = sum(x.get("ppm") or 0 for x in vals) / len(vals)
        ok = not nums or any(abs(n - otd) <= 0.5 or abs(n - ppm) <= max(0.01, ppm * 0.005) for n in nums)
        return "verified" if ok else "contradicted"
    if kind == "orders":
        parts = {facts[i].get("part") for i in ids}
        if len(parts) > 1 or not 2 <= len(ids) <= 8:
            return "irrelevant"
        prices = [facts[i].get("unit_price") for i in ids]
        ok = not nums or any(abs(n - p) <= max(0.01, p * 0.005) for n in nums for p in prices)
        return "verified" if ok else "contradicted"
    return "verified"
