"""Card lifecycle: seed, accept, execute, approve, reject, hand off. Every change is logged to card_events."""
import db
import engine


def _request_approval(con, work: dict) -> int:
    rel = engine.relevance(work["base"], work["category"], "Strategy")
    aid = db.add_card(con, fact_id=work["fact_id"], play_id=work.get("play_id"), kind="approval",
                      parent_card_id=work["id"], category=work["category"], owner_role="Strategy",
                      state="Pending approval", base=work["base"], relevance_score=rel,
                      evidence=f"{work['owner_role']} wants to: {work['suggested_action']}",
                      suggested_action=f"Approve or reject ({work['gate_rule']})", requires_approval=False,
                      gate_rule=work["gate_rule"])
    db.log(con, aid, "approval_requested", from_role=work["owner_role"], to_role="Strategy",
           to_state="Pending approval")
    return aid


def seed(con, e: engine.Engagement):
    db.reset(con)
    for c in e.cards:
        cid = db.add_card(con, kind="work", state="Surfaced", requires_approval=bool(c["gate_rule"]), **c)
        if c["gate_rule"]:
            _request_approval(con, db.card(con, cid))
    con.commit()


def accept(con, card_id):
    c = db.card(con, card_id)
    db.update_card(con, card_id, state="Drafted")
    db.log(con, card_id, "drafted", c["owner_role"], c["owner_role"], c["state"], "Drafted")
    con.commit()


def can_execute(con, c: dict) -> bool:
    if not c["requires_approval"]:
        return True
    a = db.latest_approval(con, c["id"])
    return bool(a and a["state"] == "Approved")


def execute(con, card_id):
    c = db.card(con, card_id)
    if not can_execute(con, c):
        raise PermissionError("This card needs Strategy's approval first.")
    db.update_card(con, card_id, state="Executed")
    db.log(con, card_id, "executed", c["owner_role"], c["owner_role"], c["state"], "Executed")
    con.commit()


def approve(con, approval_id):
    a = db.card(con, approval_id)
    db.update_card(con, approval_id, state="Approved")
    db.log(con, approval_id, "approved", "Strategy", "Strategy", a["state"], "Approved")
    w = db.card(con, a["parent_card_id"])
    db.update_card(con, w["id"], state="Executed")
    db.log(con, w["id"], "executed", w["owner_role"], w["owner_role"], w["state"], "Executed")
    con.commit()


def reject(con, approval_id):
    a = db.card(con, approval_id)
    db.update_card(con, approval_id, state="Rejected")
    db.log(con, approval_id, "rejected", "Strategy", "Strategy", a["state"], "Rejected")
    w = db.card(con, a["parent_card_id"])
    db.update_card(con, w["id"], state="Drafted")
    db.log(con, w["id"], "rejected", "Strategy", w["owner_role"], w["state"], "Drafted")
    con.commit()


def ask_again(con, card_id):
    c = db.card(con, card_id)
    if c["requires_approval"] and not db.open_approval(con, card_id):
        _request_approval(con, c)
        con.commit()


def handoff(con, card_id, to_role, e: engine.Engagement):
    """Re-score (RoleWeight), re-word (Frame) and re-check (Gate) the card for its new team."""
    c = db.card(con, card_id)
    action = engine.action_for(c, to_role, e)
    rule = engine.gate(action, to_role)
    old = db.open_approval(con, card_id)
    if old:  # an approval covers the exact wording, so the old one no longer applies
        db.update_card(con, old["id"], state="Withdrawn")
        db.log(con, old["id"], "withdrawn", "Strategy", "Strategy", "Pending approval", "Withdrawn")
    db.update_card(con, card_id, owner_role=to_role, suggested_action=action,
                   relevance_score=engine.relevance(c["base"], c["category"], to_role),
                   requires_approval=int(bool(rule)), gate_rule=rule, state="Drafted")
    db.log(con, card_id, "handoff", c["owner_role"], to_role, c["state"], "Drafted")
    if rule:
        _request_approval(con, db.card(con, card_id))
    con.commit()


def metrics(con, e: engine.Engagement) -> dict:
    work = db.cards(con, "work")
    approvals = db.cards(con, "approval")
    executed = [c for c in work if c["state"] == "Executed"]
    handoffs = [sum(1 for ev in db.events(con, c["id"]) if ev["event"] == "handoff") for c in executed]
    passed = sum(s.status == "passed" for s in e.sentences)
    return {
        "Cards": len(work),
        "In progress": sum(c["state"] in ("Surfaced", "Drafted") for c in work),
        "Awaiting approval": sum(a["state"] == "Pending approval" for a in approvals),
        "Executed": len(executed),
        "Avg hand-offs to execution": round(sum(handoffs) / len(handoffs), 1) if handoffs else 0.0,
        "Citation check": f"{passed} of {len(e.sentences)}",
    }
