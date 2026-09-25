"""Use cases: the database holds the synthetic data, and the offline engine produces the README's numbers."""
import json

import pytest

import db
import engine


# ---------------------------------------------------------------- database
def test_seeding_loads_every_source(con):
    counts = db.ensure_seeded(con)
    assert counts == {"M": 24, "O": 7373, "P": 8, "R": 150, "S": 146}
    assert [r["prefix"] for r in con.execute("SELECT prefix FROM sources ORDER BY prefix")] == list("MOPRS")


def test_seeding_is_idempotent(con):
    db.ensure_seeded(con)
    db.ensure_seeded(con)
    assert con.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 7701


def test_evidence_keeps_types_and_rows_unchanged(con):
    raw = json.loads(con.execute("SELECT raw FROM evidence WHERE id = 'P01'").fetchone()[0])
    assert raw["price_inr"] == 120 and raw["claims"] == "high protein"
    r = json.loads(con.execute("SELECT raw FROM evidence WHERE id = 'R071'").fetchone()[0])
    assert r["rating"] == 1 and isinstance(r["rating"], int)


def test_database_and_csv_give_identical_analysis(con, data):
    from_db = engine.analyse(engine.load(con), engine.DEMO_PROBLEM, engine.offline_plan(engine.DEMO_PROBLEM))
    from_csv = engine.analyse(data, engine.DEMO_PROBLEM, engine.offline_plan(engine.DEMO_PROBLEM))
    assert [f.sentence for f in from_db.facts.values()] == [f.sentence for f in from_csv.facts.values()]


def test_reset_clears_work_but_keeps_evidence_and_cache(con, data):
    engine.run(con, engine.DEMO_PROBLEM, data=data)
    db.cache_put(con, "k", "{}")
    db.reset(con)
    assert db.engagements(con) == [] and db.cards(con) == []
    assert con.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 7701
    assert db.cache_get(con, "k") == "{}"


# ---------------------------------------------------------------- the demo numbers
@pytest.fixture
def demo(con, data):
    return engine.run(con, engine.DEMO_PROBLEM, data=data)


def test_key_numbers_match_the_readme(demo):
    f = demo.facts
    assert round(f["F1"].magnitude, 2) == 0.34 and f["F1"].computation["share_last_quarter"] == 0.225
    assert round(engine.relevance(engine.base_score(f["F1"]), "product", "R&D"), 3) == 0.601
    assert f["F2"].computation["proforge_price_per_g"] == 6.0 and f["F2"].computation["rival_price_per_g"] == 5.0
    assert round(f["F2"].magnitude, 2) == 0.20
    assert f["F5"].computation["total_drop"] == 0.2 and f["F5"].computation["marketplace_drop"] == 0.28
    assert f["F8"].computation["lapsed_buyers"] == 400
    assert f["F10"].computation["median_days_between_orders"] == 24 and f["F10"].computation["lapsed_on_cycle"] == 232
    assert round(engine.relevance(engine.base_score(f["F10"]), "retention", "Marketing"), 3) == 0.754


def test_every_cited_id_exists(demo):
    known = set(demo.facts) | set(demo.rows)
    for s in demo.sentences:
        if s.key == "summary":
            assert s.status == "missing" and s.text == ""  # only the LLM writes the summary
            continue
        assert s.status == "passed", (s.key, s.problem)
        assert all(i in known for i in engine.CITATION.findall(s.text))
    for f in demo.facts.values():
        assert all(i in demo.rows for i in f.evidence_ids)


def test_playbook_matches_eight_of_nine(demo):
    assert [p.id for p in demo.plays if not p.matched] == ["PL7"]
    assert "6%" in next(p for p in demo.plays if p.id == "PL7").reason


def test_cards_and_gates(demo):
    assert len(demo.cards) == 13
    gated = {c["play_id"] or c["fact_id"]: c["gate_rule"] for c in demo.cards if c["gate_rule"]}
    assert gated == {"F3": "Customer-facing claim", "PL3": "Price change", "PL8": "Customer-facing claim",
                     "PL9": "Customer-facing claim"}
    assert all(not c["gate_rule"] for c in demo.cards if c["owner_role"] == "Strategy")
    f1 = next(c for c in demo.cards if c["fact_id"] == "F1" and not c["play_id"])
    assert "PL1" in f1["note"]  # "fix why they leave" merged into the texture card


def test_no_llm_means_no_summary_and_no_root_causes(demo):
    """Without the LLM nothing narrative is invented: no summary, no root causes."""
    assert demo.root_causes == []
    assert next(s for s in demo.sentences if s.key == "summary").status == "missing"


def test_thin_evidence_is_held_for_insights(con, demo):
    v = next(c for c in demo.cards if c["fact_id"] == "F6")
    assert v["owner_role"] == "Insights" and "only 6 observations" in v["suggested_action"]
    assert [(h["owner_role"], h["play_id"] or h["fact_id"]) for h in v["held"]] == [("R&D", "F6"), ("Strategy", "PL6")]
    assert not any(c["fact_id"] == "F6" and c["owner_role"] == "R&D" for c in demo.cards)


# ---------------------------------------------------------------- edge cases in the engine
@pytest.mark.parametrize("bad", ["", "   ", "hi", "why sales", None])
def test_problem_too_short_is_rejected(bad):
    with pytest.raises(ValueError):
        engine.clean_problem(bad)


def test_long_problem_is_trimmed_and_whitespace_collapsed():
    t = engine.clean_problem("  why   are\n\nsales down " + "word " * 1000)
    assert len(t) <= engine.MAX_PROBLEM_CHARS + 2 and "  " not in t and t.endswith("…")


def test_offline_plan_detects_products():
    assert engine.offline_plan("why is our whey powder not selling")["products"] == ["whey"]
    assert engine.offline_plan("bars and whey both dropped")["products"] == ["bar", "whey"]
    assert engine.offline_plan("sales are down everywhere")["products"] == ["bar"]


def test_whey_question_reports_gaps_instead_of_guessing(con, data):
    e = engine.run(con, "Why is our whey protein powder not selling on marketplaces?", data=data)
    assert set(e.facts) == {"F12", "F13", "F14"}
    assert round(e.facts["F14"].magnitude, 3) == 0.091
    assert any("No whey reviews" in g for g in e.gaps) and any("No whey orders" in g for g in e.gaps)
    assert all(not p.checked for p in e.plays if p.id in ("PL1", "PL2", "PL3", "PL4", "PL5", "PL6", "PL7"))
    assert all(s.status == "passed" for s in e.sentences if s.key != "summary")


@pytest.mark.parametrize("lenses, expected", [
    (["product"], {"F1", "F6"}),
    (["competitor"], {"F2", "F3"}),
    (["customer"], {"F4"}),
    (["channel"], {"F5"}),
    (["retention"], {"F7", "F8", "F9", "F10", "F11"}),
    (["community"], {"F12", "F13"}),
])
def test_each_lens_alone(data, lenses, expected):
    e = engine.analyse(data, "x", {"lenses": lenses, "products": ["bar"]})
    assert set(e.facts) == expected
    engine.initial_cards(e)  # never crashes on a partial set of facts
    assert all(s.status == "passed" for s in e.sentences if s.key != "summary")


def test_empty_plan_gives_empty_but_valid_brief(data):
    e = engine.analyse(data, "x", {"lenses": [], "products": ["bar"]})
    assert e.facts == {} and e.root_causes == [] and engine.initial_cards(e) == []
    assert all(not p.checked for p in e.plays)


def test_zero_data_does_not_crash(data):
    import copy
    empty = copy.copy(data)
    for name in ("reviews", "social", "orders"):
        setattr(empty, name, getattr(data, name).iloc[0:0])
    empty.sales = data.sales.iloc[0:0]
    e = engine.analyse(empty, "x", engine.offline_plan("bars"))
    assert set(e.facts) <= {"F2"}  # only the competitor catalogue is left
    assert e.gaps


def test_gate_matches_whole_words_only():
    assert engine.gate("Draft a compost guide", "Marketing") is None
    assert engine.gate("Draft Instagram posts", "Marketing") == "Customer-facing claim"
    assert engine.gate("Give a 10% discount", "Insights") == "Price change"
    assert engine.gate("Decide whether to fund it", "Strategy") is None


def test_gate_also_checks_the_template_intent():
    assert engine.gate("Share sugar facts with followers.", "Marketing",
                       "Draft Instagram posts that lead with 2 g of sugar.") == "Customer-facing claim"


def test_number_check_normalises_formats(demo):
    f, rows = demo.facts, demo.rows
    assert engine.check_numbers("Texture is 34% [F1]", f, rows)[0]
    assert engine.check_numbers("Texture is 0.34 of reviews [F1]", f, rows)[0]
    assert engine.check_numbers("Marketplace fell from 5,000 units [F5]", f, rows)[0]
    assert engine.check_numbers("₹6.0 per gram [F2]", f, rows)[0]
    assert not engine.check_numbers("Texture is 41% [F1]", f, rows)[0]
    # digits inside ids and dates of cited rows are not "allowed numbers"
    assert not engine.check_numbers("It was 2689 people [R071]", f, rows)[0]


def test_strict_mode_refuses_to_run_without_the_llm(con, data):
    with pytest.raises(engine.LLMUnavailable):
        engine.run(con, engine.DEMO_PROBLEM, None, data, strict=True)


def test_saved_engagement_reloads_identically(con, data, demo):
    again = engine.load_engagement(con, demo.id, data)
    assert [(s.key, s.text, s.written_by) for s in again.sentences] == \
           [(s.key, s.text, s.written_by) for s in demo.sentences]
    assert list(again.facts) == list(demo.facts)
    stored = {r["id"] for r in con.execute("SELECT id FROM facts WHERE engagement_id = ?", (demo.id,))}
    assert stored == set(demo.facts)
    assert con.execute("SELECT COUNT(*) FROM play_matches WHERE engagement_id = ?", (demo.id,)).fetchone()[0] == 9


def test_failed_run_is_marked_failed_and_not_loaded(con, data, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(engine, "analyse", boom)
    with pytest.raises(RuntimeError):
        engine.run(con, engine.DEMO_PROBLEM, data=data)
    row = db.engagements(con)[0]
    assert row["status"] == "failed" and engine.load_engagement(con, row["id"], data) is None


def test_missing_engagement_loads_as_none(con, data):
    assert engine.load_engagement(con, 999, data) is None
