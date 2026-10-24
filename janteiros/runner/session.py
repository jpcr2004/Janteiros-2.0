"""One case, start to end. The arena serialises calls within a session, so one message goes out at
a time: to the thread whose clock has run longest among those the policy wants to talk to, with a
keepalive to a waiting thread before its 60 s run out. Every send passes the validator; a refused
or late message is recovered from the arena's own thread view. Close before a wave's cap."""

import re
import time

from ..arena.client import ArenaError
from ..arena.docs import read_document
from ..econ.cost import breakdown, cost
from ..validate.check import validate
from .state import CLOCK_S, Thread

RUNNING = re.compile(r"one session per case in the final: (\S+)")
MARGIN_S = 6.0  # kept free on every clock for building and delivering a message
FIRST_SEND_S = 25.0  # a supplier reply until we have measured one (19 s median, 28 s p90)
CLOSE_BEFORE_CAP_S, ROUND_MARGIN_S = 90.0, 15.0
CLOSE_TRIES = 4
MAX_LOOPS = 400


class Ended(Exception):
    def __init__(self, result):
        self.result = result


class CaseRunner:
    def __init__(self, arena, case, policy, log, deadline=None, final=False):
        self.arena, self.case, self.policy, self.log = arena, case, policy, log
        self.deadline = deadline  # fn -> time.time() of the wave's cap, or None
        self.final = final
        self.session_id = None
        self.threads: dict[str, Thread] = {}
        self.send_times: list[float] = []
        self.slowest = 0.0
        self.docs: dict[str, str] = {}

    # -- the arena ----------------------------------------------------------------------------
    def open(self):
        cid = self.case["case_id"]
        try:
            r = self.arena.open_session(cid)
        except ArenaError as e:
            running = RUNNING.search(str(e.detail)) if e.status == 409 else None
            if not running:
                raise
            return self.resume(running.group(1))
        self.session_id = r["session_id"]
        self.case = r.get("case") or self.case
        now = time.monotonic()
        for sid, msgs in r["threads"].items():
            t = Thread(sid, replied_at=now)
            for m in msgs:
                t.messages.append(self.fetch(m))
            self.threads[sid] = t
        self.log = self.log.bind(session_id=self.session_id)
        self.log.emit("case_open", case=self.case, suppliers=list(self.threads), resumed=False)
        for t in self.threads.values():
            self.emit_quote(t)
        return False

    def resume(self, session_id):
        """Pick a running session up again (after a crash): every thread from the arena."""
        self.session_id = session_id
        self.log = self.log.bind(session_id=session_id)
        state = self.arena.session(session_id)
        if state.get("status") == "ended":
            raise Ended(state.get("result") or {"deal": False, "end_reason": state.get("end_reason")})
        now = time.monotonic()
        for sid, info in (state.get("threads") or {}).items():
            self.threads[sid] = self.refresh(Thread(sid, replied_at=now), info.get("seconds_left"))
        self.log.emit("case_open", case=self.case, suppliers=list(self.threads), resumed=True)
        return True

    def refresh(self, t: Thread, seconds_left=None):
        view = self.arena.thread(self.session_id, t.sid)
        t.messages = [self.fetch(m) for m in view.get("messages") or []]
        t.closed = view.get("closed")
        t.timeouts = len(view.get("timeouts") or [])
        if seconds_left is not None:
            t.replied_at = time.monotonic() - (CLOCK_S - float(seconds_left))
        return t

    def fetch(self, m):
        if m.get("from") == "buyer":
            return m
        for att in m.get("attachments") or []:
            aid = att.get("attachment_id")
            if not aid or att.get("text"):
                continue
            if aid not in self.docs:
                try:
                    self.docs[aid] = read_document(att.get("name") or "", self.arena.attachment(self.session_id, aid))
                except Exception as e:  # noqa: BLE001 - a missing document must not stop the case
                    self.log.emit("error", where="attachment", error=str(e)[:300])
                    self.docs[aid] = ""
            att["text"] = self.docs[aid]
        return m

    def seconds_left(self):
        try:
            st = self.arena.session(self.session_id)
            return {s: i.get("seconds_left") for s, i in (st.get("threads") or {}).items()}, st
        except ArenaError:
            return {}, {}

    # -- events -------------------------------------------------------------------------------
    def emit_quote(self, t):
        sid, terms = t.standing
        std = t.std
        self.log.emit("quote", supplier_id=t.sid, offer_id=sid, terms=terms, standard=std,
                      c=cost(self.case, terms, std), breakdown=breakdown(self.case, terms, std),
                      status=t.status, body=t.last.get("body", "")[:4000],
                      attachments=[a.get("name") for a in t.last.get("attachments") or []])  # fmt: skip

    # -- the loop -----------------------------------------------------------------------------
    def send_s(self):
        if not self.send_times:
            return FIRST_SEND_S
        recent = sorted(self.send_times[-8:])
        return max(recent[len(recent) // 2], recent[-1] * 0.8)

    def live(self):
        return [t for t in self.threads.values() if t.live]

    def near_cap(self):
        cap = self.deadline() if callable(self.deadline) else self.deadline
        if not cap:
            return False, None
        left = cap - time.time()
        return left < max(CLOSE_BEFORE_CAP_S, self.slowest + ROUND_MARGIN_S), left

    def budget(self, target: Thread):
        """Seconds we may spend composing the message to `target`: its own clock must not run out,
        and every other live thread must still get its turn after this send."""
        now, s = time.monotonic(), self.send_s()
        left = [CLOCK_S - MARGIN_S - target.age(now)]
        for o in self.live():
            if o is not target and self.policy.keep_alive(o):
                left.append(CLOCK_S - MARGIN_S - s - o.age(now))
        cap = self.deadline() if callable(self.deadline) else self.deadline
        if cap:
            left.append(cap - time.time() - max(CLOSE_BEFORE_CAP_S, self.slowest + ROUND_MARGIN_S) - s)
        return max(0.0, min(left))

    def run(self):
        try:
            resumed = self.open()
            self.policy.setup(self.threads, self.log, resumed=resumed)
            for _ in range(MAX_LOOPS):
                if not self.live():
                    return self.finish()
                near, left = self.near_cap()
                if near:
                    self.log.emit("clock", what="close_before_cap", seconds_left=left)
                mv = self.policy.close_decision(self.threads, final=near)
                if mv is not None:
                    out = self.close(mv)
                    if out is not None:
                        return out
                    continue
                target = self.pick()
                if target is None:
                    mv = self.policy.close_decision(self.threads, final=True)
                    out = self.close(mv) if mv else None
                    if out is not None:
                        return out
                    continue
                self.step(target)
            mv = self.policy.close_decision(self.threads, final=True)
            return (self.close(mv) if mv else None) or self.finish()
        except Ended as e:
            return self.done(e.result)

    def pick(self):
        """The thread to message now: a keepalive if a waiting thread's clock would lapse, else the
        active thread whose clock has run longest."""
        now, s = time.monotonic(), self.send_s()
        live = self.live()
        active = [t for t in live if self.policy.wants_message(t) and t.moves_left(self.case) > 0]
        waiting = [t for t in live if t not in active and self.policy.keep_alive(t) and t.moves_left(self.case) > 1]
        due = [t for t in waiting if t.age(now) + s * max(1, len(active)) > CLOCK_S - MARGIN_S - s]
        if due:
            t = max(due, key=lambda t: t.age(now))
            t._keepalive = True
            return t
        if active:
            t = max(active, key=lambda t: t.age(now))
            t._keepalive = False
            return t
        return None

    def step(self, t: Thread):
        keep = getattr(t, "_keepalive", False)
        budget = self.budget(t)
        move = self.policy.keepalive_move(t) if keep else self.policy.next_move(t, budget)
        if move is None:
            return
        msg = self.policy.compose(t, move, budget)
        problems = self.check(t, msg, move)
        self.log.emit("validate", supplier_id=t.sid, ok=not problems, problems=problems, reason=move.reason)
        if problems:
            move = self.policy.safe_move(t, problems)
            msg = self.policy.compose(t, move, 0.0)
            problems = self.check(t, msg, move)
            if problems:
                self.log.emit("validate", supplier_id=t.sid, ok=False, problems=problems, reason="fallback blocked")
                t.pruned = True
                return
        self.send(t, msg, move)

    def check(self, t, msg, move):
        return validate(self.case, msg, t, claims=move.claims, known_ids=self.policy.known_id,
                        allow_above_alt=move.allow_above_alt, allow_at_standing=move.allow_at_standing)  # fmt: skip

    def send(self, t: Thread, msg, move):
        sheets = [s["terms"] for s in msg.get("term_sheets") or []]
        std = t.std
        self.log.emit("send", supplier_id=t.sid, round=t.rounds_used + 1, subject=msg.get("subject"),
                      body=msg.get("body"), sheets=sheets, refs=msg.get("refs", []),
                      c=[cost(self.case, s, std, self.policy.est(t)) for s in sheets],
                      claims=[c.get("sentence") for c in move.claims], reason=move.reason,
                      age=round(t.age(), 1), keepalive=move.keepalive)  # fmt: skip
        t0 = time.monotonic()
        try:
            reply = self.arena.send_message(self.session_id, t.sid, msg)
        except ArenaError as e:
            took = time.monotonic() - t0
            if e.status == 409:
                self.log.emit("timeout", supplier_id=t.sid, detail=str(e.detail)[:300])
                return self.after_409(t)
            if e.status == 422:
                t.rejected += 1
                self.log.emit("error", supplier_id=t.sid, where="send", status=422, detail=str(e.detail)[:500])
                if t.rejected >= 3:
                    t.pruned = True
                return None
            raise
        took = time.monotonic() - t0
        self.send_times.append(took)
        self.slowest = max(self.slowest, took + 2.0)
        if "result" in reply and reply.get("result"):
            t.messages += [{**msg, "from": "buyer"}, reply]
            raise Ended(reply["result"])
        t.messages += [{**msg, "from": "buyer"}, self.fetch(reply)]
        t.replied_at = time.monotonic()
        sid, terms = t.standing
        std = t.std
        self.log.emit("reply", supplier_id=t.sid, round=t.rounds_used, status=reply.get("status"),
                      offer_id=sid, terms=terms, c=cost(self.case, terms, std, self.policy.est(t)),
                      breakdown=breakdown(self.case, terms, std, self.policy.est(t)),
                      missing_fields=reply.get("missing_fields"), claim_results=reply.get("claim_results"),
                      signals=reply.get("signals"), body=(reply.get("body") or "")[:4000], seconds=round(took, 2),
                      attachments=[a.get("name") for a in reply.get("attachments") or []])  # fmt: skip
        self.policy.observe(t, msg, reply, move)
        return reply

    def after_409(self, t: Thread):
        lefts, st = self.seconds_left()
        if st.get("status") == "ended":
            raise Ended(st.get("result") or {"deal": False, "end_reason": st.get("end_reason")})
        try:
            self.refresh(t, lefts.get(t.sid))
        except ArenaError as e:
            self.log.emit("error", where="refresh", detail=str(e.detail)[:300])
        self.policy.observe(t, None, t.last, None)
        return None

    def close(self, mv):
        for _ in range(CLOSE_TRIES):
            t = self.threads.get(mv.sid) if mv.sid else next(iter(self.threads.values()))
            if mv.kind == "accept":
                msg = {"action": {"type": "accept", "term_sheet_id": t.standing[0]}}
                problems = validate(self.case, msg, t)
                if problems:
                    self.log.emit("validate", supplier_id=t.sid, ok=False, problems=problems, reason="accept")
                    return None
                sid, terms = t.standing
                self.log.emit("accept", supplier_id=t.sid, offer_id=sid, terms=terms, reason=mv.reason,
                              c=cost(self.case, terms, t.std, self.policy.est(t)))  # fmt: skip
            else:
                msg = {"action": {"type": "walk_away"}}
                self.log.emit("walk", supplier_id=t.sid, reason=mv.reason)
            try:
                reply = self.arena.send_message(self.session_id, t.sid, msg)
            except ArenaError as e:
                self.log.emit("error", where="close", status=e.status, detail=str(e.detail)[:300])
                if e.status not in (409, 422):
                    raise
                lefts, st = self.seconds_left()
                if st.get("status") == "ended":
                    raise Ended(st.get("result") or {"deal": False, "end_reason": st.get("end_reason")}) from None
                for o in self.threads.values():
                    try:
                        self.refresh(o, lefts.get(o.sid))
                    except ArenaError:
                        pass
                mv = self.policy.close_decision(self.threads, final=True)
                if mv is None:
                    return None
                continue
            raise Ended(reply.get("result") or reply)
        return None

    def finish(self):
        try:
            st = self.arena.session(self.session_id)
        except ArenaError:
            st = {}
        return self.done(st.get("result") or {"deal": False, "end_reason": st.get("end_reason")})

    def done(self, result):
        result = dict(result or {})
        trace = None
        try:
            st = self.arena.session(self.session_id)
            trace = (st.get("result") or {}).get("trace")
            result.setdefault("end_reason", st.get("end_reason"))
            result["rounds"] = st.get("rounds")
        except Exception:  # noqa: BLE001
            pass
        self.log.emit("result", result={k: v for k, v in result.items() if k != "trace"},
                      reasons=_reasons(trace), timeouts=sum(t.timeouts for t in self.threads.values()))  # fmt: skip
        result["session_id"] = self.session_id
        return result


def _reasons(trace):
    if not trace:
        return None
    out = []
    for step in trace:
        if step.get("actor") != "buyer":
            mv = step.get("move") or {}
            out.append({"round": step.get("round"), "actor": step.get("actor"), "status": mv.get("status"),
                        "terms": mv.get("terms"), "reasons": step.get("reasons")})  # fmt: skip
    return out
