"""Real end-to-end test against your actual Ollama model. Prints a report; never prints the API key.

    python check_llm.py            # reads OLLAMA_HOST / OLLAMA_MODEL / OLLAMA_API_KEY from the environment or .env

Steps: connection → one small JSON call → the full pipeline on the demo problem (Plan, Synthesize, Frame)
→ one hand-off re-wording. Uses a throwaway in-memory database, so your crewasis.db is untouched and
nothing is cached from earlier runs. Exit code 0 means every LLM step worked.
"""
import sys
import time

import db
import engine
import llm as llm_mod
import workflow


def line(ok, text):
    print(f"  {'PASS' if ok else 'FAIL'}  {text}")
    return ok


def main() -> int:
    client = llm_mod.OllamaLLM(timeout=None)
    print(f"Ollama host : {client.host}")
    print(f"Model       : {client.model}")
    print(f"API key     : {'set' if client._api_key else 'not set'}")
    print(f"Timeout     : {client.timeout:.0f}s per call\n")
    results = []

    print("1. Connection")
    ok, msg = client.ping()
    results.append(line(ok, msg))
    if not ok:
        print("\nStopping: fix the connection first.")
        return 1

    con = db.connect(":memory:")
    db.ensure_seeded(con)
    client.con = con

    print("\n2. One small JSON call")
    t = time.time()
    out = client.chat_json("ping", "Reply with JSON only.", 'Return {"ok": true}.',
                           {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]})
    results.append(line(isinstance(out, dict) and out.get("ok") is True,
                        f"got {out!r} in {time.time() - t:.1f}s" + (f" ({client.last_error})" if out is None else "")))

    print("\n3. Full pipeline on the demo problem (Plan → Synthesize → Frame)")
    t = time.time()
    e = engine.run(con, engine.DEMO_PROBLEM, client)
    print(f"  took {time.time() - t:.1f}s")
    for c in db.llm_calls(con, e.id):
        results.append(line(bool(c["ok"]), f"{c['step']:<10} {c['latency_ms'] / 1000:5.1f}s {c['error']}"))
    print(f"\n  Plan: planned by {e.plan['planned_by']}; lenses {', '.join(e.plan['lenses'])}; "
          f"products {', '.join(e.plan['products'])}")
    for q in e.plan["questions"]:
        print(f"    - {q}")
    count = {k: sum(s.status == k for s in e.sentences) for k in ("passed", "repaired", "flagged", "missing")}
    llm_written = sum(s.written_by == "llm" for s in e.sentences)
    print(f"\n  Brief: {llm_written} of {len(e.sentences)} sentences written by the LLM · verified first time "
          f"{count['passed']} · fixed on retry {count['repaired']} · not verified {count['flagged']} · "
          f"missing {count['missing']}")
    summary = next((s for s in e.sentences if s.section == "summary"), None)
    if summary:
        print(f"    SUMMARY ({summary.status}, {len(engine.split_sentences(summary.text))} sentences): {summary.text}")
    for s in [s for s in e.sentences if s.section == "findings"][:3]:
        print(f"    LLM  {s.text}")
    for s in e.sentences:
        if s.status in ("repaired", "flagged", "missing"):
            print(f"    {s.status.upper():8} ({s.key}): {s.problem or 'fixed after the LLM was told what was wrong'}")
    acts = [c for c in e.cards if c["written_by"] == "llm"]
    flagged = [c for c in e.cards if c["flag"]]
    print(f"\n  Actions: {len(acts)} of {len(e.cards)} worded by the LLM; {len(flagged)} not verified; "
          f"{sum(bool(c['gate_rule']) for c in e.cards)} need Strategy approval")
    for c in e.cards[:5]:
        print(f"    [{c['owner_role']}] {c['suggested_action']}" + ("  (needs approval)" if c["gate_rule"] else "")
              + (f"  (NOT VERIFIED: {c['flag']})" if c["flag"] else ""))
    results.append(line(llm_written == len(e.sentences), "every brief sentence written by the LLM"))
    results.append(line(summary is not None and summary.written_by == "llm"
                        and len(engine.split_sentences(summary.text)) <= 2, "summary is the LLM's, 2 sentences max"))
    results.append(line(count["flagged"] + count["missing"] == 0, "every LLM sentence verified against the data"))
    results.append(line(len(acts) == len(e.cards) and not flagged, "every action worded by the LLM and verified"))

    print("\n4. Hand-off re-wording (texture card: R&D → Marketing)")
    workflow.seed(con, e)
    card = next(c for c in db.cards(con, "work", e.id) if c["fact_id"] == "F1")
    workflow.handoff(con, card["id"], "Marketing", e, client)
    h = db.card(con, card["id"])
    print(f"    [{h['owner_role']}] {h['suggested_action']}  (worded by {h['written_by']}; "
          f"gate: {h['gate_rule'] or 'none'})")
    results.append(line(h["written_by"] == "llm", "hand-off action worded by the LLM"))
    results.append(line(bool(h["gate_rule"]), "hand-off to Marketing still needs approval"))

    passed = sum(results)
    print(f"\nREAL LLM TEST: {'PASS' if all(results) else 'FAIL'} ({passed}/{len(results)} checks)")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
