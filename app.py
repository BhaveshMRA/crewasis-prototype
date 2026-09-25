"""CREWASIS · FDE in a Box — Streamlit demo.

Run:  streamlit run app.py
"""
import pandas as pd
import streamlit as st

import db
import engine
import workflow

st.set_page_config(page_title="CREWASIS · FDE in a Box", page_icon="🧭", layout="wide")

STATE_ICON = {"Surfaced": "🔵", "Drafted": "🟣", "Executed": "✅", "Pending approval": "🟠",
              "Approved": "✅", "Rejected": "⛔", "Withdrawn": "⚪"}
LENS_TITLES = {"product": "Where the product is failing", "competitor": "Competitors", "customer": "Customer discovery",
               "channel": "Channels", "retention": "Retention", "community": "Where buyers talk"}


@st.cache_data
def run_engine(problem: str, simulate_mistake: bool) -> engine.Engagement:
    return engine.run(problem, simulate_mistake)


@st.cache_resource
def connection():
    return db.connect()


con = connection()

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.markdown("## CREWASIS\n**FDE in a Box** · demo")
    role = st.selectbox("View the Team board as", engine.ROLES, key="role")
    st.divider()
    simulate = st.checkbox("Simulate an LLM mistake", help="Pretends an LLM rewrote the texture finding and got "
                           "the number wrong, so you can watch the Check step catch it.")
    if st.button("Reset demo"):
        db.reset(con)
        st.session_state.pop("problem_run", None)
        st.rerun()
    st.caption("All data is synthetic. ProForge, CleanBar Co, MuscleMint and WheyWise are fictional brands.")

problem = st.session_state.get("problem_run", engine.DEMO_PROBLEM)
e = run_engine(problem, simulate)
if db.is_empty(con) and "problem_run" in st.session_state:
    workflow.seed(con, e)
ran = not db.is_empty(con)

tab_ask, tab_brief, tab_board = st.tabs(["① Ask the FDE", "② Brief", "③ Team board"])


def rows_table(ids, limit=8) -> pd.DataFrame:
    rows = [e_rows[i] for i in ids[:limit] if i in e_rows]
    return pd.DataFrame(rows).astype(str) if rows else pd.DataFrame()


e_rows = engine.load().rows


def show_fact(fid: str):
    f = e.facts[fid]
    st.markdown(f"**{fid} · {f.title}** · lens: {f.lens} · source confidence {f.confidence:.2f}")
    st.json(f.computation, expanded=False)
    by_source = {}
    for i in f.evidence_ids:
        by_source.setdefault(i[0], []).append(i)
    for prefix, ids in by_source.items():
        name = e.sources.set_index("prefix").loc[prefix, "name"]
        st.caption(f"{name}: {len(ids)} rows used" + (f", first {min(8, len(ids))} shown" if len(ids) > 8 else ""))
        st.dataframe(rows_table(ids), hide_index=True, width="stretch")


# ------------------------------------------------------------ ① Ask the FDE
with tab_ask:
    st.header("Ask the FDE")
    c1, c2 = st.columns([1, 2])
    with c1:
        st.subheader("Brand profile")
        for k, v in engine.BRAND_PROFILE.items():
            st.markdown(f"**{k.replace('_', ' ').capitalize()}:** {v}")
    with c2:
        st.subheader("Your problem")
        text = st.text_area("Describe the business problem in plain words", engine.DEMO_PROBLEM, height=110)
        if st.button("Run the FDE", type="primary"):
            st.session_state["problem_run"] = text
            db.reset(con)
            st.rerun()
    if ran:
        st.success("Analysis done. Open **② Brief** for the findings and **③ Team board** for the actions.")
        st.markdown("**Plan:** " + " · ".join(f"✓ {LENS_TITLES[l]}" for l in e.plan["lenses"]))
        st.caption("Questions: " + " · ".join(e.plan["questions"]))

# ------------------------------------------------------------------ ② Brief
with tab_brief:
    if not ran:
        st.info("Run the FDE first (tab ①).")
    else:
        st.header("Brief · ProForge")
        st.markdown(f"> **Problem:** {e.problem}")
        passed = sum(s.status == "passed" for s in e.sentences)
        loaded = " · ".join(f"{r.rows_loaded:,} {r['name'].lower()} rows" for _, r in e.sources.iterrows())
        c1, c2 = st.columns([3, 1])
        c1.caption(f"Looked at: {loaded}")
        c2.metric("Citation check passed", f"{passed}/{len(e.sentences)}")
        for s in e.sentences:
            if s.status == "fell_back":
                st.warning(f"**Check caught a bad sentence** ({s.fact_id}): {s.problem}. "
                           f"It was replaced by the template sentence built from the fact.")

        st.subheader("Findings")
        for lens in engine.LENSES:
            fs = [f for f in e.facts.values() if f.lens == lens]
            if not fs:
                continue
            st.markdown(f"##### {LENS_TITLES[lens]}")
            for f in fs:
                s = next(x for x in e.sentences if x.fact_id == f.id and x.section == "findings")
                st.markdown(f"- {s.text}" + ("  ⚠ *fell back to template*" if s.status == "fell_back" else ""))
                with st.expander(f"Rows behind {f.id}"):
                    show_fact(f.id)

        st.subheader("Likely root causes")
        st.dataframe(pd.DataFrame([{"#": i, "Root cause": r["cause"], "Confidence (by rule)": r["confidence"],
                                    "Facts": ", ".join(r["facts"]), "Rows": r["rows"],
                                    "Source types": ", ".join(f"{k}: {v}" for k, v in r["sources"].items())}
                                   for i, r in enumerate(e.root_causes, 1)]), hide_index=True,
                     width="stretch")
        st.caption("High = 2+ source types with 30+ rows · Medium = 1 source type with 30+ rows, or 2 types with fewer "
                   "· Low = otherwise.")

        st.subheader("Retention plan")
        st.caption("Tips come from a fixed playbook. A play appears only when ProForge's own numbers match its rule.")
        ret = sorted([p for p in e.plays if p.section == "retention"],
                     key=lambda p: (not p.matched, engine.EFFORT_ORDER[p.effort]))
        for p in ret:
            if p.matched:
                owner = "R&D (added to the texture card)" if p.merge_into else p.owner
                st.markdown(f"- **{p.effort.capitalize()}** · {p.plan_line} → *{owner}*  \n"
                            f"  <small>Rule: {p.rule} · Metric to watch: {p.metric}"
                            f"{' · Do after: ' + p.do_after if p.do_after else ''}</small>",
                            unsafe_allow_html=True)
        for p in ret:
            if not p.matched:
                st.markdown(f"- ✗ **Not matched · {p.name}:** {p.reason} (rule: {p.rule}).")

        st.subheader("Where to show up")
        st.dataframe(e.community_table.rename(columns={
            "community": "Community", "posts": "Posts this quarter", "competitor_mentions": "Competitor mentions",
            "unanswered": "Unanswered questions", "share_of_conversation": "Share of conversation",
            "proforge_mentions": "ProForge mentions"}), hide_index=True, width="stretch")
        for p in [p for p in e.plays if p.section == "community" and p.matched]:
            st.markdown(f"- {p.plan_line} → *{p.owner}, needs Strategy's approval*")
        st.caption("Reply openly as the brand and follow each community's self-promotion rules: no fake accounts, "
                   "no posing as customers.")

        work = db.cards(con, "work")
        st.subheader(f"Recommendations → {len(work)} cards on the Team board")
        st.dataframe(pd.DataFrame([{"Owner": c["owner_role"], "Action": c["suggested_action"],
                                    "From": c["play_id"] or c["fact_id"],
                                    "Needs approval": c["gate_rule"] or ""} for c in
                                   sorted(work, key=lambda c: -c["relevance_score"])]),
                     hide_index=True, width="stretch")

        st.subheader("What we couldn't check")
        for g in e.gaps:
            st.markdown(f"- {g}")

        with st.expander("Sources used", expanded=False):
            st.dataframe(e.sources[["prefix", "name", "file", "description", "collected_at", "synthetic",
                                    "confidence", "rows_loaded", "rows_cited"]], hide_index=True,
                         width="stretch")

# ------------------------------------------------------------- ③ Team board


def render_card(c: dict):
    with st.container(border=True):
        tag = c["play_id"] or c["fact_id"]
        st.markdown(f"**[{c['owner_role']}]** {STATE_ICON.get(c['state'], '')} {c['state']} · `{tag}` · "
                    f"score **{c['relevance_score']:.2f}**")
        st.caption(c["evidence"])
        if c["kind"] == "approval":
            st.markdown(f"**Approval needed:** {c['gate_rule']}")
            if c["state"] == "Pending approval":
                b1, b2 = st.columns(2)
                if b1.button("Approve", key=f"ap{c['id']}", type="primary"):
                    workflow.approve(con, c["id"]); st.rerun()
                if b2.button("Reject", key=f"rj{c['id']}"):
                    workflow.reject(con, c["id"]); st.rerun()
            return
        st.markdown(f"→ **{c['suggested_action']}**")
        if c["note"]:
            st.caption(c["note"])
        if c["requires_approval"]:
            a = db.latest_approval(con, c["id"])
            status = {"Pending approval": "⚠ Requires approval · awaiting Strategy",
                      "Approved": "✓ Approved by Strategy",
                      "Rejected": "⛔ Rejected by Strategy · revise or hand off"}.get(a["state"] if a else "", "")
            st.markdown(f"<small>{status} ({c['gate_rule']})</small>", unsafe_allow_html=True)
        if c["state"] == "Surfaced":
            if st.button("Accept", key=f"acc{c['id']}"):
                workflow.accept(con, c["id"]); st.rerun()
        elif c["state"] == "Drafted":
            label = "Decide" if c["owner_role"] == "Strategy" else "Execute"
            if workflow.can_execute(con, c):
                if st.button(label, key=f"ex{c['id']}", type="primary"):
                    workflow.execute(con, c["id"]); st.rerun()
            else:
                a = db.latest_approval(con, c["id"])
                if a and a["state"] == "Rejected":
                    if st.button("Ask Strategy again", key=f"aa{c['id']}"):
                        workflow.ask_again(con, c["id"]); st.rerun()
                else:
                    st.button("Awaiting Strategy approval", key=f"ex{c['id']}", disabled=True)
        if c["state"] != "Executed":
            h1, h2 = st.columns([2, 1])
            to = h1.selectbox("Hand off to", [r for r in engine.ROLES if r != c["owner_role"]],
                              key=f"to{c['id']}", label_visibility="collapsed")
            if h2.button("Hand off", key=f"ho{c['id']}"):
                workflow.handoff(con, c["id"], to, e); st.rerun()
        with st.expander("Show source & score"):
            f = e.facts[c["fact_id"]]
            w = engine.WEIGHTS[c["category"]][c["owner_role"]]
            st.code(f"base = magnitude × (1 + change/100) × confidence\n"
                    f"     = {f.magnitude:.3f} × (1 + {f.change_pct:.1f}/100) × {f.confidence:.2f} = {c['base']:.3f}\n"
                    f"relevance = base × weight[{c['category']}][{c['owner_role']}]\n"
                    f"          = {c['base']:.3f} × {w} = {c['relevance_score']:.3f}", language=None)
            show_fact(f.id)
            ev = pd.DataFrame(db.events(con, c["id"]))
            if not ev.empty:
                st.caption("History")
                st.dataframe(ev[["event", "from_role", "to_role", "from_state", "to_state", "at"]],
                             hide_index=True, width="stretch")


with tab_board:
    if not ran:
        st.info("Run the FDE first (tab ①).")
    else:
        st.header(f"Team board · viewing as {role}")
        m = workflow.metrics(con, e)
        for col, (k, v) in zip(st.columns(len(m)), m.items()):
            col.metric(k, v)
        mine = [c for c in db.cards(con, "work") if c["owner_role"] == role]
        mine.sort(key=lambda c: (c["state"] == "Executed", -c["relevance_score"]))
        if role == "Strategy":
            pending = [a for a in db.cards(con, "approval") if a["state"] == "Pending approval"]
            pending.sort(key=lambda c: -c["relevance_score"])
            mine = pending + mine
        if not mine:
            st.info(f"No cards for {role} right now.")
        cols = st.columns(2)
        for i, c in enumerate(mine):
            with cols[i % 2]:
                render_card(c)
