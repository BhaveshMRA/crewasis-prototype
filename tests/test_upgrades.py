"""The three brief-driven upgrades: the Innovation team, the per-team learning loop, and the agent trace."""
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


def cards(con, e, role=None):
    return [c for c in db.cards(con, "work", e.id) if role is None or c["owner_role"] == role]


def card(con, e, tag, role=None):
    return next(c for c in cards(con, e, role) if (c["play_id"] or c["fact_id"]) == tag)


# ---------------------------------------------------------------- five teams
def test_five_teams_like_crewasis():
    assert engine.ROLES == ["Marketing", "Insights", "R&D", "Innovation", "Strategy"]
    assert all("Innovation" in w for w in engine.WEIGHTS.values())
    assert all("Innovation" in a for a in engine.FACT_ACTIONS.values())
    assert all("Innovation" in a for a in engine.PLAY_ACTIONS.values())


def test_same_evidence_framed_for_two_teams(con, board):
    f4 = [c for c in cards(con, board) if c["fact_id"] == "F4" and not c["play_id"]]
    assert {c["owner_role"] for c in f4} == {"Insights", "Innovation"}
    assert len({c["suggested_action"] for c in f4}) == 2  # same fact, different framing per team


def test_handoff_to_innovation(con, board):
    c = card(con, board, "F1")
    workflow.handoff(con, c["id"], "Innovation", board)
    assert db.card(con, c["id"])["suggested_action"] == engine.FACT_ACTIONS["F1"]["Innovation"]


# ---------------------------------------------------------------- learning loop
def test_not_relevant_lowers_similar_cards_for_that_team_only(con, board):
    before = {(c["owner_role"], c["play_id"] or c["fact_id"]): workflow.ranking(con, c) for c in cards(con, board)}
    workflow.not_relevant(con, card(con, board, "PL5")["id"])  # a Marketing retention card
    for c in cards(con, board):
        key = (c["owner_role"], c["play_id"] or c["fact_id"])
        if c["owner_role"] == "Marketing" and c["category"] == "retention":
            assert workflow.ranking(con, c) == pytest.approx(before[key] * 0.8)
        else:  # other categories, and every other team's ranking, are untouched
            assert workflow.ranking(con, c) == pytest.approx(before[key])


def test_dismissed_card_and_metrics(con, board):
    c = card(con, board, "PL5")
    workflow.not_relevant(con, c["id"])
    assert db.card(con, c["id"])["state"] == "Dismissed"
    assert workflow.metrics(con, board)["Not relevant"] == 1
    assert [ev["event"] for ev in db.events(con, c["id"])][-1] == "not_relevant"


def test_learning_has_a_floor(con, board):
    for tag in ("PL2", "PL3", "PL5"):
        workflow.not_relevant(con, card(con, board, tag)["id"])
    assert workflow.learned_multiplier(con, "Marketing", "retention") == pytest.approx(0.8 ** 3)
    counts = {("Marketing", "retention"): 50}
    assert workflow.learned_multiplier(con, "Marketing", "retention", counts) == workflow.LEARN_FLOOR


def test_restore_undoes_the_signal(con, board):
    c = card(con, board, "PL5")
    workflow.accept(con, c["id"])
    workflow.not_relevant(con, c["id"])
    workflow.restore(con, c["id"])
    assert db.card(con, c["id"])["state"] == "Drafted"  # back where it was
    assert workflow.learned_multiplier(con, "Marketing", "retention") == 1.0


def test_dismissing_a_gated_card_withdraws_its_approval_and_restore_asks_again(con, board):
    c = card(con, board, "F3")
    pending = db.open_approval(con, c["id"])
    workflow.not_relevant(con, c["id"])
    assert db.card(con, pending["id"])["state"] == "Withdrawn"
    workflow.restore(con, c["id"])
    again = db.open_approval(con, c["id"])
    assert again and again["id"] != pending["id"]


def test_not_relevant_illegal_moves(con, board):
    c = card(con, board, "F1")
    workflow.not_relevant(con, c["id"])
    with pytest.raises(InvalidMove):
        workflow.not_relevant(con, c["id"])
    with pytest.raises(InvalidMove):
        workflow.handoff(con, c["id"], "Insights", board)
    with pytest.raises(InvalidMove):
        workflow.accept(con, c["id"])
    with pytest.raises(InvalidMove):
        workflow.restore(con, card(con, board, "F6")["id"])  # was never dismissed
    done = card(con, board, "PL4")
    workflow.accept(con, done["id"])
    workflow.execute(con, done["id"])
    with pytest.raises(InvalidMove):
        workflow.not_relevant(con, done["id"])


def test_learning_carries_into_the_next_analysis(con, data, board):
    workflow.not_relevant(con, card(con, board, "PL5")["id"])
    nxt = engine.run(con, "Why are customers not reordering our protein bars?", data=data)
    workflow.seed(con, nxt)
    pl2 = card(con, nxt, "PL2")
    assert workflow.ranking(con, pl2) == pytest.approx(pl2["relevance_score"] * 0.8)


# ---------------------------------------------------------------- agent trace
def test_offline_trace_names_every_agent(con, board):
    rows = db.trace(con, board.id)
    agents = [r["agent"] for r in rows]
    assert agents[0] == "Planner" and "Analyst" in agents and "Playbook" in agents
    assert agents[-3:] == ["Router", "Framer", "Governance"]
    assert all(r["kind"] == "code" for r in rows)  # no LLM in this run
    gate = rows[-1]
    assert "4 action(s) to Strategy" in gate["action"] and set(gate["evidence"]) == {"F3", "PL3", "PL8", "PL9"}


def test_llm_trace_marks_llm_agents(con, data, fake_llm):
    llm = fake_llm(plan={"lenses": ["product", "retention"], "products": ["bar"], "questions": ["Why?"]},
                   synthesize=lambda req: {"summary": "Fix the texture first [F1].",
                                           "sentences": [{"key": s["key"], "text": s["text"]}
                                                         for s in req["sentences"]]},
                   frame=lambda req: {"actions": [{"key": c["key"], "action": c["example_action"]}
                                                  for c in req["cards"]]})
    e = engine.run(con, engine.DEMO_PROBLEM, llm, data)
    kinds = {r["agent"]: r["kind"] for r in e.trace}
    assert kinds["Planner"] == "llm" and kinds["Writer"] == "llm" and kinds["Framer"] == "llm"
    assert kinds["Analyst"] == "code" and kinds["Governance"] == "code"


def test_human_moves_and_handoffs_are_traced(con, board):
    c = card(con, board, "F1")
    workflow.handoff(con, c["id"], "Marketing", board)
    workflow.approve(con, db.open_approval(con, c["id"])["id"])
    workflow.not_relevant(con, card(con, board, "PL5")["id"])
    tail = [(r["agent"], r["kind"]) for r in db.trace(con, board.id)][-6:]
    assert tail == [("R&D (human)", "human"), ("Router", "code"), ("Framer", "code"), ("Governance", "code"),
                    ("Strategy (human)", "human"), ("Marketing (human)", "human")]


def test_trace_survives_reload(con, data, board):
    again = engine.load_engagement(con, board.id, data)
    assert [r["action"] for r in again.trace] == [r["action"] for r in board.trace]
