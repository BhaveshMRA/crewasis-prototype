"""Card lifecycle: every legal move works, every illegal move is refused, and every change is logged."""
import pytest

import db
import engine
import workflow
from workflow import InvalidMove


@pytest.fixture
def board(con, data):
    e = engine.run(con, engine.DEMO_PROBLEM, data=data)
    workflow.seed(con, e)
    return e


def card(con, e, tag):
    return next(c for c in db.cards(con, "work", e.id) if (c["play_id"] or c["fact_id"]) == tag)


def events(con, cid):
    return [ev["event"] for ev in db.events(con, cid)]


# ---------------------------------------------------------------- happy paths
def test_seed_creates_cards_and_approvals(con, board):
    assert len(db.cards(con, "work", board.id)) == 13
    assert len([a for a in db.cards(con, "approval", board.id) if a["state"] == "Pending approval"]) == 4


def test_seed_is_idempotent(con, board):
    workflow.seed(con, board)
    assert len(db.cards(con, "work", board.id)) == 13


def test_ungated_card_accept_then_execute(con, board):
    c = card(con, board, "F1")
    workflow.accept(con, c["id"])
    workflow.execute(con, c["id"])
    assert db.card(con, c["id"])["state"] == "Executed"
    assert events(con, c["id"]) == ["created", "drafted", "executed"]


def test_gated_card_is_blocked_until_approved(con, board):
    c = card(con, board, "F3")
    workflow.accept(con, c["id"])
    with pytest.raises(PermissionError):
        workflow.execute(con, c["id"])
    workflow.approve(con, db.open_approval(con, c["id"])["id"])
    assert db.card(con, c["id"])["state"] == "Executed"


def test_reject_then_ask_again_then_approve(con, board):
    c = card(con, board, "PL3")
    workflow.reject(con, db.open_approval(con, c["id"])["id"])
    assert db.card(con, c["id"])["state"] == "Drafted" and not workflow.can_execute(con, db.card(con, c["id"]))
    workflow.ask_again(con, c["id"])
    workflow.approve(con, db.open_approval(con, c["id"])["id"])
    assert db.card(con, c["id"])["state"] == "Executed"


def test_strategy_decides_without_approval(con, board):
    c = card(con, board, "PL4")
    assert not c["requires_approval"]
    workflow.accept(con, c["id"])
    workflow.execute(con, c["id"])
    assert db.card(con, c["id"])["state"] == "Executed"


def test_handoff_rescores_rewords_and_regates(con, board):
    c = card(con, board, "F1")
    workflow.handoff(con, c["id"], "Marketing", board)
    h = db.card(con, c["id"])
    assert h["owner_role"] == "Marketing" and h["state"] == "Drafted"
    assert h["suggested_action"] == engine.FACT_ACTIONS["F1"]["Marketing"]
    assert round(h["relevance_score"], 3) == round(engine.base_score(board.facts["F1"]) * 0.9, 3)
    assert h["gate_rule"] == "Customer-facing claim" and db.open_approval(con, c["id"])


def test_handoff_withdraws_a_stale_approval(con, board):
    c = card(con, board, "F1")
    workflow.handoff(con, c["id"], "Marketing", board)
    first = db.open_approval(con, c["id"])
    workflow.handoff(con, c["id"], "Insights", board)
    assert db.card(con, first["id"])["state"] == "Withdrawn"
    assert not db.card(con, c["id"])["requires_approval"] and db.open_approval(con, c["id"]) is None
    workflow.execute(con, c["id"])
    assert events(con, c["id"]).count("handoff") == 2


def test_play_card_handoff_uses_play_wording(con, board):
    c = card(con, board, "PL2")
    workflow.handoff(con, c["id"], "Strategy", board)
    assert db.card(con, c["id"])["suggested_action"] == "Decide whether to go ahead with “Reorder reminder”."
    workflow.handoff(con, c["id"], "Marketing", board)  # back to the owner: the original action returns
    assert db.card(con, c["id"])["suggested_action"].startswith("Send a reorder reminder on day 22")


def test_community_card_handoff(con, board):
    c = card(con, board, "PL8")
    workflow.handoff(con, c["id"], "Insights", board)
    h = db.card(con, c["id"])
    assert "r/IndianFitness" in h["suggested_action"] and not h["requires_approval"]


def test_metrics(con, board):
    workflow.accept(con, card(con, board, "F1")["id"])
    workflow.execute(con, card(con, board, "F1")["id"])
    c = card(con, board, "F5")
    workflow.handoff(con, c["id"], "Insights", board)
    workflow.execute(con, c["id"])
    m = workflow.metrics(con, board)
    assert m["Executed"] == 2 and m["Avg hand-offs to execution"] == 0.5 and m["Cards"] == 13


# ---------------------------------------------------------------- illegal moves
def test_accept_twice_is_refused(con, board):
    c = card(con, board, "F1")
    workflow.accept(con, c["id"])
    with pytest.raises(InvalidMove):
        workflow.accept(con, c["id"])


def test_execute_before_accept_is_refused(con, board):
    with pytest.raises(InvalidMove):
        workflow.execute(con, card(con, board, "F1")["id"])


def test_double_approve_is_refused(con, board):
    a = db.open_approval(con, card(con, board, "F3")["id"])
    workflow.approve(con, a["id"])
    with pytest.raises(InvalidMove):
        workflow.approve(con, a["id"])
    with pytest.raises(InvalidMove):
        workflow.reject(con, a["id"])


def test_approving_a_withdrawn_approval_is_refused(con, board):
    c = card(con, board, "F3")
    a = db.open_approval(con, c["id"])
    workflow.handoff(con, c["id"], "Insights", board)
    with pytest.raises(InvalidMove):
        workflow.approve(con, a["id"])


def test_ask_again_while_pending_is_refused(con, board):
    with pytest.raises(InvalidMove):
        workflow.ask_again(con, card(con, board, "F3")["id"])
    with pytest.raises(InvalidMove):
        workflow.ask_again(con, card(con, board, "F1")["id"])  # never needed approval


@pytest.mark.parametrize("to_role", ["R&D", "Legal", ""])
def test_bad_handoff_targets(con, board, to_role):
    with pytest.raises(InvalidMove):
        workflow.handoff(con, card(con, board, "F1")["id"], to_role, board)


def test_executed_card_cannot_be_handed_off(con, board):
    c = card(con, board, "F1")
    workflow.accept(con, c["id"])
    workflow.execute(con, c["id"])
    with pytest.raises(InvalidMove):
        workflow.handoff(con, c["id"], "Insights", board)


def test_wrong_card_kinds_and_missing_cards(con, board):
    a = db.open_approval(con, card(con, board, "F3")["id"])
    with pytest.raises(InvalidMove):
        workflow.accept(con, a["id"])
    with pytest.raises(InvalidMove):
        workflow.approve(con, card(con, board, "F1")["id"])
    with pytest.raises(InvalidMove):
        workflow.execute(con, 99999)


# ---------------------------------------------------------------- several analyses
def test_engagements_keep_separate_boards(con, data, board):
    other = engine.run(con, "Why is our whey protein powder not selling?", data=data)
    workflow.seed(con, other)
    assert len(db.cards(con, "work", board.id)) == 13 and len(db.cards(con, "work", other.id)) == 3
    workflow.approve(con, db.open_approval(con, card(con, board, "F3")["id"])["id"])
    assert workflow.metrics(con, other)["Executed"] == 0
