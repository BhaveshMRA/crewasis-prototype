"""Card lifecycle: seed, accept, execute, approve, reject, ask again, hand off.

Every change is logged to card_events. Invalid moves raise ValueError (or PermissionError when approval is
missing), so a double click or a stale page can never put a card into an impossible state.
"""
import db
import engine


class InvalidMove(ValueError):
    pass


# ---------------------------------------------------------------- learning loop
# "Not relevant" is a one-tap signal borrowed from Instagram's "Not interested", but scoped to the team that
# tapped it: similar cards (same category) drop for that team only; every other team's ranking is untouched.
LEARN_RATE = 0.8      # each tap multiplies that team's weight for the category by 0.8
LEARN_FLOOR = 0.3     # never below 30%, so a signal can't disappear completely


def learned_multiplier(con, role, category, counts=None) -> float:
    n = (counts if counts is not None else db.feedback_counts(con)).get((role, category), 0)
    return max(LEARN_FLOOR, LEARN_RATE ** n)


def ranking(con, c: dict, counts=None) -> float:
    """What a team's board sorts by: base × team weight × what this team has taught it."""
    if c["kind"] != "work":
        return c["relevance_score"]
    return c["relevance_score"] * learned_multiplier(con, c["owner_role"], c["category"], counts)


def human(con, eid, role, action, evidence=()):
    db.add_trace(con, eid, [engine.trace_row(f"{role} (human)", action, evidence=evidence, kind="human")])


def _card(con, card_id, kind="work") -> dict:
    c = db.card(con, card_id)
    if c is None:
        raise InvalidMove(f"Card {card_id} doesn't exist.")
    if kind and c["kind"] != kind:
        raise InvalidMove(f"Card {card_id} is an {c['kind']} card.")
    return c


def _request_approval(con, work: dict) -> int:
    rel = engine.relevance(work["base"], work["category"], "Strategy")
    aid = db.add_card(con, engagement_id=work["engagement_id"], fact_id=work["fact_id"], play_id=work.get("play_id"),
                      kind="approval", parent_card_id=work["id"], category=work["category"], owner_role="Strategy",
                      state="Pending approval", base=work["base"], relevance_score=rel,
                      evidence=f"{work['owner_role']} wants to: {work['suggested_action']}",
                      suggested_action=f"Approve or reject ({work['gate_rule']})", requires_approval=False,
                      gate_rule=work["gate_rule"])
    db.log(con, aid, "approval_requested", from_role=work["owner_role"], to_role="Strategy",
           to_state="Pending approval")
    return aid


def seed(con, e: engine.Engagement):
    """Create the work cards for an engagement, plus an approval card for each gated one."""
    if e.id is not None and db.cards(con, engagement_id=e.id):
        return  # already seeded
    for c in e.cards:
        cid = db.add_card(con, engagement_id=e.id, kind="work", state="Surfaced",
                          requires_approval=bool(c["gate_rule"]), **c)
        if c["gate_rule"]:
            _request_approval(con, db.card(con, cid))
    con.commit()


def tag(c):
    return c["play_id"] or c["fact_id"]


def accept(con, card_id):
    c = _card(con, card_id)
    if c["state"] != "Surfaced":
        raise InvalidMove(f"Only a Surfaced card can be accepted; this one is {c['state']}.")
    db.update_card(con, card_id, state="Drafted")
    db.log(con, card_id, "drafted", c["owner_role"], c["owner_role"], c["state"], "Drafted")
    human(con, c["engagement_id"], c["owner_role"], f"Accepted {tag(c)}: {c['suggested_action']}", [tag(c)])
    con.commit()


def not_relevant(con, card_id):
    """One tap: the card is set aside and this team's ranking learns to push similar cards down."""
    c = _card(con, card_id)
    if c["state"] not in ("Surfaced", "Drafted"):
        raise InvalidMove(f"Only an open card can be marked not relevant; this one is {c['state']}.")
    db.add_feedback(con, c)
    old = db.open_approval(con, card_id)
    if old:
        db.update_card(con, old["id"], state="Withdrawn")
        db.log(con, old["id"], "withdrawn", "Strategy", "Strategy", "Pending approval", "Withdrawn")
    db.update_card(con, card_id, state="Dismissed")
    db.log(con, card_id, "not_relevant", c["owner_role"], c["owner_role"], c["state"], "Dismissed")
    m = learned_multiplier(con, c["owner_role"], c["category"])
    human(con, c["engagement_id"], c["owner_role"],
          f"Marked {tag(c)} not relevant; {c['owner_role']}'s {c['category']} cards now rank at ×{m:.2f}", [tag(c)])
    con.commit()


def restore(con, card_id):
    """Undo a 'not relevant' tap: the card comes back and the team's ranking forgets the signal."""
    c = _card(con, card_id)
    if c["state"] != "Dismissed":
        raise InvalidMove("Only a card marked not relevant can be restored.")
    db.undo_feedback(con, card_id)
    before = next((ev["from_state"] for ev in reversed(db.events(con, card_id)) if ev["event"] == "not_relevant"),
                  "Surfaced")
    db.update_card(con, card_id, state=before)
    db.log(con, card_id, "restored", c["owner_role"], c["owner_role"], "Dismissed", before)
    c = db.card(con, card_id)
    a = db.latest_approval(con, card_id)
    if c["requires_approval"] and not (a and a["state"] in ("Pending approval", "Approved")):
        _request_approval(con, c)
    human(con, c["engagement_id"], c["owner_role"], f"Restored {tag(c)}", [tag(c)])
    con.commit()


def can_execute(con, c: dict) -> bool:
    if not c["requires_approval"]:
        return True
    a = db.latest_approval(con, c["id"])
    return bool(a and a["state"] == "Approved")


def execute(con, card_id):
    c = _card(con, card_id)
    if c["state"] != "Drafted":
        raise InvalidMove(f"Only a Drafted card can be executed; this one is {c['state']}.")
    if not can_execute(con, c):
        raise PermissionError("This card needs Strategy's approval first.")
    db.update_card(con, card_id, state="Executed")
    db.log(con, card_id, "executed", c["owner_role"], c["owner_role"], c["state"], "Executed")
    human(con, c["engagement_id"], c["owner_role"], f"Executed {tag(c)} (simulated): {c['suggested_action']}",
          [tag(c)])
    con.commit()


def _pending(con, approval_id) -> dict:
    a = _card(con, approval_id, kind="approval")
    if a["state"] != "Pending approval":
        raise InvalidMove(f"This approval was already {a['state'].lower()}.")
    return a


def approve(con, approval_id):
    a = _pending(con, approval_id)
    w = db.card(con, a["parent_card_id"])
    db.update_card(con, approval_id, state="Approved")
    db.log(con, approval_id, "approved", "Strategy", "Strategy", a["state"], "Approved")
    db.update_card(con, w["id"], state="Executed")
    db.log(con, w["id"], "executed", w["owner_role"], w["owner_role"], w["state"], "Executed")
    human(con, w["engagement_id"], "Strategy", f"Approved {tag(w)} ({a['gate_rule']}); it ran (simulated)", [tag(w)])
    con.commit()


def reject(con, approval_id):
    a = _pending(con, approval_id)
    w = db.card(con, a["parent_card_id"])
    db.update_card(con, approval_id, state="Rejected")
    db.log(con, approval_id, "rejected", "Strategy", "Strategy", a["state"], "Rejected")
    db.update_card(con, w["id"], state="Drafted")
    db.log(con, w["id"], "rejected", "Strategy", w["owner_role"], w["state"], "Drafted")
    human(con, w["engagement_id"], "Strategy", f"Rejected {tag(w)}; sent back to {w['owner_role']}", [tag(w)])
    con.commit()


def ask_again(con, card_id):
    c = _card(con, card_id)
    a = db.latest_approval(con, card_id)
    if not c["requires_approval"] or not a or a["state"] != "Rejected":
        raise InvalidMove("Only a card whose approval was rejected can ask again.")
    _request_approval(con, c)
    con.commit()


def handoff(con, card_id, to_role, e: engine.Engagement, llm=None):
    """Re-score (RoleWeight), re-word (Frame) and re-check (Gate) the card for its new team."""
    c = _card(con, card_id)
    if to_role not in engine.ROLES:
        raise InvalidMove(f"Unknown team {to_role!r}.")
    if to_role == c["owner_role"]:
        raise InvalidMove("The card already belongs to that team.")
    if c["state"] in ("Executed", "Dismissed"):
        raise InvalidMove(f"A {c['state'].lower()} card can't be handed off.")
    action, written_by, intent, flag = engine.frame_for_role(c, to_role, e, llm, c["engagement_id"])
    rule = engine.gate(action, to_role, intent)
    old = db.open_approval(con, card_id)
    if old:  # an approval covers the exact wording, so the old one no longer applies
        db.update_card(con, old["id"], state="Withdrawn")
        db.log(con, old["id"], "withdrawn", "Strategy", "Strategy", "Pending approval", "Withdrawn")
    db.update_card(con, card_id, owner_role=to_role, suggested_action=action, written_by=written_by, flag=flag,
                   relevance_score=engine.relevance(c["base"], c["category"], to_role),
                   requires_approval=int(bool(rule)), gate_rule=rule, state="Drafted")
    db.log(con, card_id, "handoff", c["owner_role"], to_role, c["state"], "Drafted")
    if rule:
        _request_approval(con, db.card(con, card_id))
    eid = c["engagement_id"]
    human(con, eid, c["owner_role"], f"Handed {tag(c)} to {to_role}", [tag(c)])
    db.add_trace(con, eid, [
        engine.trace_row("Router", f"Re-scored {tag(c)} for {to_role}: "
                                   f"{engine.relevance(c['base'], c['category'], to_role):.2f}", evidence=[tag(c)]),
        engine.trace_row("Framer", f"Worded it for {to_role}: {action}",
                         f"not verified: {flag}" if flag else "", [tag(c)],
                         kind="llm" if written_by == "llm" else "code"),
        engine.trace_row("Governance", f"Gate: {rule}, sent to Strategy" if rule else "Gate: no approval needed",
                         evidence=[tag(c)])])
    con.commit()


def metrics(con, e: engine.Engagement, sentences=None) -> dict:
    work = db.cards(con, "work", e.id)
    approvals = db.cards(con, "approval", e.id)
    executed = [c for c in work if c["state"] == "Executed"]
    handoffs = [sum(1 for ev in db.events(con, c["id"]) if ev["event"] == "handoff") for c in executed]
    sents = sentences if sentences is not None else e.sentences
    passed = sum(s.status == "passed" for s in sents)
    return {
        "Cards": len(work),
        "In progress": sum(c["state"] in ("Surfaced", "Drafted") for c in work),
        "Not relevant": sum(c["state"] == "Dismissed" for c in work),
        "Awaiting approval": sum(a["state"] == "Pending approval" for a in approvals),
        "Executed": len(executed),
        "Avg hand-offs to execution": round(sum(handoffs) / len(handoffs), 1) if handoffs else 0.0,
        "Citation check": f"{passed} of {len(sents)}",
    }
