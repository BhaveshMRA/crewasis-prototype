"""Generate the synthetic ProForge demo data.

Everything here is made up: ProForge, CleanBar Co, MuscleMint and WheyWise are fictional brands.
The generator is seeded, so every run writes the same files, and the counts below are fixed so
the engine's findings match the README (34% texture complaints, 20% price gap, 400 lapsed buyers…).

Run:  python data/generate.py
"""
import csv
import random
from datetime import date, timedelta
from pathlib import Path

random.seed(7)
HERE = Path(__file__).parent
AS_OF = date(2026, 9, 24)
Q2 = (date(2026, 4, 1), date(2026, 6, 30))
Q3 = (date(2026, 7, 1), date(2026, 9, 24))


def rand_date(start, end):
    return start + timedelta(days=random.randint(0, (end - start).days))


def write(name, rows):
    with open(HERE / name, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------- orders.csv
# segment: (customers, repeaters, subscribers, subscribers who repeat)
SEGMENTS = {
    "gym_regular": (1200, 420, 160, 114),
    "beginner": (1000, 180, 60, 43),
    "weight_loss": (800, 240, 80, 57),
    "vegetarian": (600, 168, 60, 43),
    "endurance": (400, 140, 40, 27),
}
N_LAPSED_REGULAR = 232   # were on the usual reorder cycle, then stopped
N_LAPSED_IRREGULAR = 168  # ordered irregularly, then stopped


def schedule(n_orders, gap_range, last_days_ago):
    last = AS_OF - timedelta(days=random.randint(*last_days_ago))
    dates = [last]
    for _ in range(n_orders - 1):
        dates.append(dates[-1] - timedelta(days=random.randint(*gap_range)))
    return sorted(dates)


customers = []  # (customer_id, segment, subscription, kind)
for seg, (n, rep, subs, sub_rep) in SEGMENTS.items():
    customers += [(seg, "yes", "sub_repeat")] * sub_rep
    customers += [(seg, "yes", "sub_once")] * (subs - sub_rep)
    customers += [(seg, "no", "repeat")] * (rep - sub_rep)
    customers += [(seg, "no", "once")] * (n - subs - (rep - sub_rep))
random.shuffle(customers)
customers = [(f"C{i:04d}", seg, sub, kind) for i, (seg, sub, kind) in enumerate(customers, 1)]

repeat_ids = [c for c in customers if c[3] == "repeat"]
random.shuffle(repeat_ids)
lapsed_regular = {c[0] for c in repeat_ids[:N_LAPSED_REGULAR]}
lapsed_irregular = {c[0] for c in repeat_ids[N_LAPSED_REGULAR:N_LAPSED_REGULAR + N_LAPSED_IRREGULAR]}
active_repeat = {c[0] for c in repeat_ids[N_LAPSED_REGULAR + N_LAPSED_IRREGULAR:]}

orders = []
for cid, seg, sub, kind in customers:
    if cid in lapsed_regular:
        dates = schedule(random.randint(2, 4), (20, 28), (61, 150))
    elif cid in lapsed_irregular:
        dates = schedule(random.randint(2, 3), (35, 60), (61, 150))
    elif cid in active_repeat:
        dates = schedule(random.randint(2, 6), (18, 28), (0, 40))
    elif kind == "sub_repeat":
        dates = schedule(random.randint(3, 8), (22, 26), (0, 25))
    elif kind == "sub_once":
        dates = schedule(1, (0, 0), (0, 25))
    else:
        dates = schedule(1, (0, 0), (0, 358))
    channel = "d2c" if sub == "yes" else random.choices(["marketplace", "d2c"], [0.6, 0.4])[0]
    for d in dates:
        orders.append({"customer_id": cid, "order_date": d.isoformat(), "product": "bar",
                       "pack_size": "12-bar box", "channel": channel, "segment": seg,
                       "subscription": sub})
orders.sort(key=lambda o: (o["order_date"], o["customer_id"]))
orders = [{"id": f"O{i:05d}", **o} for i, o in enumerate(orders, 1)]
write("orders.csv", orders)

# --------------------------------------------------------------- reviews.csv
TEXTURE = [
    "Tastes fine but so chalky, hard to finish.", "Way too dry this batch, needed water with every bite.",
    "Gritty texture, feels like chewing sand.", "Chalky aftertaste, not like it used to be.",
    "The new batch is dry and crumbly.", "Too chalky, I stopped buying.",
    "Texture got worse, very dry now.", "Gritty and dry, would not reorder.",
    "Chalky. Really chalky.", "Used to love it, now it is dry and gritty.",
    "Chalky texture ruins an otherwise decent bar.", "Dry as cardboard.",
]
PACKAGING = [
    "Arrived melted, the wrapper was stuck to the bar.", "Wrapper tore open in the box.",
    "Bars melted in transit.", "Half the box arrived melted.", "Wrapper torn, bar exposed.",
]
PRICE = [
    "Too expensive for what you get.", "Overpriced compared to other bars.", "Pricey for a snack bar.",
    "Found a cheaper bar with the same protein.", "The price keeps going up.",
]
TASTE = ["Too sweet for me.", "Did not like the flavour.", "Chocolate taste is artificial.",
         "Weird aftertaste of sweetener."]
DIGESTION = ["Made me bloated.", "Upset my stomach.", "Gave me gas and bloating.",
             "My stomach did not agree with it."]
DELIVERY = ["Delivery was late by a week.", "Late delivery, had to chase support."]
OTHER = ["Not what I expected.", "Meh.", "Would not buy again."]
PRAISE = [
    "Great after a workout.", "Good protein, keeps me full till lunch.", "My go-to snack at the office.",
    "Solid bar, would recommend.", "Love the chocolate one.", "Easy to carry in my gym bag.",
    "Good value when it is on sale.", "Kids steal these from my bag.",
]
FATIGUE = ["Good bar but I am bored of the same flavour every week.",
           "Okay, but bored of the flavour now.", "Same flavour every time, getting bored of it."]

# (theme, count) per quarter for 1–2★ reviews
LOW = {
    "Q2": [("texture", 9), ("packaging", 3), ("price", 10), ("taste", 8), ("digestion", 5), ("delivery", 3), ("other", 2)],
    "Q3": [("texture", 17), ("packaging", 6), ("price", 8), ("taste", 7), ("digestion", 5), ("delivery", 4), ("other", 3)],
}
TEXTS = {"texture": TEXTURE, "packaging": PACKAGING, "price": PRICE, "taste": TASTE,
         "digestion": DIGESTION, "delivery": DELIVERY, "other": OTHER}
OTHER_SEGMENTS = ["gym_regular", "beginner", "weight_loss", "endurance"]

reviews = []
for q, (start, end) in (("Q2", Q2), ("Q3", Q3)):
    for theme, n in LOW[q]:
        for i in range(n):
            seg = "vegetarian" if theme == "digestion" or (theme in ("taste", "price") and i % 3 == 0) \
                else random.choice(OTHER_SEGMENTS)
            reviews.append({"theme": theme, "date": rand_date(start, end), "rating": random.choice([1, 2]),
                            "text": TEXTS[theme][i % len(TEXTS[theme])], "segment": seg})
    fatigue_here = 3 if q == "Q3" else 0
    for i in range(30):
        if i < fatigue_here:
            text, rating = FATIGUE[i], 3
        else:
            text, rating = PRAISE[i % len(PRAISE)], random.choice([4, 5, 5])
        reviews.append({"theme": "fatigue" if i < fatigue_here else "praise", "date": rand_date(start, end),
                        "rating": rating, "text": text, "segment": random.choice(OTHER_SEGMENTS)})

# Link reviews to customers: 44 reviews from lapsed buyers (18 about texture, 3 about flavour
# fatigue), 6 from active repeat buyers, the rest from one-time buyers or unverified.
by_id = {c[0]: c for c in customers}
lapsed_pool = sorted(lapsed_regular | lapsed_irregular)
random.shuffle(lapsed_pool)
active_pool = sorted(active_repeat)
random.shuffle(active_pool)
once_pool = [c[0] for c in customers if c[3] == "once"]
random.shuffle(once_pool)


def take(pool, segment):
    for i, cid in enumerate(pool):
        if by_id[cid][1] == segment:
            return pool.pop(i)
    raise ValueError(segment)


idx = list(range(len(reviews)))
texture_idx = [i for i in idx if reviews[i]["theme"] == "texture"]
fatigue_idx = [i for i in idx if reviews[i]["theme"] == "fatigue"]
other_idx = [i for i in idx if reviews[i]["theme"] not in ("texture", "fatigue")]
random.shuffle(texture_idx)
random.shuffle(other_idx)
links = {}
for i in texture_idx[:18] + fatigue_idx + other_idx[:23]:
    links[i] = take(lapsed_pool, reviews[i]["segment"])
for i in other_idx[23:29]:
    links[i] = take(active_pool, reviews[i]["segment"])
for i in other_idx[29:] + texture_idx[18:]:
    if random.random() < 0.5:
        links[i] = take(once_pool, reviews[i]["segment"])

rows = []
for n, i in enumerate(sorted(idx, key=lambda i: (reviews[i]["date"], i)), 1):
    r = reviews[i]
    rows.append({"id": f"R{n:03d}", "date": r["date"].isoformat(), "product": "bar",
                 "channel": random.choice(["marketplace", "marketplace", "d2c"]), "rating": r["rating"],
                 "text": r["text"], "reviewer_segment": r["segment"], "customer_id": links.get(i, "")})
write("reviews.csv", rows)

# -------------------------------------------------------- social_signals.csv
IG = "Instagram @proforge comments"
SOCIAL = {  # quarter: [(count, platform, community, signal_type, topic, brand, answered, texts)]
    "Q2": [
        (6, "instagram", IG, "question", "sugar", "ProForge", "yes", ["How much sugar is in the bar?"]),
        (4, "reddit", "r/IndianFitness", "question", "sugar", "ProForge", "no", ["Anyone know the sugar content of ProForge bars?"]),
        (3, "instagram", IG, "question", "availability", "ProForge", "yes", ["Where can I buy these offline?"]),
        (3, "instagram", IG, "question", "protein", "ProForge", "yes", ["Is it 20 g protein per bar?"]),
        (7, "instagram", IG, "request", "plant_protein", "ProForge", "no", ["Please make a vegan version!"]),
        (5, "reddit", "r/veganfitness", "request", "plant_protein", "none", "no", ["Wish there was a decent plant-protein bar in India."]),
        (5, "instagram", IG, "request", "bigger_pack", "ProForge", "yes", ["Bring back the 24-bar box."]),
        (3, "instagram", IG, "request", "new_flavour", "ProForge", "yes", ["Peanut butter flavour when?"]),
        (4, "instagram", IG, "complaint", "texture", "ProForge", "no", ["Why is the new batch so chalky?"]),
        (2, "x", "X #proteinbar", "competitor_mention", "comparison", "WheyWise", "no", ["WheyWise vs the rest: taste test thread"]),
        (2, "x", "X #proteinbar", "competitor_mention", "comparison", "CleanBar Co", "no", ["CleanBar Co is my pick for no added sugar"]),
        (6, "instagram", IG, "praise", "general", "ProForge", "yes", ["Best bar for my morning commute!"]),
    ],
    "Q3": [
        (9, "instagram", IG, "question", "sugar", "ProForge", "mixed", ["How much sugar is in the bar?", "Is this bar sugar free?"]),
        (22, "reddit", "r/IndianFitness", "question", "sugar", "ProForge", "no",
         ["Anyone know the sugar content of ProForge bars?", "ProForge vs CleanBar Co for sugar?",
          "Is ProForge actually low sugar or just marketing?"]),
        (5, "instagram", IG, "question", "availability", "ProForge", "yes", ["Is it on quick-commerce apps yet?"]),
        (4, "instagram", IG, "question", "protein", "ProForge", "yes", ["Is it 20 g protein per bar?"]),
        (8, "instagram", IG, "request", "plant_protein", "ProForge", "mixed", ["Please make a vegan version!"]),
        (14, "reddit", "r/veganfitness", "request", "plant_protein", "none", "no",
         ["Wish there was a decent plant-protein bar in India.", "Any brand doing a pea-protein bar?"]),
        (4, "instagram", IG, "request", "bigger_pack", "ProForge", "yes", ["Bring back the 24-bar box."]),
        (3, "instagram", IG, "request", "new_flavour", "ProForge", "yes", ["Peanut butter flavour when?"]),
        (6, "reddit", "r/gainit", "complaint", "texture", "ProForge", "no", ["ProForge bars got chalky, anyone else?"]),
        (4, "instagram", IG, "complaint", "texture", "ProForge", "no", ["Why is the new batch so chalky?"]),
        (6, "x", "X #proteinbar", "competitor_mention", "comparison", "WheyWise", "no", ["WheyWise vs the rest: taste test thread"]),
        (4, "x", "X #proteinbar", "competitor_mention", "comparison", "CleanBar Co", "no", ["CleanBar Co is my pick for no added sugar"]),
        (7, "instagram", IG, "praise", "general", "ProForge", "yes", ["Best bar for my morning commute!"]),
    ],
}
social = []
for q, (start, end) in (("Q2", Q2), ("Q3", Q3)):
    for count, platform, community, stype, topic, brand, answered, texts in SOCIAL[q]:
        for i in range(count):
            ans = answered if answered != "mixed" else ("yes" if i % 2 == 0 else "no")
            social.append({"date": rand_date(start, end).isoformat(), "platform": platform, "community": community,
                           "signal_type": stype, "topic": topic, "brand_mentioned": brand,
                           "answered_by_brand": ans, "text": texts[i % len(texts)]})
social.sort(key=lambda s: (s["date"], s["community"], s["text"]))
write("social_signals.csv", [{"id": f"S{i:03d}", **s} for i, s in enumerate(social, 1)])

# ---------------------------------------------------------- competitors.csv
write("competitors.csv", [
    {"id": "P01", "brand": "ProForge", "product": "ProForge Bar", "format": "bar", "price_inr": 120, "servings": 1,
     "protein_g_per_serving": 20, "sugar_g_per_serving": 2, "claims": "high protein",
     "packaging": "plastic wrapper", "channels": "marketplace; d2c", "date_checked": "2026-09-20"},
    {"id": "P02", "brand": "ProForge", "product": "ProForge Whey 1 kg", "format": "whey", "price_inr": 2400, "servings": 33,
     "protein_g_per_serving": 24, "sugar_g_per_serving": 1, "claims": "high protein",
     "packaging": "tub", "channels": "marketplace; d2c", "date_checked": "2026-09-20"},
    {"id": "P03", "brand": "CleanBar Co", "product": "CleanBar Whey 1 kg", "format": "whey", "price_inr": 2200, "servings": 33,
     "protein_g_per_serving": 24, "sugar_g_per_serving": 0, "claims": "no added sugar; clean label",
     "packaging": "resealable pouch", "channels": "marketplace; d2c; quick_commerce", "date_checked": "2026-09-20"},
    {"id": "P04", "brand": "CleanBar Co", "product": "CleanBar Protein Bar", "format": "bar", "price_inr": 100, "servings": 1,
     "protein_g_per_serving": 20, "sugar_g_per_serving": 1, "claims": "no added sugar; clean label",
     "packaging": "foil-lined wrapper", "channels": "marketplace; d2c; quick_commerce", "date_checked": "2026-09-20"},
    {"id": "P05", "brand": "MuscleMint", "product": "MuscleMint Bar", "format": "bar", "price_inr": 110, "servings": 1,
     "protein_g_per_serving": 20, "sugar_g_per_serving": 1, "claims": "no added sugar; high protein",
     "packaging": "foil-lined wrapper", "channels": "marketplace; quick_commerce", "date_checked": "2026-09-20"},
    {"id": "P06", "brand": "MuscleMint", "product": "MuscleMint Whey 1 kg", "format": "whey", "price_inr": 2300, "servings": 33,
     "protein_g_per_serving": 25, "sugar_g_per_serving": 1, "claims": "high protein",
     "packaging": "tub", "channels": "marketplace; quick_commerce", "date_checked": "2026-09-20"},
    {"id": "P07", "brand": "WheyWise", "product": "WheyWise Bar", "format": "bar", "price_inr": 130, "servings": 1,
     "protein_g_per_serving": 22, "sugar_g_per_serving": 3, "claims": "high protein; gut friendly",
     "packaging": "plastic wrapper", "channels": "marketplace; d2c", "date_checked": "2026-09-20"},
    {"id": "P08", "brand": "WheyWise", "product": "WheyWise Whey 1 kg", "format": "whey", "price_inr": 2600, "servings": 33,
     "protein_g_per_serving": 25, "sugar_g_per_serving": 2, "claims": "gut friendly",
     "packaging": "tub", "channels": "marketplace; d2c", "date_checked": "2026-09-20"},
])

# ---------------------------------------------------------------- sales.csv
# Q2 and Q3 are exact: bar units −20% overall, marketplace −28%; repeat purchase 38% → 29%.
SALES = {  # month: {channel: (units, customers, repeat_customers)}
    "2025-10": {"marketplace": (1600, 640, 220), "d2c": (950, 320, 140)},
    "2025-11": {"marketplace": (1650, 650, 225), "d2c": (960, 320, 141)},
    "2025-12": {"marketplace": (1750, 690, 240), "d2c": (990, 330, 145)},
    "2026-01": {"marketplace": (1700, 670, 234), "d2c": (1000, 333, 146)},
    "2026-02": {"marketplace": (1680, 665, 232), "d2c": (990, 330, 145)},
    "2026-03": {"marketplace": (1720, 680, 238), "d2c": (1010, 336, 148)},
    "2026-04": {"marketplace": (1700, 680, 238), "d2c": (1000, 334, 147)},
    "2026-05": {"marketplace": (1650, 660, 231), "d2c": (1000, 333, 147)},
    "2026-06": {"marketplace": (1650, 660, 231), "d2c": (1000, 333, 146)},
    "2026-07": {"marketplace": (1300, 560, 128), "d2c": (950, 340, 140)},
    "2026-08": {"marketplace": (1200, 540, 118), "d2c": (930, 330, 135)},
    "2026-09": {"marketplace": (1100, 500, 106), "d2c": (920, 330, 135)},
}
sales, n = [], 0
for month, chans in SALES.items():
    for channel, (units, cust, rep) in chans.items():
        n += 1
        sales.append({"id": f"M{n:02d}", "month": month, "product": "bar", "channel": channel,
                      "units": units, "customers": cust, "repeat_customers": rep})
write("sales.csv", sales)

# -------------------------------------------------------------- sources.csv
write("sources.csv", [
    {"prefix": "R", "name": "Reviews", "file": "reviews.csv", "confidence": 0.90, "synthetic": "yes",
     "collected_at": "2026-09-24", "description": "Marketplace and website reviews of the ProForge bar"},
    {"prefix": "S", "name": "Social signals", "file": "social_signals.csv", "confidence": 0.70, "synthetic": "yes",
     "collected_at": "2026-09-24", "description": "Instagram comments, Reddit posts and X posts about protein bars"},
    {"prefix": "P", "name": "Competitor catalogue", "file": "competitors.csv", "confidence": 0.95, "synthetic": "yes",
     "collected_at": "2026-09-20", "description": "Prices, nutrition, claims, packaging and channels of 4 brands"},
    {"prefix": "M", "name": "Sales", "file": "sales.csv", "confidence": 1.00, "synthetic": "yes",
     "collected_at": "2026-09-24", "description": "Monthly ProForge bar sales by channel"},
    {"prefix": "O", "name": "Orders", "file": "orders.csv", "confidence": 1.00, "synthetic": "yes",
     "collected_at": "2026-09-24", "description": "12 months of online orders by anonymised customer id"},
])
print(f"orders {len(orders)} · reviews {len(rows)} · social {len(social)} · sales {len(sales)}")
