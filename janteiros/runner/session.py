"""One case, start to end. The arena serialises calls within a session, so one message goes out at
a time: to the thread whose clock has run longest among those the policy wants to talk to, with a
keepalive to a waiting thread before its 60 s run out. Every send passes the validator; a refused
or late message is recovered from the arena's own thread view. Close before a wave's cap."""

import json
import re
import time

import httpx

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
MAX_ERRORS = 8
PANIC_TIMEOUTS = 2  # timeouts in a case before we close on the best valid sheet


class Ended(Exception):
    def __init__(self, result):
        self.result = result


class CaseRunner:
    def __init__(self, arena, case, policy, log, deadline=None, final=False, mono=time.monotonic, wall=time.time):
        self.arena, self.case, self.policy, self.log = arena, case, policy, log
        self.mono, self.wall = mono, wall
        self.deadline = deadline  # fn -> self.wall() of the wave's cap, or None
        self.final = final
        self.session_id = None
        self.threads: dict[str, Thread] = {}
        self.send_times: list[float] = []
        self.errors = 0
        self.timeouts = 0
        self.broken = False  # the policy failed: close with the plain rule
        self.sleep = time.sleep
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
        now = self.mono()
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
        now = self.mono()
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
            t.replied_at = self.mono() - (CLOCK_S - float(seconds_left))
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
        left = cap - self.wall()
        return left < max(CLOSE_BEFORE_CAP_S, self.slowest + ROUND_MARGIN_S), left

    def budget(self, target: Thread):
        """Seconds we may spend composing the message to `target`: its own clock must not run out,
        and every other live thread must still get its turn after this send."""
        now, s = self.mono(), self.send_s()
        left = [CLOCK_S - MARGIN_S - target.age(now)]
        for o in self.live():
            if o is not target and self.policy.keep_alive(o):
                left.append(CLOCK_S - MARGIN_S - s - o.age(now))
        cap = self.deadline() if callable(self.deadline) else self.deadline
        if cap:
            left.append(cap - self.wall() - max(CLOSE_BEFORE_CAP_S, self.slowest + ROUND_MARGIN_S) - s)
        return max(0.0, min(left))

    def run(self):
        try:
            resumed = self.open()
            try:
                self.policy.setup(self.threads, self.log, resumed=resumed)
            except Exception as e:  # noqa: BLE001 - play on with the plain close rather than lose the case
                self.log.emit("error", where="setup", error=repr(e)[:500])
                self.broken = True
            for _ in range(MAX_LOOPS):
                try:
                    out = self.tick()
                except Ended:
                    raise
                except (ArenaError, httpx.TransportError) as e:
                    if isinstance(e, ArenaError) and e.status in (401, 403, 404):
                        raise
                    self.errors += 1
                    self.log.emit("error", where="loop", error=repr(e)[:500], n=self.errors)
                    if self.errors > MAX_ERRORS:
                        raise
                    self.recover()
                    continue
                except Exception as e:  # noqa: BLE001 - a bug in the policy must not lose the case
                    self.errors += 1
                    self.broken = True
                    self.log.emit("error", where="policy", error=repr(e)[:500], n=self.errors)
                    continue
                if out is not None:
                    return out
            return self.close(self.fallback_close()) or self.finish()
        except Ended as e:
            return self.done(e.result)

    def tick(self):
        """One decision: close, or one message. Returns the result once the case has ended."""
        if not self.live():
            out = self.finish()
            if out is None:  # every thread ended but the session did not: close it ourselves
                return self.close(self.fallback_close())
            return out
        near, left = self.near_cap()
        if near:
            self.log.emit("clock", what="close_before_cap", seconds_left=left)
        if self.broken:
            return self.close(self.fallback_close()) or self.finish()
        mv = self.policy.close_decision(self.threads, final=near)
        if mv is not None:
            return self.close(mv)
        target = self.pick()
        if target is None:
            mv = self.policy.close_decision(self.threads, final=True)
            return self.close(mv) if mv else None
        self.step(target)
        return None

    def fallback_close(self):
        """Without our policy: the cheapest standing sheet that passes the gate, else walk."""
        from ..policy.base import Policy

        return Policy.close_decision(Policy(self.case), self.threads, final=True)

    def recover(self):
        """After a network or server error: the arena's own view of every thread."""
        self.sleep(min(5.0, 0.5 * 2**self.errors))
        lefts, st = self.seconds_left()
        if st.get("status") == "ended":
            raise Ended(st.get("result") or {"deal": False, "end_reason": st.get("end_reason")})
        for t in self.threads.values():
            try:
                self.refresh(t, lefts.get(t.sid))
            except ArenaError:
                pass

    def pick(self):
        """The thread to message now: a keepalive if a waiting thread's clock would lapse, else the
        active thread whose clock has run longest. With more active threads than one clock can
        serve (the arena serialises a session's calls), the least promising stops."""
        now, s = self.mono(), self.send_s()
        live = self.live()
        active = [t for t in live if self.policy.wants_message(t) and t.moves_left(self.case) > 0]
        # a thread waits for one send to every other active thread before its own turn
        while len(active) > 1 and (len(active) - 1) * s > CLOCK_S - MARGIN_S - 2.0:
            worst = max(active, key=self.policy.priority)
            worst.pruned = True
            active.remove(worst)
            self.log.emit("clock", what="prune_for_time", supplier_id=worst.sid, send_s=round(s, 1),
                          active=len(active) + 1)  # fmt: skip
        waiting = [t for t in live if t not in active and self.policy.keep_alive(t) and t.moves_left(self.case) > 1]
        # a waiting thread needs a message now if one more send elsewhere would let its clock lapse
        due = [t for t in waiting if active and t.age(now) + s > CLOCK_S - MARGIN_S]
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
            t.pruned = True  # nothing more to offer here; its standing sheet stays a candidate
            self.log.emit("model_update", supplier_id=t.sid, pruned=True, stop="no move")
            return
        msg = self.policy.compose(t, move, budget)
        problems = self.check(t, msg, move)
        self.log.emit("validate", supplier_id=t.sid, ok=not problems, problems=problems, reason=move.reason)
        if problems and any(p.startswith("attachment ") for p in problems):
            msg.pop("attachments", None)  # the message goes without the attachment
            problems = [p for p in problems if not p.startswith("attachment ")]
        bad = {int(m.group(1)) for p in problems if (m := re.match(r"sheet (\d+):", p))}
        if problems and bad and all(re.match(r"sheet \d+:", p) for p in problems) and len(bad) < len(move.sheets):
            move.sheets = [sh for i, sh in enumerate(move.sheets) if i not in bad]  # drop just those
            msg = self.policy.compose(t, move, 0.0)
            problems = self.check(t, msg, move)
            self.log.emit("validate", supplier_id=t.sid, ok=not problems, problems=problems, reason="dropped sheets")
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
                      age=round(t.age(self.mono()), 1), keepalive=move.keepalive,
                      attachments=[a.get("name") for a in msg.get("attachments") or []])  # fmt: skip
        t0 = self.mono()
        try:
            reply = self.arena.send_message(self.session_id, t.sid, msg)
        except ArenaError as e:
            took = self.mono() - t0
            if e.status == 409:
                self.log.emit("timeout", supplier_id=t.sid, detail=str(e.detail)[:300])
                return self.after_409(t)
            if e.status == 422 and msg.get("attachments"):
                # the arena refused the attachment: the same message without it (no round was used)
                self.log.emit("error", supplier_id=t.sid, where="send", status=422, detail=str(e.detail)[:500],
                              retry="without attachments")  # fmt: skip
                msg = {k: v for k, v in msg.items() if k != "attachments"}
                return self.send(t, msg, move)
            if e.status == 422:
                t.rejected += 1
                self.log.emit("error", supplier_id=t.sid, where="send", status=422, detail=str(e.detail)[:500])
                if t.rejected >= 3:
                    t.pruned = True
                return None
            raise
        took = self.mono() - t0
        self.send_times.append(took)
        self.slowest = max(self.slowest, took + 2.0)
        if "result" in reply and reply.get("result"):
            t.messages += [{**msg, "from": "buyer"}, reply]
            raise Ended(reply["result"])
        t.messages += [{**msg, "from": "buyer"}, self.fetch(reply)]
        t.replied_at = self.mono()
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
        self.timeouts += 1
        if self.timeouts >= PANIC_TIMEOUTS:
            self.broken = True  # the clocks are not holding: close on the best valid sheet now
            self.log.emit("clock", what="panic_close", timeouts=self.timeouts)
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
        """The result once the session has ended; None while it is still open."""
        try:
            st = self.arena.session(self.session_id)
        except ArenaError:
            return None
        if st.get("status") != "ended":
            return None
        return self.done(st.get("result") or {"deal": False, "end_reason": st.get("end_reason")})

    def done(self, result):
        result = dict(result or {})
        trace = None
        try:
            st = self.arena.session(self.session_id)
            trace = (st.get("result") or {}).get("trace")
            result.setdefault("end_reason", st.get("end_reason"))
            result["rounds"] = st.get("rounds")
            self.save_session(st)
        except Exception:  # noqa: BLE001
            pass
        self.log.emit("result", result={k: v for k, v in result.items() if k != "trace"},
                      reasons=_reasons(trace), timeouts=sum(t.timeouts for t in self.threads.values()))  # fmt: skip
        result["session_id"] = self.session_id
        return result

    def save_session(self, st):
        """The arena's full session (trace with the supplier's reasons) and, outside the final,
        its back office, next to our events: runs/<run>/sessions/<case>_<session>.json."""
        root = getattr(getattr(self.log, "log", None), "dir", None)
        if root is None:
            return
        out = {"session": st, "backoffice": None}
        if not self.final:  # the back office opens after the final only
            try:
                out["backoffice"] = self.arena.backoffice(self.session_id)
            except Exception:  # noqa: BLE001
                pass
        d = root / "sessions"
        d.mkdir(exist_ok=True)
        name = f"{self.case['case_id'].replace(':', '_')}_{self.session_id}.json"
        (d / name).write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


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
