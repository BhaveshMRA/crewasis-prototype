"""Smoke tests for the engine numbers and the card workflow. Run: python test_workflow.py"""
import db
import engine
import workflow

db.DB_PATH = db.DB_PATH.with_name("test.db")


def by_tag(con, tag):
    return next(c for c in db.cards(con, "work") if (c["play_id"] or c["fact_id"]) == tag)


def main():
    e = engine.run()
    f = e.facts
    assert round(f["F1"].magnitude, 2) == 0.34 and round(f["F1"].computation["share_last_quarter"], 3) == 0.225
    assert round(engine.relevance(engine.base_score(f["F1"]), "product", "R&D"), 3) == 0.601
    assert f["F2"].computation["proforge_price_per_g"] == 6.0 and f["F2"].computation["rival_price_per_g"] == 5.0
    assert f["F8"].computation["lapsed_buyers"] == 400
    assert f["F10"].computation["median_days_between_orders"] == 24 and f["F10"].computation["lapsed_on_cycle"] == 232
    assert all(s.status == "passed" for s in e.sentences)
    assert engine.run(simulate_llm_mistake=True).sentences[0].status == "fell_back"
    assert [p.id for p in e.plays if not p.matched] == ["PL7"]

    con = db.connect()
    workflow.seed(con, e)
    assert len(db.cards(con, "work")) == 13
    pending = [a for a in db.cards(con, "approval") if a["state"] == "Pending approval"]
    assert len(pending) == 4, len(pending)

    # gated Marketing card can't execute until Strategy approves
    f3 = by_tag(con, "F3")
    workflow.accept(con, f3["id"])
    assert not workflow.can_execute(con, db.card(con, f3["id"]))
    try:
        workflow.execute(con, f3["id"]); raise AssertionError("should be blocked")
    except PermissionError:
        pass
    workflow.approve(con, db.open_approval(con, f3["id"])["id"])
    assert db.card(con, f3["id"])["state"] == "Executed"

    # reject keeps the card in Drafted; asking again opens a new approval
    pl3 = by_tag(con, "PL3")
    workflow.reject(con, db.open_approval(con, pl3["id"])["id"])
    assert db.card(con, pl3["id"])["state"] == "Drafted" and not workflow.can_execute(con, db.card(con, pl3["id"]))
    workflow.ask_again(con, pl3["id"])
    assert db.open_approval(con, pl3["id"])

    # hand-off: R&D texture card → Marketing gets re-worded as a post, gated, re-scored
    f1 = by_tag(con, "F1")
    workflow.handoff(con, f1["id"], "Marketing", e)
    c = db.card(con, f1["id"])
    assert c["owner_role"] == "Marketing" and c["gate_rule"] == "Customer-facing claim"
    assert round(c["relevance_score"], 3) == round(engine.base_score(f["F1"]) * 0.9, 3)
    first = db.open_approval(con, f1["id"])
    # hand it on to Insights: the approval is withdrawn and the card isn't gated any more
    workflow.handoff(con, f1["id"], "Insights", e)
    assert db.card(con, first["id"])["state"] == "Withdrawn"
    assert not db.card(con, f1["id"])["requires_approval"] and not db.open_approval(con, f1["id"])
    workflow.execute(con, f1["id"])

    # Strategy cards are never gated
    assert all(not c["gate_rule"] for c in db.cards(con, "work") if c["owner_role"] == "Strategy")
    # play card handed off gets the generic play wording
    pl2 = by_tag(con, "PL2")
    workflow.handoff(con, pl2["id"], "Strategy", e)
    assert db.card(con, pl2["id"])["suggested_action"].startswith("Decide whether to go ahead")

    m = workflow.metrics(con, e)
    assert m["Executed"] == 2 and m["Avg hand-offs to execution"] == 1.0, m
    print("all checks passed:", m)
    db.DB_PATH.unlink()


if __name__ == "__main__":
    main()
