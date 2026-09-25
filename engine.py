"""CREWASIS FDE engine: source rows → facts → retention plays → brief → cards.

Plain Python, no LLM. Every number is calculated here from source rows, and every fact keeps the
ids of the rows it used, so the brief can cite them and anyone can recompute them.

Run `python engine.py` to print the facts, plays and cards.
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

DATA = Path(__file__).parent / "data"
AS_OF = date(2026, 9, 24)
QUARTERS = {"last": (date(2026, 4, 1), date(2026, 6, 30)), "this": (date(2026, 7, 1), date(2026, 9, 24))}
ROLES = ["Marketing", "Insights", "R&D", "Strategy"]
LENSES = ["product", "competitor", "customer", "channel", "retention", "community"]
LAPSED_AFTER_DAYS = 60

BRAND_PROFILE = {
    "brand": "ProForge Nutrition",
    "products": "Protein bars + whey",
    "price": "₹120 per bar",
    "channels": "marketplace, own website (D2C)",
    "competitors": "CleanBar Co, MuscleMint, WheyWise",
    "value_unit": "grams of protein",
}
DEMO_PROBLEM = ("Online sales of our protein bars fell 20% this quarter, and repeat purchase dropped from "
                "38% to 29%. Why, and what should we do?")


# ------------------------------------------------------------------ data
@dataclass
class Data:
    sources: pd.DataFrame
    reviews: pd.DataFrame
    social: pd.DataFrame
    competitors: pd.DataFrame
    sales: pd.DataFrame
    orders: pd.DataFrame
    rows: dict  # evidence id → row dict


def load() -> Data:
    sources = pd.read_csv(DATA / "sources.csv")
    reviews = pd.read_csv(DATA / "reviews.csv", parse_dates=["date"], keep_default_na=False)
    social = pd.read_csv(DATA / "social_signals.csv", parse_dates=["date"], keep_default_na=False)
    competitors = pd.read_csv(DATA / "competitors.csv", keep_default_na=False)
    sales = pd.read_csv(DATA / "sales.csv")
    orders = pd.read_csv(DATA / "orders.csv", parse_dates=["order_date"], keep_default_na=False)
    rows = {}
    for df in (reviews, social, competitors, sales, orders):
        for r in df.to_dict("records"):
            rows[r["id"]] = r
    return Data(sources, reviews, social, competitors, sales, orders, rows)


def in_quarter(dates: pd.Series, q: str) -> pd.Series:
    start, end = QUARTERS[q]
    return (dates.dt.date >= start) & (dates.dt.date <= end)


# ----------------------------------------------------------------- facts
@dataclass
class Fact:
    id: str
    key: str
    lens: str
    category: str
    title: str
    sentence: str               # the brief sentence, with citations
    magnitude: float            # 0–1: how big the problem is
    change_pct: float           # how the magnitude moved vs last quarter (0 if no earlier period)
    confidence: float           # lowest confidence of the sources used
    evidence_ids: list
    computation: dict           # every input and intermediate value
    default_owner: str | None = None  # None: becomes a card only through a retention play


def pct(x: float) -> str:
    """0.225 → '22.5%', 0.34 → '34%'."""
    return f"{x * 100:.1f}".rstrip("0").rstrip(".") + "%"


def cite(ids, n=3) -> str:
    return " ".join(f"[{i}]" for i in list(ids)[:n])


# Complaint themes, assigned by keyword; the first match wins.
THEMES = [
    ("texture", r"\b(chalky|dry|gritty|cardboard)\b"),
    ("packaging", r"\b(melted|melting|wrapper|torn|tore)\b"),
    ("digestion", r"\b(bloat\w*|stomach|gas)\b"),
    ("delivery", r"\b(late|delivery)\b"),
    ("price", r"\b(expensive|overpriced|pricey|cheaper|price)\b"),
    ("taste", r"\b(taste\w*|flavou?r|sweet\w*|aftertaste)\b"),
]
FATIGUE = r"\b(?:bored of|same flavou?r)\b"


def theme_of(text: str) -> str:
    for theme, pattern in THEMES:
        if re.search(pattern, text, re.I):
            return theme
    return "other"


def confidence_of(d: Data, prefixes) -> float:
    conf = dict(zip(d.sources.prefix, d.sources.confidence))
    return min(conf[p] for p in prefixes)


def product_lens(d: Data) -> list[Fact]:
    r = d.reviews.copy()
    r["theme"] = r.text.map(theme_of)
    low = r[r.rating <= 2]
    this, last = low[in_quarter(low.date, "this")], low[in_quarter(low.date, "last")]
    facts = []
    for fid, theme, words, category, title in (
        ("F1", "texture", "chalky, dry or gritty texture", "product", "Texture complaints"),
        ("F6", "packaging", "a melted bar or a torn wrapper", "packaging", "Melted bars and torn wrappers"),
    ):
        n_this, n_last = int((this.theme == theme).sum()), int((last.theme == theme).sum())
        share, prev = n_this / len(this), n_last / len(last)
        change = (share - prev) / prev * 100
        ids = list(this[this.theme == theme].id) + list(last[last.theme == theme].id)
        top = this.theme.value_counts().idxmax()
        lead = ("Texture is the top complaint: " if theme == top else "")
        sentence = (f"{lead}{pct(share)} of this quarter's 1–2★ bar reviews mention {words}, "
                    f"up from {pct(prev)} last quarter [{fid}] {cite(ids)}.")
        facts.append(Fact(fid, theme, "product", category, title, sentence, share, change,
                          confidence_of(d, "R"), ids,
                          {f"{theme}_reviews_this_quarter": n_this, "low_reviews_this_quarter": len(this),
                           "share_this_quarter": round(share, 4), f"{theme}_reviews_last_quarter": n_last,
                           "low_reviews_last_quarter": len(last), "share_last_quarter": round(prev, 4),
                           "change_pct": round(change, 1)},
                          "R&D"))
    return facts


def competitor_lens(d: Data) -> list[Fact]:
    bars = d.competitors[d.competitors.format == "bar"].copy()
    bars["price_per_g"] = bars.price_inr / (bars.servings * bars.protein_g_per_serving)
    own = bars[bars.brand == "ProForge"].iloc[0]
    rivals = bars[bars.brand != "ProForge"]
    cheapest = rivals.loc[rivals.price_per_g.idxmin()]
    gap = (own.price_per_g - cheapest.price_per_g) / cheapest.price_per_g
    f2 = Fact("F2", "price_gap", "competitor", "pricing", "Price per gram of protein",
              f"ProForge's bar costs ₹{own.price_per_g:.1f} per gram of protein; {cheapest.brand}'s costs "
              f"₹{cheapest.price_per_g:.1f}, so ProForge is {pct(gap)} more expensive [F2] [{own.id}] [{cheapest.id}].",
              gap, 0.0, confidence_of(d, "P"), [own.id] + list(rivals.id),
              {"proforge_price_inr": int(own.price_inr), "proforge_protein_g": int(own.protein_g_per_serving),
               "proforge_price_per_g": round(own.price_per_g, 2), "cheapest_rival": cheapest.brand,
               "rival_price_inr": int(cheapest.price_inr), "rival_protein_g": int(cheapest.protein_g_per_serving),
               "rival_price_per_g": round(cheapest.price_per_g, 2), "gap": round(gap, 4),
               "price_gap_inr_per_g": round(own.price_per_g - cheapest.price_per_g, 2)},
              "Strategy")

    claim = "no added sugar"
    claimers = rivals[rivals.claims.str.contains(claim)]
    s = d.social
    q_this = s[in_quarter(s.date, "this") & (s.signal_type == "question")]
    q_last = s[in_quarter(s.date, "last") & (s.signal_type == "question")]
    sugar_this, sugar_last = q_this[q_this.topic == "sugar"], q_last[q_last.topic == "sugar"]
    share, prev = len(sugar_this) / len(q_this), len(sugar_last) / len(q_last)
    change = (share - prev) / prev * 100
    f3 = Fact("F3", "sugar_positioning", "competitor", "positioning", "Low-sugar positioning",
              f"{len(claimers)} of {len(rivals)} competitors lead with “{claim}”, while ProForge only claims "
              f"“{own.claims}” even though its bar has {own.sugar_g_per_serving} g of sugar; sugar is now "
              f"{pct(share)} of the questions people ask about ProForge, up from {pct(prev)} "
              f"[F3] {cite(list(claimers.id) + list(sugar_this.id), 3)}.",
              share, change, confidence_of(d, "PS"), list(claimers.id) + [own.id] + list(sugar_this.id),
              {"competitors_claiming_no_added_sugar": len(claimers), "competitors": len(rivals),
               "proforge_sugar_g": int(own.sugar_g_per_serving), "sugar_questions_this_quarter": len(sugar_this),
               "questions_this_quarter": len(q_this), "share_this_quarter": round(share, 4),
               "sugar_questions_last_quarter": len(sugar_last), "questions_last_quarter": len(q_last),
               "share_last_quarter": round(prev, 4), "change_pct": round(change, 1)},
              "Marketing")
    return [f2, f3]


def customer_lens(d: Data) -> list[Fact]:
    s = d.social
    req_this = s[in_quarter(s.date, "this") & (s.signal_type == "request")]
    req_last = s[in_quarter(s.date, "last") & (s.signal_type == "request")]
    plant_this, plant_last = req_this[req_this.topic == "plant_protein"], req_last[req_last.topic == "plant_protein"]
    share, prev = len(plant_this) / len(req_this), len(plant_last) / len(req_last)
    change = (share - prev) / prev * 100
    growth = (len(plant_this) - len(plant_last)) / len(plant_last) * 100
    by_seg = d.reviews.groupby("reviewer_segment").rating.mean()
    veg, overall = round(by_seg["vegetarian"], 1), round(d.reviews.rating.mean(), 1)
    veg_ids = list(d.reviews[d.reviews.reviewer_segment == "vegetarian"].id)
    return [Fact("F4", "plant_protein", "customer", "customer", "Unmet plant-protein demand",
                 f"Requests for a plant-protein bar rose from {len(plant_last)} to {len(plant_this)} this quarter "
                 f"(+{growth:.0f}%) and are now {pct(share)} of all requests; vegetarian reviewers give ProForge its "
                 f"lowest rating, {veg}★ against {overall}★ overall [F4] {cite(list(plant_this.id), 2)} {cite(veg_ids, 1)}.",
                 share, change, confidence_of(d, "SR"), list(plant_this.id) + veg_ids,
                 {"plant_requests_this_quarter": len(plant_this), "plant_requests_last_quarter": len(plant_last),
                  "request_growth_pct": round(growth, 1), "requests_this_quarter": len(req_this),
                  "share_this_quarter": round(share, 4), "requests_last_quarter": len(req_last),
                  "share_last_quarter": round(prev, 4), "change_pct": round(change, 1),
                  "vegetarian_avg_rating": veg, "overall_avg_rating": overall,
                  "lowest_rated_segment": by_seg.idxmin()},
                 "Insights")]


def quarter_sales(d: Data, months, channel=None):
    m = d.sales[d.sales.month.isin(months)]
    if channel:
        m = m[m.channel == channel]
    return m


Q_MONTHS = {"last": ["2026-04", "2026-05", "2026-06"], "this": ["2026-07", "2026-08", "2026-09"]}


def channel_lens(d: Data) -> list[Fact]:
    mp_last = quarter_sales(d, Q_MONTHS["last"], "marketplace").units.sum()
    mp_this = quarter_sales(d, Q_MONTHS["this"], "marketplace").units.sum()
    all_last = quarter_sales(d, Q_MONTHS["last"]).units.sum()
    all_this = quarter_sales(d, Q_MONTHS["this"]).units.sum()
    mp_drop, all_drop = (mp_last - mp_this) / mp_last, (all_last - all_this) / all_last
    qc = d.competitors[(d.competitors.format == "bar") & d.competitors.channels.str.contains("quick_commerce")]
    ids = list(quarter_sales(d, Q_MONTHS["last"] + Q_MONTHS["this"], "marketplace").id)
    return [Fact("F5", "marketplace_drop", "channel", "channel", "Marketplace decline",
                 f"Marketplace bar sales fell {pct(mp_drop)} ({mp_last:,} → {mp_this:,} units) while total bar sales "
                 f"fell {pct(all_drop)}; {' and '.join(qc.brand)} sell on quick-commerce apps, where ProForge isn't "
                 f"listed [F5] {cite(ids[-2:], 2)} {cite(qc.id, 2)}.",
                 mp_drop, 0.0, confidence_of(d, "MP"), ids + list(qc.id),
                 {"marketplace_units_last_quarter": int(mp_last), "marketplace_units_this_quarter": int(mp_this),
                  "marketplace_drop": round(mp_drop, 4), "total_units_last_quarter": int(all_last),
                  "total_units_this_quarter": int(all_this), "total_drop": round(all_drop, 4),
                  "rivals_on_quick_commerce": len(qc)},
                 "Marketing")]


def customer_table(d: Data) -> pd.DataFrame:
    """One row per customer: orders, gaps between orders, lapsed or not."""
    o = d.orders.sort_values("order_date")
    rows = []
    for cid, g in o.groupby("customer_id"):
        dates = list(g.order_date.dt.date)
        rows.append({"customer_id": cid, "segment": g.segment.iloc[0], "subscription": g.subscription.iloc[0],
                     "orders": len(dates), "last_order": dates[-1],
                     "gaps": [(b - a).days for a, b in zip(dates, dates[1:])], "order_ids": list(g.id)})
    c = pd.DataFrame(rows)
    c["repeat"] = c.orders >= 2
    c["lapsed"] = c.repeat & (c.last_order < AS_OF - timedelta(days=LAPSED_AFTER_DAYS))
    return c


def retention_lens(d: Data) -> tuple[list[Fact], dict]:
    facts = []
    # F7 · repeat purchase rate by quarter (sales)
    def rate(months, ch=None):
        m = quarter_sales(d, months, ch)
        return m.repeat_customers.sum() / m.customers.sum()
    r_last, r_this = rate(Q_MONTHS["last"]), rate(Q_MONTHS["this"])
    mp_last, mp_this = rate(Q_MONTHS["last"], "marketplace"), rate(Q_MONTHS["this"], "marketplace")
    ids = list(quarter_sales(d, Q_MONTHS["last"] + Q_MONTHS["this"]).id)
    facts.append(Fact("F7", "repeat_rate", "retention", "retention", "Repeat purchase rate",
                      f"Repeat purchase fell from {pct(r_last)} to {pct(r_this)} this quarter, and fell hardest on "
                      f"marketplaces, from {pct(mp_last)} to {pct(mp_this)} [F7] {cite(ids[-2:], 2)}.",
                      (r_last - r_this) / r_last, 0.0, confidence_of(d, "M"), ids,
                      {"repeat_rate_last_quarter": round(r_last, 4), "repeat_rate_this_quarter": round(r_this, 4),
                       "marketplace_repeat_last_quarter": round(mp_last, 4),
                       "marketplace_repeat_this_quarter": round(mp_this, 4)}))

    c = customer_table(d)
    lapsed = c[c.lapsed]
    lapsed_order_ids = [i for ids in lapsed.order_ids for i in ids]

    # F8 · lapsed buyers and why they left
    r = d.reviews
    lapsed_reviews = r[r.customer_id.isin(set(lapsed.customer_id))].copy()
    lapsed_reviews["theme"] = lapsed_reviews.text.map(theme_of)
    top_theme = lapsed_reviews.theme.value_counts().idxmax()
    top_n = int((lapsed_reviews.theme == top_theme).sum())
    top_share = top_n / len(lapsed_reviews)
    tex_ids = list(lapsed_reviews[lapsed_reviews.theme == top_theme].id)
    facts.append(Fact("F8", "lapsed_buyers", "retention", "retention", "Lapsed buyers",
                      f"{len(lapsed)} buyers who ordered at least twice haven't ordered in {LAPSED_AFTER_DAYS} days; "
                      f"{pct(top_share)} of the reviews they left mention {top_theme} [F8] {cite(tex_ids, 2)} "
                      f"{cite(lapsed_order_ids, 1)}.",
                      top_share, 0.0, confidence_of(d, "OR"), tex_ids + lapsed_order_ids,
                      {"lapsed_buyers": len(lapsed), "lapsed_after_days": LAPSED_AFTER_DAYS,
                       "reviews_by_lapsed_buyers": len(lapsed_reviews), "top_theme": top_theme,
                       "top_theme_reviews": top_n, "top_theme_share": round(top_share, 4)}))

    # F9 · subscribers vs one-off buyers
    subs, once = c[c.subscription == "yes"], c[c.subscription == "no"]
    sub_share, sub_rate, once_rate = len(subs) / len(c), subs.repeat.mean(), once.repeat.mean()
    facts.append(Fact("F9", "subscribers", "retention", "retention", "Subscribers reorder more",
                      f"Subscribers are {pct(sub_share)} of buyers and {pct(sub_rate)} of them reorder, against "
                      f"{pct(once_rate)} of buyers who don't subscribe [F9] {cite(subs.order_ids.iloc[0], 1)}.",
                      (sub_rate - once_rate) / sub_rate, 0.0, confidence_of(d, "O"),
                      [i for ids in subs.order_ids for i in ids][:200],
                      {"customers": len(c), "subscribers": len(subs), "subscriber_share": round(sub_share, 4),
                       "subscriber_repeat_rate": round(sub_rate, 4), "non_subscriber_repeat_rate": round(once_rate, 4)}))

    # F10 · the reorder window lapsed buyers missed
    all_gaps = [g for gaps in c.gaps for g in gaps]
    median_gap = int(statistics.median(all_gaps))
    window = (median_gap - 6, median_gap + 6)
    on_cycle = lapsed[lapsed.gaps.map(lambda gs: all(window[0] <= g <= window[1] for g in gs))]
    share = len(on_cycle) / len(lapsed)
    cyc_ids = [i for ids in on_cycle.order_ids for i in ids]
    facts.append(Fact("F10", "reorder_window", "retention", "retention", "Missed reorder window",
                      f"Buyers usually reorder every {median_gap} days; {pct(share)} of lapsed buyers "
                      f"({len(on_cycle)} of {len(lapsed)}) were on that cycle and then missed their next order "
                      f"[F10] {cite(cyc_ids, 2)}.",
                      share, 0.0, confidence_of(d, "O"), cyc_ids,
                      {"median_days_between_orders": median_gap, "cycle_window_days": list(window),
                       "lapsed_on_cycle": len(on_cycle), "lapsed_buyers": len(lapsed),
                       "share": round(share, 4), "reminder_day": median_gap - 2, "gaps_measured": len(all_gaps)}))

    # F11 · second-order rate by segment
    seg = c.groupby("segment").repeat.mean()
    beg, gym = seg["beginner"], seg["gym_regular"]
    beg_ids = [i for ids in c[c.segment == "beginner"].order_ids for i in ids]
    facts.append(Fact("F11", "beginners", "retention", "retention", "Beginners don't come back",
                      f"Only {pct(beg)} of beginners place a second order, against {pct(gym)} of gym regulars "
                      f"[F11] {cite(beg_ids, 2)}.",
                      (gym - beg) / gym, 0.0, confidence_of(d, "O"), beg_ids,
                      {"beginner_second_order_rate": round(beg, 4), "gym_regular_second_order_rate": round(gym, 4),
                       "second_order_rate_by_segment": {k: round(v, 4) for k, v in seg.items()}}))

    # flavour fatigue among repeat buyers (only used by play PL7)
    repeat_ids = set(c[c.repeat].customer_id)
    rep_reviews = r[r.customer_id.isin(repeat_ids)]
    fatigue = rep_reviews[rep_reviews.text.str.contains(FATIGUE, case=False, regex=True)]
    extra = {"fatigue_reviews": len(fatigue), "repeat_buyer_reviews": len(rep_reviews),
             "fatigue_share": len(fatigue) / len(rep_reviews), "fatigue_ids": list(fatigue.id)}
    return facts, extra


def community_lens(d: Data) -> tuple[list[Fact], pd.DataFrame]:
    s = d.social
    this = s[in_quarter(s.date, "this")]
    last = s[in_quarter(s.date, "last")]
    open_q = this[this.signal_type.isin(["question", "request"]) & (this.answered_by_brand == "no")]

    table = (this.groupby("community")
             .agg(posts=("id", "count"),
                  competitor_mentions=("signal_type", lambda t: int((t == "competitor_mention").sum())))
             .reset_index())
    table["unanswered"] = table.community.map(open_q.community.value_counts()).fillna(0).astype(int)
    table["share_of_conversation"] = (table.posts / len(this)).round(3)
    table["proforge_mentions"] = table.community.map(
        this[this.brand_mentioned == "ProForge"].community.value_counts()).fillna(0).astype(int)
    table = table.sort_values("posts", ascending=False)

    top = open_q.community.value_counts()
    top_c, top_n = top.idxmax(), int(top.max())
    top_rows = open_q[open_q.community == top_c]
    topic = top_rows.topic.value_counts().idxmax()
    f12 = Fact("F12", "unanswered_questions", "community", "community", "Unanswered questions",
               f"{top_c} has {top_n} unanswered questions about {topic} this quarter, the most of any community, "
               f"and ProForge hasn't replied to any of them [F12] {cite(top_rows.id, 3)}.",
               top_n / len(open_q), 0.0, confidence_of(d, "S"), list(top_rows.id),
               {"community": top_c, "unanswered_in_community": top_n, "topic": topic,
                "unanswered_everywhere": len(open_q), "proforge_replies": 0})

    comp = this[this.signal_type == "competitor_mention"]
    by_c = comp.community.value_counts()
    cc = by_c.idxmax()
    in_cc = this[this.community == cc]
    rivals = in_cc[in_cc.signal_type == "competitor_mention"]
    own = int((in_cc.brand_mentioned == "ProForge").sum())
    share, prev = len(in_cc) / len(this), (last.community == cc).sum() / len(last)
    change = (share - prev) / prev * 100
    f13 = Fact("F13", "competitor_presence", "community", "community", "Where competitors talk and ProForge doesn't",
               f"On {cc}, {' and '.join(rivals.brand_mentioned.value_counts().index)} were mentioned "
               f"{len(rivals)} times this quarter and ProForge {own} times [F13] {cite(rivals.id, 3)}.",
               share, change, confidence_of(d, "S"), list(rivals.id),
               {"community": cc, "competitor_mentions": len(rivals), "proforge_mentions": own,
                "share_of_conversation": round(share, 4), "share_last_quarter": round(prev, 4),
                "change_pct": round(change, 1)})
    return [f12, f13], table


# --------------------------------------------------------------- playbook
EFFORT_ORDER = {"quick": 0, "medium": 1, "long": 2}


@dataclass
class Play:
    id: str
    name: str
    rule: str
    owner: str
    action: str
    effort: str
    metric: str
    fact_id: str | None = None
    matched: bool = False
    reason: str = ""
    plan_line: str = ""
    do_after: str | None = None
    merge_into: str | None = None   # fact id whose card this play attaches to
    section: str = "retention"      # "retention" or "community"


def run_playbook(f: dict, fatigue: dict) -> list[Play]:
    F6, F8, F9, F10, F11, F12, F13 = (f[k] for k in ("F6", "F8", "F9", "F10", "F11", "F12", "F13"))
    c8, c9, c10, c11, c12, c13 = F8.computation, F9.computation, F10.computation, F11.computation, F12.computation, F13.computation
    plays = [
        Play("PL1", "Fix why they leave", "one complaint is in 25% or more of lapsed buyers' reviews",
             "R&D", "(added to the existing card for that complaint)", "long",
             "repeat rate of lapsed buyers", "F8", c8["top_theme_share"] >= 0.25,
             f"{c8['top_theme']} is in {pct(c8['top_theme_share'])} of lapsed buyers' reviews",
             f"Fix {c8['top_theme']} first: it's in {pct(c8['top_theme_share'])} of the reviews lapsed buyers left, "
             f"so it's added to the R&D {c8['top_theme']} card [F8] [F1].",
             merge_into="F1"),
        Play("PL2", "Reorder reminder", "40% or more of lapsed buyers missed the order due at the usual reorder point",
             "Marketing", f"Send a reorder reminder on day {c10['reminder_day']} by email and WhatsApp.", "quick",
             "on-time reorder rate", "F10", c10["share"] >= 0.40,
             f"{pct(c10['share'])} of lapsed buyers missed the order due around day {c10['median_days_between_orders']}",
             f"Reorder reminder on day {c10['reminder_day']}: {pct(c10['share'])} of lapsed buyers missed the order "
             f"due around day {c10['median_days_between_orders']} [F10]."),
        Play("PL3", "Win back lapsed buyers", "200 or more lapsed buyers", "Marketing",
             "Send lapsed buyers a free sample of the improved bar with a win-back offer.", "quick",
             "% of lapsed buyers who reorder within 30 days", "F8", c8["lapsed_buyers"] >= 200,
             f"{c8['lapsed_buyers']} lapsed buyers",
             f"Win-back sample for the {c8['lapsed_buyers']} lapsed buyers, after the {c8['top_theme']} fix [F8].",
             do_after="PL1 · Fix why they leave"),
        Play("PL4", "Subscribe and save", "subscribers reorder at 2× or more the rate of others, and under 25% subscribe",
             "Strategy", "Decide on a subscribe-and-save price for the 12-bar box.", "medium",
             "subscription share and repeat rate", "F9",
             c9["subscriber_repeat_rate"] >= 2 * c9["non_subscriber_repeat_rate"] and c9["subscriber_share"] < 0.25,
             f"subscribers reorder at {pct(c9['subscriber_repeat_rate'])} vs {pct(c9['non_subscriber_repeat_rate'])}, "
             f"and only {pct(c9['subscriber_share'])} subscribe",
             f"Subscribe and save: subscribers reorder at {pct(c9['subscriber_repeat_rate'])} against "
             f"{pct(c9['non_subscriber_repeat_rate'])}, but only {pct(c9['subscriber_share'])} subscribe [F9]."),
        Play("PL5", "First-month onboarding", "beginners' second-order rate is under 60% of gym regulars'",
             "Marketing", "Send new beginners a 3-message first-month guide on when to eat the bar.", "medium",
             "beginners' second-order rate", "F11",
             c11["beginner_second_order_rate"] < 0.6 * c11["gym_regular_second_order_rate"],
             f"beginners {pct(c11['beginner_second_order_rate'])} vs gym regulars "
             f"{pct(c11['gym_regular_second_order_rate'])}",
             f"First-month guide for beginners: only {pct(c11['beginner_second_order_rate'])} of them order again, "
             f"against {pct(c11['gym_regular_second_order_rate'])} of gym regulars [F11]."),
        Play("PL6", "Damage guarantee", "melted or damaged bars are in 10% or more of 1–2★ reviews", "Strategy",
             "Decide on a free replacement for bars that arrive damaged.", "quick",
             "repeat rate of buyers who complained", "F6", F6.magnitude >= 0.10,
             f"melted or torn in {pct(F6.magnitude)} of 1–2★ reviews",
             f"Free replacement for damaged bars: melted bars or torn wrappers are in {pct(F6.magnitude)} of "
             f"1–2★ reviews [F6]."),
        Play("PL7", "Variety pack", "“bored of the flavour” is in 15% or more of repeat buyers' reviews", "R&D",
             "Design a mixed-flavour starter pack.", "medium", "second-order rate", None,
             fatigue["fatigue_share"] >= 0.15,
             f"flavour fatigue is in {pct(fatigue['fatigue_share'])} of repeat buyers' reviews "
             f"({fatigue['fatigue_reviews']} of {fatigue['repeat_buyer_reviews']})"),
        Play("PL8", "Answer where they ask", "a community has 15 or more unanswered questions this quarter",
             "Marketing",
             f"Reply publicly, as ProForge, to the {c12['unanswered_in_community']} unanswered {c12['topic']} questions "
             f"in {c12['community']}, following the community's self-promotion rules.", "quick",
             f"questions answered and brand sentiment in {c12['community']}", "F12",
             c12["unanswered_in_community"] >= 15,
             f"{c12['unanswered_in_community']} unanswered questions in {c12['community']}",
             f"{c12['community']}: answer the {c12['unanswered_in_community']} open {c12['topic']} questions openly as "
             f"ProForge, and follow the community's rules on brands [F12].",
             section="community"),
        Play("PL9", "Show up where competitors talk",
             "a community has 8 or more competitor mentions and no ProForge mentions", "Marketing",
             f"Post a price-per-gram-of-protein comparison thread on {c13['community']}.", "medium",
             f"ProForge mentions on {c13['community']}", "F13",
             c13["competitor_mentions"] >= 8 and c13["proforge_mentions"] == 0,
             f"{c13['competitor_mentions']} competitor mentions and {c13['proforge_mentions']} for ProForge "
             f"on {c13['community']}",
             f"{c13['community']}: competitors were mentioned {c13['competitor_mentions']} times and ProForge "
             f"{c13['proforge_mentions']} times, so join the comparison threads [F13].",
             section="community"),
    ]
    return plays


# ------------------------------------------------------------- scoring
WEIGHTS = {
    "product":     {"Marketing": 0.9, "Insights": 1.0, "R&D": 1.3, "Strategy": 1.0},
    "packaging":   {"Marketing": 0.9, "Insights": 1.0, "R&D": 1.3, "Strategy": 1.0},
    "pricing":     {"Marketing": 1.0, "Insights": 1.0, "R&D": 0.7, "Strategy": 1.3},
    "positioning": {"Marketing": 1.3, "Insights": 1.1, "R&D": 0.8, "Strategy": 1.1},
    "customer":    {"Marketing": 1.2, "Insights": 1.3, "R&D": 0.9, "Strategy": 1.0},
    "channel":     {"Marketing": 1.2, "Insights": 1.1, "R&D": 0.7, "Strategy": 1.2},
    "retention":   {"Marketing": 1.3, "Insights": 1.0, "R&D": 0.9, "Strategy": 1.2},
    "community":   {"Marketing": 1.3, "Insights": 1.1, "R&D": 0.7, "Strategy": 1.0},
}


def clip(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def base_score(f: Fact) -> float:
    return clip(f.magnitude * (1 + clip(f.change_pct, -100, 100) / 100) * f.confidence)


def relevance(base: float, category: str, role: str) -> float:
    return clip(base * WEIGHTS[category][role])


GATE_RULES = {
    "Customer-facing claim": ["post", "posts", "caption", "campaign", "announce", "announcing", "publish", "claim",
                              "label", "pack copy", "reply publicly"],
    "Budget spend": ["budget", "spend", "fund", "paid", "boost", "sponsor", "influencer", "launch", "listing fee"],
    "Price change": ["discount", "price cut", "reprice", "offer"],
}


def gate(action: str, owner: str) -> str | None:
    """Return the rule an action trips, or None. Strategy is the approver, so its cards are never gated."""
    if owner == "Strategy":
        return None
    for rule, words in GATE_RULES.items():
        if re.search(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", action, re.I):
            return rule
    return None


# ------------------------------------------------------------- checks
CITATION = re.compile(r"\[([A-Z]+\d+)\]")
NUMBER = re.compile(r"[-+]?₹?\d[\d,]*(?:\.\d+)?")


def _numbers_in(obj):
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        yield float(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _numbers_in(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _numbers_in(v)


def check_citations(text: str, known_ids: set) -> tuple[bool, str]:
    ids = CITATION.findall(text)
    if not ids:
        return False, "no citation"
    missing = [i for i in ids if i not in known_ids]
    return (not missing, f"unknown id {missing[0]}" if missing else "")


def check_numbers(text: str, facts: dict, rows: dict) -> tuple[bool, str]:
    """Every number in the sentence must appear in a cited fact or row (after normalising)."""
    allowed = {1.0, 2.0, 3.0, 4.0, 5.0}  # the rating scale, as in "1–2★"
    for cid in CITATION.findall(text):
        if cid in facts:
            allowed |= set(_numbers_in(facts[cid].computation))
        elif cid in rows:
            allowed |= set(_numbers_in([v for v in rows[cid].values() if isinstance(v, (int, float))]))
    body = CITATION.sub("", text)
    for m in NUMBER.findall(body):
        x = abs(float(m.replace("₹", "").replace(",", "")))
        ok = any(abs(x - c) < 1e-6 or abs(x - round(c)) < 1e-6 or abs(x - round(c, 1)) < 1e-6
                 for v in allowed for c in (abs(v), abs(v) * 100))
        if not ok:
            return False, f"{m} isn't in the cited facts or rows"
    return True, ""


# ------------------------------------------------------------- engagement
@dataclass
class Sentence:
    section: str
    fact_id: str | None
    text: str          # what is shown
    template: str      # fallback built from the fact
    status: str = "passed"
    problem: str = ""


@dataclass
class Engagement:
    problem: str
    plan: dict
    facts: dict
    plays: list
    sentences: list
    root_causes: list
    community_table: pd.DataFrame
    gaps: list
    sources: pd.DataFrame
    cards: list = field(default_factory=list)


ROOT_CAUSES = [
    ("Bar texture is driving bad reviews and losing repeat buyers", ["F1", "F8", "F10"]),
    ("Competitors own the low-sugar message and the conversation", ["F3", "F12", "F13"]),
    ("Plant-protein demand is going unmet", ["F4"]),
    ("ProForge is priced above rivals and missing from quick-commerce", ["F2", "F5"]),
]

GAPS = [  # (source the question needs, what we couldn't check)
    ("Offline sales", "No offline or gym-store sales data, so offline performance wasn't checked."),
    ("Returns", "No returns or refunds data, so we can't tell whether texture complaints led to refunds."),
    ("Batch records", "No production batch records, so we can't link the texture complaints to a specific batch."),
]


def confidence_label(prefix_counts: dict) -> str:
    types, rows = len(prefix_counts), sum(prefix_counts.values())
    if types >= 2 and rows >= 30:
        return "High"
    if (types == 1 and rows >= 30) or types >= 2:
        return "Medium"
    return "Low"


def run(problem: str = DEMO_PROBLEM, simulate_llm_mistake: bool = False) -> Engagement:
    d = load()
    facts_list = product_lens(d) + competitor_lens(d) + customer_lens(d) + channel_lens(d)
    retention, fatigue = retention_lens(d)
    community, community_table = community_lens(d)
    facts_list += retention + community
    facts = {f.id: f for f in sorted(facts_list, key=lambda f: int(f.id[1:]))}
    plays = run_playbook(facts, fatigue)

    # Brief sentences. Without an LLM the shown text is the template; the checks still run on it.
    sentences = [Sentence("findings", f.id, f.sentence, f.sentence) for f in facts.values()]
    sentences += [Sentence(p.section, p.fact_id, p.plan_line, p.plan_line) for p in plays if p.matched and p.plan_line]
    if simulate_llm_mistake:  # pretend the LLM rewrote F1 and got a number wrong
        s = sentences[0]
        s.text = s.text.replace(pct(facts["F1"].magnitude), "41%", 1)
    known = set(facts) | set(d.rows)
    for s in sentences:
        ok_c, why_c = check_citations(s.text, known)
        ok_n, why_n = check_numbers(s.text, facts, d.rows)
        if not (ok_c and ok_n):
            s.status, s.problem, s.text = "fell_back", why_c or why_n, s.template

    root_causes = []
    for title, fids in ROOT_CAUSES:
        ids = [i for fid in fids for i in facts[fid].evidence_ids]
        counts = pd.Series([i[0] for i in ids]).value_counts().to_dict()
        root_causes.append({"cause": title, "facts": fids, "confidence": confidence_label(counts),
                            "rows": len(ids), "sources": counts})
    order = {"High": 0, "Medium": 1, "Low": 2}
    root_causes.sort(key=lambda r: (order[r["confidence"]], -r["rows"]))

    have = set(d.sources.name)
    gaps = [msg for needed, msg in GAPS if needed not in have]
    plan = {"lenses": LENSES, "products": ["bar"],
            "questions": ["Where is the bar failing?", "What are competitors doing on price, claims and packaging?",
                          "Which customers are we missing?", "Which channels are we losing?",
                          "Why do buyers stop coming back?", "Where are buyers talking, and are we there?"]}

    cited = {i for s in sentences for i in CITATION.findall(s.text)}
    cited |= {i for fid in cited if fid in facts for i in facts[fid].evidence_ids}
    src = d.sources.copy()
    src["rows_loaded"] = [len(getattr(d, n)) for n in ("reviews", "social", "competitors", "sales", "orders")]
    src["rows_cited"] = [sum(1 for i in cited if i[0] == p and i in d.rows) for p in src.prefix]

    e = Engagement(problem, plan, facts, plays, sentences, root_causes, community_table, gaps, src)
    e.cards = initial_cards(e)
    return e


# ------------------------------------------------------------- actions
FACT_ACTIONS = {  # fact → role → action, worded for that team's job
    "F1": {"R&D": "Test a softer bar base to cut chalky-texture complaints.",
           "Marketing": "Draft a post announcing the softer recipe once R&D signs it off.",
           "Insights": "Validate the texture spike by reading this quarter's 1–2★ reviews by channel.",
           "Strategy": "Decide whether to fund a recipe change for the bar."},
    "F2": {"Strategy": "Decide whether to close the price-per-gram gap with CleanBar Co.",
           "Marketing": "Draft pack copy that shows value per gram of protein.",
           "Insights": "Check whether marketplace buyers who left compared prices with CleanBar Co.",
           "R&D": "Cost out a bar with the same protein at a lower ingredient cost."},
    "F3": {"Marketing": "Draft Instagram posts that lead with ProForge's 2 g of sugar per bar.",
           "Insights": "Validate how often buyers ask about sugar before they buy.",
           "R&D": "Confirm whether the bar qualifies for a “no added sugar” label claim.",
           "Strategy": "Decide whether to reposition the bar around low sugar."},
    "F4": {"Insights": "Validate the plant-protein request spike against vegetarian buyers' reviews.",
           "R&D": "Scope a plant-protein bar prototype.",
           "Marketing": "Ask followers in a story poll which plant-protein flavour they want.",
           "Strategy": "Decide whether to fund a plant-protein bar."},
    "F5": {"Marketing": "Pitch ProForge to the quick-commerce apps where competitors already sell.",
           "Strategy": "Decide whether ProForge should be on quick-commerce apps.",
           "Insights": "Find out whether marketplace buyers moved to quick-commerce apps.",
           "R&D": "Check the bar's shelf life under quick-commerce storage conditions."},
    "F6": {"R&D": "Trial a foil-lined wrapper that survives heat in transit.",
           "Strategy": "Decide whether to use heat-safe shipping in summer months.",
           "Marketing": "Add a storage-care note to the order confirmation email.",
           "Insights": "Check which cities and months the melted-bar complaints come from."},
}
PLAY_ACTIONS = {  # category → role → action for a play card handed to a team that doesn't own the play
    "retention": {"Marketing": "Plan the customer messages for: {name}.",
                  "Insights": "Check the data behind “{name}” before it goes out.",
                  "R&D": "Check what product change “{name}” depends on.",
                  "Strategy": "Decide whether to go ahead with “{name}”."},
    "community": {"Marketing": "{action}",
                  "Insights": "Read the {community} threads and list the top questions.",
                  "R&D": "Prepare the product facts needed to answer questions in {community}.",
                  "Strategy": "Decide who may speak for ProForge in {community}."},
}


def action_for(card: dict, role: str, e: Engagement) -> str:
    if card["play_id"]:
        play = next(p for p in e.plays if p.id == card["play_id"])
        if role == play.owner:
            return play.action
        fact = e.facts.get(play.fact_id)
        return PLAY_ACTIONS[play.section][role].format(
            name=play.name, action=play.action, community=(fact.computation.get("community", "") if fact else ""))
    return FACT_ACTIONS[card["fact_id"]][role]


def initial_cards(e: Engagement) -> list[dict]:
    cards = []
    for f in e.facts.values():
        if not f.default_owner:
            continue
        base = base_score(f)
        action = FACT_ACTIONS[f.id][f.default_owner]
        cards.append({"fact_id": f.id, "play_id": None, "category": f.category, "owner_role": f.default_owner,
                      "base": base, "relevance_score": relevance(base, f.category, f.default_owner),
                      "evidence": f.sentence, "suggested_action": action,
                      "gate_rule": gate(action, f.default_owner), "note": ""})
    for p in e.plays:
        if not p.matched:
            continue
        if p.merge_into:
            card = next(c for c in cards if c["fact_id"] == p.merge_into)
            card["note"] = (f"Retention ({p.id} · {p.name}): {p.reason} [{p.fact_id}]. "
                            f"Metric to watch: {p.metric}.")
            continue
        f = e.facts[p.fact_id]
        base = base_score(f)
        cat = p.section
        cards.append({"fact_id": f.id, "play_id": p.id, "category": cat, "owner_role": p.owner,
                      "base": base, "relevance_score": relevance(base, cat, p.owner),
                      "evidence": f"{p.name}: {p.reason} [{p.fact_id}].", "suggested_action": p.action,
                      "gate_rule": gate(p.action, p.owner),
                      "note": (f"Do after: {p.do_after}. " if p.do_after else "")
                              + f"Effort: {p.effort}. Metric to watch: {p.metric}."})
    return cards


if __name__ == "__main__":
    e = run()
    for f in e.facts.values():
        print(f"{f.id:4} base={base_score(f):.3f}  {f.sentence}")
    print()
    for p in e.plays:
        print(f"{p.id} {'✓' if p.matched else '✗'} {p.name}: {p.reason}")
    print()
    for c in sorted(e.cards, key=lambda c: (c["owner_role"], -c["relevance_score"])):
        print(f"{c['owner_role']:9} {c['relevance_score']:.3f} {c['play_id'] or c['fact_id']:4} "
              f"{'[gated: ' + c['gate_rule'] + '] ' if c['gate_rule'] else ''}{c['suggested_action']}")
    print()
    print("checks:", sum(s.status == "passed" for s in e.sentences), "of", len(e.sentences), "passed")
    for s in e.sentences:
        if s.status != "passed":
            print("  FELL BACK", s.fact_id, s.problem)
    for r in e.root_causes:
        print(r["confidence"], r["rows"], r["cause"])
    print(e.sources[["name", "rows_loaded", "rows_cited"]].to_string(index=False))
