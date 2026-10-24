"""The data package, the Sunday release and a case's own `facts` as one index. Every table is the
package's SQLite rows plus the release's CSV rows of the same name (the old agent's SQL never saw
the release), and every citable ID resolves through facts.json + release facts + case facts."""

import csv
import json
import math
import os
import re
import sqlite3
import threading
from functools import cached_property
from pathlib import Path

from .. import flags
from ..model.profile import traits_in

RELEASE = "release-sunday"


class World:
    def __init__(self, root, release=None):
        given = Path(root).expanduser().absolute()
        self.root = given.resolve()
        self.facts = json.loads((self.root / "facts.json").read_text(encoding="utf-8"))
        # next to the package as given (data/release-sunday) and next to where a symlink points
        cands = [Path(release)] if release else [given / RELEASE, given.parent / RELEASE, self.root / RELEASE,
                                                 self.root.parent / RELEASE]  # fmt: skip
        if os.environ.get("ARENA_RELEASE"):
            cands.insert(0, Path(os.environ["ARENA_RELEASE"]).expanduser())
        self.release = next((p.resolve() for p in cands if (p / "facts.json").is_file()), None)
        if self.release:
            self.facts = {**self.facts, **json.loads((self.release / "facts.json").read_text(encoding="utf-8"))}
        db = next(iter(sorted(self.root.rglob("*.sqlite"))), None)
        self.db = sqlite3.connect(f"file:{db}?mode=ro", uri=True, check_same_thread=False) if db else None
        if self.db:
            self.db.row_factory = sqlite3.Row
        self._tables: dict = {}
        self._lock = threading.RLock()

    PRELOAD = ("suppliers", "supplier_intel", "documents", "news", "quotes", "scorecards", "should_cost",
               "cost_drivers")  # fmt: skip

    @classmethod
    def load(cls, root):
        w = cls(root)
        for name in cls.PRELOAD:  # before cases share it across threads
            w.table(name)
        _ = w.series
        return w

    def table(self, name):
        with self._lock:
            return self._table(name)

    def _table(self, name):
        if name not in self._tables:
            rows = []
            if self.db is not None:
                try:
                    rows = [dict(r) for r in self.db.execute(f'SELECT * FROM "{name}"')]
                except sqlite3.Error:
                    rows = []
            if self.release:
                for p in sorted(self.release.rglob(f"{name}.csv")):
                    with p.open(encoding="utf-8", newline="") as f:
                        rows += [{**r, "_root": str(self.release)} for r in csv.DictReader(f)]
            self._tables[name] = rows
        return self._tables[name]

    @cached_property
    def series(self):
        """series -> sorted [(month, value)] from facts (what the arena checks)."""
        out: dict = {}
        for k, v in self.facts.items():
            if k.startswith("idx:") and isinstance(v, dict) and v.get("value") is not None:
                out.setdefault(v["series"], []).append((v["month"], float(v["value"])))
        for s in out.values():
            s.sort()
        return out

    def text(self, row):
        """A document's text: its file in the package or the release, else its own fields."""
        for col in ("body", "text", "content"):
            if row.get(col):
                return str(row[col])
        f = row.get("file") or row.get("path")
        if f:
            base = Path(row.get("_root") or self.root)
            p = (base / f).resolve()
            if p.is_file() and p.is_relative_to(base):
                return p.read_text(encoding="utf-8", errors="replace")
        return " ".join(str(row.get(k) or "") for k in ("subject", "headline", "title", "topic"))

    def for_case(self, case):
        return CaseWorld(self, case)


def _f(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


class CaseWorld:
    """The world as one case sees it: plus the case's own facts."""

    def __init__(self, world: World, case):
        self.w, self.case = world, case
        self.case_facts = dict(case.get("facts") or {})
        self.parts = list(case.get("parts") or [])

    def fact(self, ref):
        return self.case_facts.get(ref) or self.w.facts.get(ref)

    def known(self, ref):
        return self.fact(ref) is not None

    def series(self, name):
        pts = dict(self.w.series.get(name, []))
        for k, v in self.case_facts.items():
            if k.startswith("idx:") and isinstance(v, dict) and v.get("series") == name and v.get("value") is not None:
                pts[v["month"]] = float(v["value"])
        return sorted(pts.items())

    def rows(self, table, **eq):
        return [r for r in self.w.table(table) if all(str(r.get(k)) == str(v) for k, v in eq.items())]

    def texts(self, sid):
        """Letters, notes, e-mails and news about this supplier (package, release, case facts),
        plus its quote remarks."""
        out = []
        for r in self.w.table("documents") + self.w.table("news"):
            if r.get("supplier_id") == sid:
                out.append(self.w.text(r))
        for r in self.w.table("quotes"):
            if r.get("supplier_id") == sid and r.get("remarks"):
                out.append(str(r["remarks"]))
        for _k, v in self.case_facts.items():
            if isinstance(v, dict) and v.get("supplier") == sid:
                out.append(" ".join(str(x) for x in v.values() if isinstance(x, str)))
        return out

    def profile(self, sid, case=None):
        """What the data says about a supplier: owner type, traits, cost of money, should-cost,
        real defect rate, cost drivers and what an index clause on them is worth to it."""
        prof = {"traits": set(), "drivers": [], "clause_moves": {}}
        intel = self.rows("supplier_intel", supplier_id=sid)
        if intel:
            prof["owner_type"] = intel[0].get("owner_type") or None
            prof["credit_index"] = _f(intel[0].get("credit_index"))
            prev = _f(intel[0].get("credit_index_12m_ago"))
            if prof["credit_index"] and prev and prof["credit_index"] > prev + 15:
                prof["traits"].add("cash_need")  # a worsening credit index (higher is worse)
        sup = self.rows("suppliers", id=sid)
        if sup:
            prof["country"] = sup[0].get("country") or None
            pct, days = _f(sup[0].get("skonto_pct")), _f(sup[0].get("skonto_days"))
            pay = _f(sup[0].get("payment_days")) or 30
            if pct and days is not None and pay > days:
                # a cash discount of pct % for paying (pay - days) days early, as a yearly rate
                prof["rate"] = min(0.25, pct / 100 * 360 / max(10.0, pay - days) / 2)
                prof["traits"].add("cash_need")
        prof["traits"] |= traits_in("\n".join(self.texts(sid)))
        sc = sorted((r for r in self.rows("scorecards", supplier_id=sid)), key=lambda r: r.get("quarter") or "")
        if sc:
            prof["ppm"] = _f(sc[-1].get("ppm"))
            prof["otd"] = _f(sc[-1].get("otd_pct"))
        for part in self.parts:
            own = generic = None
            for r in self.rows("should_cost", part_id=part):
                if r.get("supplier_id") in (None, "", sid):
                    prof["should_cost"] = _f(r.get("unit_cost"))
                    if r.get("supplier_id") == sid:
                        own = r
                    else:
                        generic = r
            if flags.on("FC_V2") and (own or generic):
                # the row modelled on this supplier's own plant first (error about 5 %), else the
                # German cost base (error 5-15 %, needs the country factor): company.md
                r = own or generic
                prof["should_cost"] = _f(r.get("unit_cost"))
                prof["sc_kind"] = "own" if own else "generic"
                prof["sc_base"] = r.get("cost_base") or "DE"
            fid = f"cost:{part.split(':', 1)[1]}"
            if prof.get("should_cost") is None and self.fact(fid):
                f = self.fact(fid)
                prof["should_cost"] = _f(f.get("unit_cost"))
                prof["sc_kind"] = "own" if (_f(f.get("confidence_pct")) or 99) <= 5 else "generic"
                prof["sc_base"] = f.get("cost_base") or "DE"
            for r in self.rows("cost_drivers", part_id=part):
                prof["drivers"].append((r.get("series_id"), _f(r.get("weight")) or 0.0))
        if len(self.parts) > 1:
            prof["should_cost"] = None  # a bundle's price is not one part's should-cost
        for clause in (self.case.get("term_options") or {}).get("index_clause") or []:
            if clause and clause.get("series"):
                mv = self.move_12m(clause["series"])
                if mv is not None:
                    prof["clause_moves"][clause["series"]] = mv
        prof["traits"] = sorted(prof["traits"])
        return prof

    def move_12m(self, series):
        pts = self.series(series)
        if len(pts) < 13:
            return None
        return pts[-1][1] / pts[-13][1] - 1


def month_diff(a, b):
    ya, ma = map(int, a.split("-"))
    yb, mb = map(int, b.split("-"))
    return (yb - ya) * 12 + (mb - ma)


ID_SAFE = re.compile(r"^[\w.:@#/-]+$")
