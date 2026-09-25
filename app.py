"""CREWASIS · Winston: from answer to action — Streamlit demo.

Run:  streamlit run app.py
LLM:  set OLLAMA_HOST / OLLAMA_MODEL (and OLLAMA_API_KEY for Ollama Cloud) before starting. See README.
"""
import html
import os
import re

import pandas as pd
import streamlit as st

import db
import engine
import llm as llm_mod
import workflow
from engine import pct

st.set_page_config(page_title="CREWASIS · Winston", page_icon="🧭", layout="wide")

LENS_TITLES = {"product": "Where the product is failing", "competitor": "Competitors", "customer": "Customer discovery",
               "channel": "Channels", "retention": "Retention", "community": "Where buyers talk"}
TEAM_CLASS = {"Marketing": "mkt", "Insights": "ins", "R&D": "rnd", "Innovation": "inn", "Strategy": "str"}
STATE_CLASS = {"Surfaced": "surfaced", "Drafted": "drafted", "Executed": "done", "Pending approval": "pending",
               "Approved": "done", "Rejected": "rejected", "Withdrawn": "withdrawn", "Dismissed": "withdrawn"}
KIND_CLASS = {"llm": "llm", "code": "code", "human": "human"}

st.markdown("""
<style>
.pill{display:inline-block;padding:2px 10px;border-radius:999px;font-size:.76rem;font-weight:600;margin:0 6px 4px 0;
      line-height:1.5;white-space:nowrap}
.team{color:#fff}
.team.mkt{background:#7D66EC}.team.ins{background:#0284C7}.team.rnd{background:#059669}
.team.str{background:#F59E0B;color:#1A1825}.team.inn{background:#DB2777}
.agent{display:inline-block;padding:2px 10px;border-radius:999px;font-size:.76rem;font-weight:700;margin-right:6px}
.agent.llm{background:#7D66EC;color:#fff}.agent.code{background:#334155;color:#fff}
.agent.human{background:#F59E0B;color:#1A1825}
.tstep{border-left:2px solid rgba(128,128,128,.3);padding:2px 0 10px 14px;margin-left:6px}
.roster{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin:6px 0 16px}
.roster div{border:1px solid rgba(128,128,128,.25);border-radius:10px;padding:8px 12px;font-size:.85rem}
.learned{font-size:.78rem;color:#B45309;margin-top:4px}
.state{border:1.5px solid currentColor;background:transparent}
.state.surfaced{color:#0284C7}.state.drafted{color:#7D66EC}.state.done{color:#059669}
.state.pending{color:#D97706}.state.rejected{color:#DC2626}.state.withdrawn{color:#9CA3AF}
.tag{background:rgba(128,128,128,.14)}
.by{font-size:.66rem;padding:1px 7px;margin-left:4px;vertical-align:middle}
.by.llm{background:rgba(125,102,236,.14);color:#7D66EC}.by.tpl{background:rgba(128,128,128,.14)}
.by.fix{background:rgba(2,132,199,.12);color:#0369A1}.by.bad{background:rgba(220,38,38,.12);color:#DC2626}
.score{float:right;font-weight:700;font-size:.95rem}
.score small{font-weight:400;opacity:.6}
.action{font-size:1.05rem;font-weight:650;margin:6px 0 4px;line-height:1.35}
.why{font-size:.86rem;opacity:.78;line-height:1.4}
.note{font-size:.78rem;opacity:.65;margin-top:4px}
.banner{border-radius:8px;padding:6px 10px;font-size:.82rem;margin:8px 0 2px}
.banner.warn{background:rgba(245,158,11,.14);border-left:3px solid #F59E0B}
.banner.ok{background:rgba(5,150,105,.12);border-left:3px solid #059669}
.banner.bad{background:rgba(220,38,38,.10);border-left:3px solid #DC2626}
.chip{font-family:ui-monospace,Menlo,monospace;font-size:.72rem;padding:1px 6px;border-radius:6px;margin-left:2px;
      white-space:nowrap}
.chip.fact{background:rgba(125,102,236,.16);color:#7D66EC;font-weight:600}
.chip.row{background:rgba(128,128,128,.15)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin:8px 0 18px}
.tile{border:1px solid rgba(128,128,128,.25);border-radius:12px;padding:12px 16px}
.tile.accent{border-color:#7D66EC;background:rgba(125,102,236,.07)}
.tile .v{font-size:1.65rem;font-weight:700;line-height:1.2}
.tile .l{font-size:.8rem;opacity:.7;margin-top:2px}
.lead{font-size:.95rem;opacity:.75;margin:-6px 0 14px}
.plan{border:1px solid rgba(128,128,128,.25);border-radius:10px;padding:10px 12px;margin-bottom:10px}
.plan b{display:block;margin-bottom:4px}
.plan .m{font-size:.76rem;opacity:.65;margin-top:6px}
.plan.off{opacity:.55;border-style:dashed}
.start{border-left:3px solid #7D66EC;padding:6px 12px;margin:6px 0;border-radius:4px;
       background:rgba(125,102,236,.05)}
.problem{border-left:3px solid rgba(128,128,128,.4);padding:4px 12px;margin:0 0 12px;opacity:.85}
.summary{border:1px solid #7D66EC;border-radius:12px;padding:12px 16px;margin:4px 0 16px;
         background:rgba(125,102,236,.05);line-height:1.5}
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------- resources
@st.cache_resource
def connection():
    con = db.connect()
    db.ensure_seeded(con)
    return con


@st.cache_resource
def dataset():
    return engine.load(connection())


@st.cache_resource(max_entries=20)
def engagement(eid: int):
    return engine.load_engagement(connection(), eid, dataset())


con = connection()
data = dataset()
e_rows = data.rows


# ---------------------------------------------------------------- helpers
def chips(text: str) -> str:
    """Escape text and turn [F1] / [R071] citations into chips."""
    def chip(m):
        i = m.group(1)
        return f'<span class="chip {"fact" if i.startswith("F") else "row"}">{i}</span>'
    return re.sub(r"\[([A-Z]+\d+)\]", chip, html.escape(str(text)))


def team_pill(role: str) -> str:
    return f'<span class="pill team {TEAM_CLASS[role]}">{html.escape(role)}</span>'


def state_pill(state: str) -> str:
    return f'<span class="pill state {STATE_CLASS[state]}">{state}</span>'


def by_pill(written_by: str, status: str = "passed", flag: str = "") -> str:
    if written_by != "llm":
        return '<span class="pill by tpl">offline</span>'
    if status == "flagged" or flag:
        return '<span class="pill by bad">LLM · not verified</span>'
    if status == "repaired":
        return '<span class="pill by fix">LLM · fixed on retry</span>'
    return '<span class="pill by llm">LLM · checked</span>'


def label_of(s) -> str:
    return "summary" if s.section == "summary" else (s.key.split(":")[-1] if s.key else (s.fact_id or s.section))


def tiles(items, accent_first=False) -> str:
    out = []
    for i, (value, label) in enumerate(items):
        cls = "tile accent" if accent_first and i == 0 else "tile"
        out.append(f'<div class="{cls}"><div class="v">{html.escape(str(value))}</div>'
                   f'<div class="l">{html.escape(label)}</div></div>')
    return '<div class="tiles">' + "".join(out) + "</div>"


def rows_table(ids, limit=8) -> pd.DataFrame:
    rows = [e_rows[i] for i in ids[:limit] if i in e_rows]
    return pd.DataFrame(rows).astype(str) if rows else pd.DataFrame()


def show_fact(e: engine.Engagement, fid: str):
    f = e.facts[fid]
    st.markdown(f"**{fid} · {f.title}** · lens: {f.lens} · source confidence {f.confidence:.2f}")
    st.markdown(f'<div class="why">{chips(f.sentence)}</div>', unsafe_allow_html=True)
    st.json(f.computation, expanded=False)
    by_source = {}
    for i in f.evidence_ids:
        by_source.setdefault(i[0], []).append(i)
    names = e.sources.set_index("prefix")["name"]
    for prefix, ids in by_source.items():
        st.caption(f"{names[prefix]}: {len(ids)} rows used" +
                   (f", first {min(8, len(ids))} shown" if len(ids) > 8 else ""))
        st.dataframe(rows_table(ids), hide_index=True, width="stretch")


def act(fn, *args):
    """Run a workflow action; show a friendly error instead of a traceback if the move isn't allowed."""
    try:
        fn(*args)
    except (workflow.InvalidMove, PermissionError) as ex:
        st.session_state["flash"] = str(ex)
    st.rerun()


# ---------------------------------------------------------------- sidebar
# A new or reset analysis is queued here and applied before the "Analysis" dropdown is drawn, because
# Streamlit doesn't allow changing a widget's value after the widget exists.
if "pending_eid" in st.session_state:
    st.session_state["eid"] = st.session_state.pop("pending_eid")

with st.sidebar:
    st.markdown("## CREWASIS\n**Winston** · from answer to action")
    past = [x for x in db.engagements(con) if x["status"] == "done"]
    if past:
        labels = {x["id"]: f"#{x['id']} · {x['problem'][:42]}{'…' if len(x['problem']) > 42 else ''}" for x in past}
        ids = [x["id"] for x in past]
        cur = st.session_state.get("eid")
        if cur not in ids:
            st.session_state["eid"] = ids[0]
        st.selectbox("Analysis", ids, key="eid", format_func=labels.get)
    role = st.selectbox("View the Team board as", engine.ROLES, key="role")
    st.divider()
    st.markdown("**LLM (Ollama)**")
    offline_env = os.environ.get("CREWASIS_OFFLINE") == "1"
    use_llm = st.toggle("Use the LLM", value=not offline_env, key="use_llm",
                        help="Off = templates only. The app works the same either way; the LLM only rewords.")
    host = st.text_input("Host", os.environ.get("OLLAMA_HOST", llm_mod.DEFAULT_HOST), key="host",
                         disabled=not use_llm)
    model = st.text_input("Model", os.environ.get("OLLAMA_MODEL", llm_mod.DEFAULT_MODEL), key="model",
                          disabled=not use_llm)
    st.caption("API key: " + ("set from OLLAMA_API_KEY" if os.environ.get("OLLAMA_API_KEY") else
                              "not set (fine for a local Ollama)"))
    client = llm_mod.OllamaLLM(con, host=host, model=model) if use_llm else None
    if use_llm and st.button("Test connection"):
        ok, msg = client.ping()
        (st.success if ok else st.error)(msg)
    st.divider()
    simulate = st.checkbox("Simulate an LLM mistake", help="Pretends the LLM changed a number in the first finding, "
                           "so you can watch the Check step catch it.")
    if st.button("Reset demo"):
        db.reset(con)
        engagement.clear()
        st.session_state["pending_eid"] = None
        st.rerun()
    st.caption("All data is synthetic. ProForge, CleanBar Co, MuscleMint and WheyWise are fictional brands.")

eid = st.session_state.get("eid")
e = engagement(eid) if eid else None
if e is not None and not db.cards(con, engagement_id=e.id):
    workflow.seed(con, e)
sentences = engine.with_simulated_mistake(e) if (e is not None and simulate) else (e.sentences if e else [])

if "flash" in st.session_state:
    st.warning(st.session_state.pop("flash"))

tab_ask, tab_brief, tab_board, tab_trace = st.tabs(["① Ask Winston", "② Brief", "③ Team board", "④ Agent trace"])

# ------------------------------------------------------------ ① Ask Winston
with tab_ask:
    st.header("Ask Winston")
    st.markdown('<div class="lead">Winston today answers questions. Here it takes the next step: its agents plan '
                'the analysis, read reviews, social posts, competitor data, sales and orders, check every claim '
                'against the data, and hand each team its next action, with a person approving anything risky.</div>',
                unsafe_allow_html=True)
    c1, c2 = st.columns([1, 2], gap="large")
    with c1:
        st.subheader("Brand profile")
        for k, v in engine.BRAND_PROFILE.items():
            st.markdown(f"**{k.replace('_', ' ').capitalize()}:** {v}")
    with c2:
        st.subheader("Your problem")
        text = st.text_area("Describe the business problem in plain words", engine.DEMO_PROBLEM, height=110,
                            max_chars=engine.MAX_PROBLEM_CHARS)
        st.caption("Try also: “Why is our whey not selling?” or “Are we losing customers to competitors?”")
        if st.button("Ask Winston", type="primary"):
            try:
                engine.clean_problem(text)
            except ValueError as ex:
                st.error(str(ex))
            else:
                with st.spinner("Planning, analysing and writing the brief" +
                                (f" with {client.model} (this can take a minute)…" if client else "…")):
                    new = engine.run(con, text, client, data)
                    workflow.seed(con, new)
                st.session_state["pending_eid"] = new.id
                st.rerun()
    if e is not None:
        st.success(f"Showing analysis #{e.id}. Open **② Brief** for the findings and **③ Team board** for the "
                   f"actions.")
        st.markdown("**Plan:** " + " ".join(f'<span class="pill tag">✓ {LENS_TITLES[l]}</span>'
                                             for l in e.plan["lenses"])
                    + f" · products: {', '.join(e.plan['products'])} · planned by {e.plan.get('planned_by', '—')}",
                    unsafe_allow_html=True)
        st.caption("Questions: " + " · ".join(e.plan["questions"]))

# ------------------------------------------------------------------ ② Brief
with tab_brief:
    if e is None:
        st.info("Ask Winston first (tab ①).")
    else:
        f = e.facts
        verified = sum(s.status in ("passed", "repaired") for s in sentences)
        by_llm = sum(s.written_by == "llm" and s.status in ("passed", "repaired") for s in sentences)
        st.header(f"Brief · analysis #{e.id}")
        st.markdown('<div class="lead">What is going wrong, why, and what to do. Every number is calculated from '
                    'source rows; purple chips are facts, grey chips are the rows behind them.</div>',
                    unsafe_allow_html=True)
        st.markdown(f'<div class="problem"><b>Problem:</b> {html.escape(e.problem)}</div>', unsafe_allow_html=True)
        calls = db.llm_calls(con, e.id)
        failed = [c for c in calls if not c["ok"]]
        if e.llm_mode == "offline":
            st.caption("Offline: the LLM is switched off, so the text is built directly from the facts.")
        elif failed and not any(s.written_by == "llm" for s in sentences):
            st.warning(f"The LLM ({e.llm_mode}) couldn't be reached (“{failed[-1]['error']}”), so this brief is built "
                       f"directly from the facts. Check the connection and run it again.")
        elif failed:
            st.warning(f"The LLM ({e.llm_mode}) had {len(failed)} failed call(s), latest: “{failed[-1]['error']}”.")
        tile_items = []
        if "F5" in f:
            tile_items.append((f"−{pct(f['F5'].computation['total_drop'])}", "sales this quarter"))
        if "F7" in f:
            tile_items.append((f"{f['F7'].computation['repeat_rate_last_quarter']:.0%} → "
                               f"{f['F7'].computation['repeat_rate_this_quarter']:.0%}", "repeat purchase"))
        if "F8" in f:
            tile_items.append((f"{f['F8'].computation['lapsed_buyers']}", "regular buyers who stopped"))
        if "F1" in f:
            tile_items.append((pct(f["F1"].magnitude), "bad reviews about texture"))
        if "F14" in f and f["F14"].magnitude > 0:
            tile_items.append((f"+{pct(f['F14'].magnitude)}", "whey price per gram vs cheapest rival"))
        tile_items.append((f"{verified}/{len(sentences)}", f"sentences verified against the data ({by_llm} "
                                                            f"written by the LLM)"))
        st.markdown(tiles(tile_items, accent_first=True), unsafe_allow_html=True)
        for s in sentences:
            if s.status == "flagged":
                st.error(f"**Not verified** ({label_of(s)}): {s.problem}. It's shown as the LLM wrote it; check the "
                         f"rows behind it before relying on it.")
            elif s.status == "missing":
                st.warning(f"The LLM didn't write the {label_of(s)} sentence, even after being asked again.")

        summary = next((s for s in sentences if s.section == "summary"), None)
        if summary and summary.status != "missing":
            st.markdown(f'<div class="summary"><b>Summary</b> {by_pill(summary.written_by, summary.status)}<br>'
                        f'{chips(summary.text)}</div>', unsafe_allow_html=True)

        work = db.cards(con, "work", e.id)
        if work:
            st.subheader("Start here")
            for c in sorted(work, key=lambda c: -c["base"])[:3]:
                st.markdown(f'<div class="start">{team_pill(c["owner_role"])}<b>{html.escape(c["suggested_action"])}'
                            f'</b>{by_pill(c["written_by"], flag=c["flag"])}<div class="why">'
                            f'{chips(engine.card_why(c, e))}</div></div>',
                            unsafe_allow_html=True)

        if e.root_causes:
            st.subheader("Likely root causes")
            for i, r in enumerate(e.root_causes, 1):
                conf = {"High": "done", "Medium": "pending", "Low": "withdrawn"}[r["confidence"]]
                fact_chips = chips(" ".join(f"[{x}]" for x in r["facts"]))
                st.markdown(f'{i}. **{html.escape(r["cause"])}** <span class="pill state {conf}">{r["confidence"]}'
                            f'</span> <span class="why">{fact_chips} · {r["rows"]:,} rows from {len(r["sources"])} '
                            f'source types</span>', unsafe_allow_html=True)
            st.caption("Confidence is set by rule: High = 2+ source types with 30+ rows · Medium = 1 type with 30+ "
                       "rows, or 2 types with fewer · Low = otherwise.")

        st.subheader("Findings")
        if not f:
            st.info("No findings for this plan. See “What we couldn't check” below.")
        for lens in engine.LENSES:
            fs = [x for x in f.values() if x.lens == lens]
            if not fs:
                continue
            st.markdown(f"##### {LENS_TITLES[lens]}")
            for x in fs:
                s = next((y for y in sentences if y.fact_id == x.id and y.section == "findings"), None)
                if s is None:
                    continue
                if s.status == "missing":
                    st.markdown(f"- *The LLM didn't write this finding; see the rows behind {x.id}.*")
                else:
                    st.markdown(f"- {chips(s.text)}{by_pill(s.written_by, s.status)}", unsafe_allow_html=True)
                with st.expander(f"Rows behind {x.id}"):
                    show_fact(e, x.id)

        ret = [p for p in e.plays if p.section == "retention"]
        def play_text(p):
            s = next((s for s in sentences if s.key == f"play:{p.id}"), None)
            if s is None:
                return p.plan_line, "template", "passed"
            if s.status == "missing":
                return "The LLM didn't write this line.", "llm", "flagged"
            return s.text, s.written_by, s.status

        if any(p.checked for p in ret):
            st.subheader("Retention plan")
            st.caption("Tips come from a fixed playbook. A play shows up only when ProForge's own numbers match its "
                       "rule.")
            cols = st.columns(3)
            for col, effort in zip(cols, ["quick", "medium", "long"]):
                with col:
                    st.markdown(f"**{effort.capitalize()} {'wins' if effort == 'quick' else 'fixes'}**")
                    for p in [p for p in ret if p.matched and p.effort == effort]:
                        owner = "R&D" if p.merge_into else p.owner
                        extra = f" · Do after: {p.do_after}" if p.do_after else ""
                        txt, wb, stt = play_text(p)
                        st.markdown(f'<div class="plan"><b>{html.escape(p.name)}</b>{team_pill(owner)}{by_pill(wb, stt)}'
                                    f'<div class="why">{chips(txt)}</div>'
                                    f'<div class="m">Metric to watch: {html.escape(p.metric)}{html.escape(extra)}'
                                    f'</div></div>', unsafe_allow_html=True)
            for p in [p for p in ret if not p.matched]:
                st.markdown(f'<div class="plan off"><b>{"Not checked" if not p.checked else "Not matched"} · '
                            f'{html.escape(p.name)}</b><div class="why">{html.escape(p.reason[0].upper() + p.reason[1:])}'
                            f' (rule: {html.escape(p.rule)})</div></div>', unsafe_allow_html=True)

        if not e.community_table.empty:
            st.subheader("Where to show up")
            for p in [p for p in e.plays if p.section == "community" and p.matched]:
                txt, wb, stt = play_text(p)
                st.markdown(f'<div class="plan"><b>{html.escape(p.name)}</b>{team_pill(p.owner)}'
                            f'<span class="pill state pending">needs Strategy approval</span>{by_pill(wb, stt)}'
                            f'<div class="why">{chips(txt)}</div></div>', unsafe_allow_html=True)
            st.dataframe(e.community_table.rename(columns={
                "community": "Community", "posts": "Posts this quarter", "competitor_mentions": "Competitor mentions",
                "unanswered": "Unanswered questions", "share_of_conversation": "Share of conversation",
                "proforge_mentions": "ProForge mentions"}), hide_index=True, width="stretch")
            st.caption("Reply openly as the brand and follow each community's self-promotion rules: no fake "
                       "accounts, no posing as customers.")

        st.subheader(f"All recommendations → {len(work)} cards on the Team board")
        if work:
            st.dataframe(pd.DataFrame([{"Owner": c["owner_role"], "Action": c["suggested_action"],
                                        "Worded by": ("LLM" + (" (not verified)" if c["flag"] else "")) if c["written_by"] == "llm" else "offline", "From": c["play_id"] or c["fact_id"],
                                        "Needs approval": c["gate_rule"] or ""} for c in
                                       sorted(work, key=lambda c: -c["relevance_score"])]),
                         hide_index=True, width="stretch")

        st.subheader("What we couldn't check")
        for g in e.gaps:
            st.markdown(f"- {g}")

        with st.expander("Sources used"):
            st.dataframe(e.sources[["prefix", "name", "file", "description", "collected_at", "synthetic",
                                    "confidence", "rows_loaded", "rows_cited"]], hide_index=True, width="stretch")
        with st.expander(f"What the LLM did ({len(calls)} calls)"):
            st.caption("The LLM plans and writes the words. Every number above is calculated by code; every LLM "
                       "sentence is checked against the data, sent back with the reason if it fails, and flagged if "
                       "it still can't be verified.")
            if calls:
                st.dataframe(pd.DataFrame(calls)[["step", "model", "ok", "cached", "latency_ms", "error", "at"]],
                             hide_index=True, width="stretch")
            else:
                st.write("No LLM calls for this analysis.")


# ------------------------------------------------------------- ③ Team board
def render_card(c: dict):
    with st.container(border=True):
        tag = c["play_id"] or c["fact_id"]
        rank = workflow.ranking(con, c, counts)
        head = (f'{team_pill(c["owner_role"])}{state_pill(c["state"])}<span class="pill tag">{tag}</span>'
                f'<span class="score">{rank:.2f} <small>relevance</small></span>')
        if c["kind"] == "approval":
            st.markdown(f'{head}<div class="action">Approve this?</div>'
                        f'<div class="why">{html.escape(c["evidence"])}</div>'
                        f'<div class="banner warn">Why it needs approval: {html.escape(c["gate_rule"])}</div>',
                        unsafe_allow_html=True)
            if c["state"] == "Pending approval":
                b1, b2 = st.columns(2)
                if b1.button("Approve", key=f"ap{c['id']}", type="primary", width="stretch"):
                    act(workflow.approve, con, c["id"])
                if b2.button("Reject", key=f"rj{c['id']}", width="stretch"):
                    act(workflow.reject, con, c["id"])
            return

        banner = ""
        if c["requires_approval"]:
            a = db.latest_approval(con, c["id"])
            state = a["state"] if a else ""
            kind, msg = {"Pending approval": ("warn", "Needs Strategy approval"),
                         "Approved": ("ok", "Approved by Strategy"),
                         "Rejected": ("bad", "Rejected by Strategy: revise or hand off")}.get(state, ("warn", ""))
            banner = f'<div class="banner {kind}">{msg} · {html.escape(c["gate_rule"])}</div>'
        note = f'<div class="note">{chips(c["note"])}</div>' if c["note"] else ""
        n_taps = counts.get((c["owner_role"], c["category"]), 0)
        if n_taps and c["state"] != "Dismissed":
            note += (f'<div class="learned">↓ Ranked lower ({c["relevance_score"]:.2f} → {rank:.2f}) after '
                     f'{c["owner_role"]} marked {n_taps} similar {c["category"]} card{"s" if n_taps > 1 else ""} '
                     f'not relevant. Only {c["owner_role"]}\'s ranking changed.</div>')
        if c["flag"]:
            banner += f'<div class="banner bad">LLM wording not verified: {html.escape(c["flag"])}</div>'
        st.markdown(f'{head}<div class="action">{html.escape(c["suggested_action"])}'
                    f'{by_pill(c["written_by"], flag=c["flag"])}</div>'
                    f'<div class="why">Why: {chips(engine.card_why(c, e))}</div>{note}{banner}',
                    unsafe_allow_html=True)

        if c["state"] == "Dismissed":
            if st.button("Restore", key=f"rs{c['id']}"):
                act(workflow.restore, con, c["id"])
            return
        b1, b2 = st.columns([1, 1])
        if b2.button("Not relevant ×", key=f"nr{c['id']}", help="One tap: set this aside and rank similar cards "
                     "lower for your team only. Other teams' rankings don't change."):
            act(workflow.not_relevant, con, c["id"])
        if c["state"] == "Surfaced":
            if b1.button("Accept", key=f"acc{c['id']}"):
                act(workflow.accept, con, c["id"])
        elif c["state"] == "Drafted":
            label = "Decide" if c["owner_role"] == "Strategy" else "Execute"
            if workflow.can_execute(con, c):
                if b1.button(label, key=f"ex{c['id']}", type="primary"):
                    act(workflow.execute, con, c["id"])
            else:
                a = db.latest_approval(con, c["id"])
                if a and a["state"] == "Rejected":
                    if b1.button("Ask Strategy again", key=f"aa{c['id']}"):
                        act(workflow.ask_again, con, c["id"])
                else:
                    b1.button("Awaiting Strategy approval", key=f"ex{c['id']}", disabled=True)
        if c["state"] != "Executed":
            h1, h2 = st.columns([2, 1])
            to = h1.selectbox("Hand off to", [r for r in engine.ROLES if r != c["owner_role"]],
                              key=f"to{c['id']}", label_visibility="collapsed")
            if h2.button("Hand off", key=f"ho{c['id']}", width="stretch"):
                with st.spinner("Re-wording for the new team…" if client else "Handing off…"):
                    act(workflow.handoff, con, c["id"], to, e, client)
        with st.expander("Show source & score"):
            fact = e.facts.get(c["fact_id"])
            if fact is None:
                st.write("The fact behind this card isn't in the current analysis.")
                return
            w = engine.WEIGHTS[c["category"]][c["owner_role"]]
            learned = workflow.learned_multiplier(con, c["owner_role"], c["category"], counts)
            st.code(f"base = magnitude × (1 + change/100) × confidence\n"
                    f"     = {fact.magnitude:.3f} × (1 + {fact.change_pct:.1f}/100) × {fact.confidence:.2f}"
                    f" = {c['base']:.3f}\n"
                    f"relevance = base × weight[{c['category']}][{c['owner_role']}] × learned[{c['owner_role']}]\n"
                    f"          = {c['base']:.3f} × {w} × {learned:.2f} = {c['relevance_score'] * learned:.3f}\n"
                    f"learned = {workflow.LEARN_RATE} ^ (\"not relevant\" taps by {c['owner_role']} on "
                    f"{c['category']} cards), min {workflow.LEARN_FLOOR}", language=None)
            show_fact(e, fact.id)
            ev = pd.DataFrame(db.events(con, c["id"]))
            if not ev.empty:
                st.caption("History")
                st.dataframe(ev[["event", "from_role", "to_role", "from_state", "to_state", "at"]],
                             hide_index=True, width="stretch")


with tab_board:
    if e is None:
        st.info("Ask Winston first (tab ①).")
    else:
        st.header(f"Team board · {role}")
        lead = ("Approvals waiting for you come first. Approving runs the action; rejecting sends it back."
                if role == "Strategy" else
                "Your team's actions, most relevant first. Anything public, paid or price-related needs "
                "Strategy's approval before it can run.")
        st.markdown(f'<div class="lead">{lead} Switch team in the sidebar.</div>', unsafe_allow_html=True)
        m = workflow.metrics(con, e, sentences)
        st.markdown(tiles([(m["Executed"], "executed"), (m["In progress"], "in progress"),
                           (m["Awaiting approval"], "awaiting approval"), (m["Not relevant"], "marked not relevant"),
                           (m["Avg hand-offs to execution"], "avg hand-offs to execution")], accent_first=True),
                    unsafe_allow_html=True)
        counts = db.feedback_counts(con)
        allmine = [c for c in db.cards(con, "work", e.id) if c["owner_role"] == role]
        dismissed = [c for c in allmine if c["state"] == "Dismissed"]
        mine = [c for c in allmine if c["state"] != "Dismissed"]
        mine.sort(key=lambda c: (c["state"] == "Executed", -workflow.ranking(con, c, counts)))
        if role == "Strategy":
            pending = [a for a in db.cards(con, "approval", e.id) if a["state"] == "Pending approval"]
            pending.sort(key=lambda c: -c["relevance_score"])
            mine = pending + mine
        if not mine:
            st.info(f"No cards for {role} in this analysis.")
        cols = st.columns(2)
        for i, c in enumerate(mine):
            with cols[i % 2]:
                render_card(c)
        if dismissed:
            with st.expander(f"Marked not relevant by {role} ({len(dismissed)})"):
                for c in dismissed:
                    render_card(c)

# ------------------------------------------------------------- ④ Agent trace
with tab_trace:
    if e is None:
        st.info("Ask Winston first (tab ①).")
    else:
        st.header("Agent trace")
        st.markdown('<div class="lead">Which agent acted, what it did and on what evidence. LLM agents plan and '
                    'write; code agents calculate, check, route and gate; people approve.</div>',
                    unsafe_allow_html=True)
        st.markdown('<div class="roster">' + "".join(
            f'<div><span class="agent {kind}">{name}</span>{"LLM" if kind == "llm" else "code"}<br>'
            f'<span class="why">{html.escape(job)}</span></div>' for name, (kind, job) in engine.AGENTS.items())
            + '<div><span class="agent human">People</span>each team<br><span class="why">Accept, execute, hand '
              'off, mark not relevant; Strategy approves or rejects</span></div></div>', unsafe_allow_html=True)
        st.caption("Flow: Planner → Analyst → Playbook → Writer ⇄ Governance → Router → Framer ⇄ Governance → "
                   "Strategy approves → action runs → the team's feedback teaches the Router")
        rows = db.trace(con, e.id)
        if not rows:
            st.info("No trace for this analysis (it was created before the trace existed).")
        for r in rows:
            ev = chips(" ".join(f"[{x}]" for x in r["evidence"] if x)) if r["evidence"] else ""
            detail = f'<div class="why">{html.escape(r["detail"])}</div>' if r["detail"] else ""
            st.markdown(f'<div class="tstep"><span class="agent {KIND_CLASS.get(r["kind"], "code")}">'
                        f'{html.escape(r["agent"])}</span><b>{html.escape(r["action"])}</b> {ev}{detail}</div>',
                        unsafe_allow_html=True)
