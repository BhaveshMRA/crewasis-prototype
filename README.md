# CREWASIS — Role-Aware Recommendation Cards (Hackathon Prototype)

CREWASIS is a decision-intelligence platform for consumer brands. This prototype shows one idea:
**the same underlying signal is scored and reframed differently depending on which team is looking at it**,
and each recommendation card moves through a lifecycle with hand-offs between teams.

- **Demo brand:** GlowNest (fictional skincare/wellness brand)
- **Signal source:** Instagram, **synthetic only**. No real API calls; `synthetic_data.csv` is the only data source.
- **Teams (roles):** Marketing · Insights · R&D · Strategy
- **Card lifecycle:** `Surfaced → Drafted → Pending approval → Executed`

---

## Quick start

```bash
pip install -r requirements.txt
```

```bash
export OPENAI_API_KEY=sk-...
```

```bash
streamlit run app.py
```

Requires **Python 3.11+**. On first launch the app loads the CSV into SQLite and runs the pipeline once
(2 LLM calls × 50 rows). Later launches reuse the stored cards and make no LLM calls.

To re-run the pipeline from scratch, delete the database file and restart:

```bash
rm crewasis.db
```

### Using Anthropic instead of OpenAI (optional)

The LLM client is a thin wrapper in `pipeline.py`. To switch providers:

```bash
export LLM_PROVIDER=anthropic
export ANTHROPIC_API_KEY=sk-ant-...
```

---

## Project structure

```
.
├── app.py               # Streamlit UI: role selector, card grid, hand-off controls
├── pipeline.py          # LangGraph graph (5 nodes) + thin LLM wrapper (OpenAI / Anthropic)
├── db.py                # SQLite setup and helpers (built-in sqlite3, no ORM)
├── scoring.py           # Deterministic scoring, role-weight table, approval-gate rules
├── synthetic_data.csv   # 50 synthetic GlowNest / Instagram signals
├── requirements.txt
└── README.md
```

**Stack:** Python · SQLite · LangGraph · OpenAI API (swappable for Anthropic) · Streamlit.
There is **no vector DB**: with 50 rows, relevant rows go straight into the prompt.

---

## Data

`synthetic_data.csv` has 50 rows with these columns:

| column | meaning |
|---|---|
| `id` | row id |
| `brand` | always `GlowNest` |
| `platform` | always `Instagram` |
| `raw_signal` | the observed signal text |
| `mention_count` | number of mentions |
| `velocity_pct` | week-over-week change in % (can be negative) |
| `confidence` | 0–1 confidence in the signal |
| `category` | e.g. packaging, ingredient, sentiment, competitor, pricing |
| `source_type` | e.g. comment, post, story, influencer |
| `date_observed` | date the signal was observed |

---

## Database schema (SQLite)

**`insights`**: the CSV rows, stored unchanged.

**`cards`**: one row per processed insight, plus the extra Strategy approval cards:

| column | type | notes |
|---|---|---|
| `id` | INTEGER PK | |
| `insight_id` | INTEGER FK → `insights.id` | |
| `owner_role` | TEXT | `Marketing` \| `Insights` \| `R&D` \| `Strategy` |
| `state` | TEXT | `Surfaced` \| `Drafted` \| `Pending approval` \| `Executed` |
| `relevance_score` | REAL | 0–1 |
| `evidence` | TEXT | human-readable; cites the raw signal and its numbers |
| `suggested_action` | TEXT | next step worded for the owning role |
| `requires_approval` | BOOLEAN | set by the Gate |
| `created_at`, `updated_at` | TIMESTAMP | |

---

## Pipeline (LangGraph, 5 nodes, runs once per insight)

```
Tag (LLM) → Score → RoleWeight → Frame (LLM) → Gate ──► persist card
                                                  └─(requires_approval)──► + Strategy card, "Pending approval"
```

Each insight gets **exactly two LLM calls** (Tag and Frame). Scoring and gating are plain Python
that you can inspect.

| # | Node | Kind | Input → Output |
|---|---|---|---|
| 1 | **Tag** | LLM (JSON output) | `raw_signal`, `category`, `source_type` → short `evidence` summary + suggested `owner_role` |
| 2 | **Score** | deterministic | numeric columns → base `relevance_score` |
| 3 | **RoleWeight** | deterministic | `category × owner_role` lookup → adjusted score |
| 4 | **Frame** | LLM | `evidence` + `owner_role` → `suggested_action` worded for that role |
| 5 | **Gate** | deterministic | `suggested_action` → `requires_approval` (+ Strategy branch) |

### Score formula

```
relevance_score = clip( normalize(mention_count) × (1 + velocity_pct/100) × confidence , 0, 1 )
```

`normalize` is min–max scaling across the 50 rows. Every input and intermediate value is logged, so any
score can be recomputed by hand during judge Q&A.

### Role weights

A hardcoded `category × role` table in `scoring.py` multiplies the base score. The result is clipped to 0–1.
Example of what the table looks like:

| category | Marketing | Insights | R&D | Strategy |
|---|---|---|---|---|
| packaging | 0.8 | 1.0 | 1.3 | 1.0 |
| ingredient | 0.9 | 1.0 | 1.3 | 1.0 |
| sentiment | 1.2 | 1.2 | 0.8 | 1.0 |
| competitor | 1.0 | 1.1 | 0.9 | 1.3 |
| pricing | 1.0 | 1.0 | 0.7 | 1.3 |

### Framing examples

The same signal gets a different next step for each role:

- **Marketing:** "Draft a post about X"
- **Insights:** "Size the trend around X across the last 4 weeks"
- **R&D:** "Review formulation for X"
- **Strategy:** "Assess whether X warrants a budget shift"

### Approval gate

A deterministic keyword/rule table in `scoring.py` decides whether a card needs approval.
`requires_approval = true` when `suggested_action` implies either:

- **customer-facing claims** (e.g. post, campaign, announce, claim, publish), or
- **budget spend** (e.g. spend, budget, paid, sponsor, launch).

When it is true, the pipeline also creates a **second card routed to Strategy** with state `Pending approval`.

---

## UI

- **Role selector** at the top: Marketing / Insights / R&D / Strategy.
- **Card grid** for the selected role, sorted by `relevance_score`, highest first. Each card shows:
  - `owner_role` badge and `state` badge
  - `relevance_score`
  - evidence text
  - suggested action
  - **Requires approval** flag, when set
- **Primary action button**, labelled with the suggested action.
- **Hand off** control: pick the next `owner_role` and `state`. This re-runs **Frame** and **Gate** for
  the new role and updates the card in place, so you can watch the card change as it moves between teams.

On startup the app loads the CSV and runs the pipeline **only if `cards` is empty**. Reloading the app does
not call the LLM again.

---

## What to check first

1. **The startup run finished.** Confirm the `cards` table has at least 50 rows, plus one extra
   Strategy card for each gated card:
   ```bash
   sqlite3 crewasis.db "SELECT owner_role, state, COUNT(*) FROM cards GROUP BY 1,2;"
   ```
2. **Caching works.** Restart `streamlit run app.py`. It should load instantly and the logs should show
   no LLM calls.
3. **Scores add up.** Pick one card, find its logged formula inputs, and recompute the score by hand.
4. **Views differ by role.** Switch between Marketing and R&D. Packaging and ingredient signals should rank
   higher for R&D, and the wording of the actions should change.
5. **Gate and Strategy branch.** Find a Marketing card with a customer-facing action ("Draft a post…").
   It should show **Requires approval**, and a matching `Pending approval` card should appear in the
   Strategy view.
6. **Hand-off.** Hand a card from Insights to Marketing. The suggested action should be reworded, the
   approval flag re-evaluated, and `updated_at` bumped.

---

## Suggested demo flow (about 3 minutes)

1. Open the **Insights** view and point out one strong signal with its evidence and score.
2. Switch to **R&D** and show the same kind of signal ranked and worded differently.
3. **Hand off** an Insights card to Marketing. The action becomes "Draft a post…" and gets flagged
   **Requires approval**.
4. Open **Strategy** and show the new `Pending approval` card waiting for sign-off.
5. Judge Q&A: show the logged score inputs and the rule table. Only two steps use an LLM; ranking and
   approval are deterministic.

---

## Constraints and non-goals

- No real Instagram API calls; the CSV is the only data source.
- Exactly two LLM calls per insight (Tag, Frame); no extra agent hops.
- No vector search (ChromaDB, Pinecone, etc.). The dataset is small enough to put straight into prompts.
- No auth, multi-user support, or real approval workflow. This is a single-user demo.
