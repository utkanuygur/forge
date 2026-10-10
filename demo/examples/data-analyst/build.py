"""Build the data-analyst example: seed CSVs, agent.json, evals.json and prompt_checks.json.

    uv run python demo/examples/data-analyst/build.py [--out DIR]

Generated from a fixed random seed, so every run writes the same bytes. The data is a small
online store from January to September 2026: customers, products and order lines. Planted:

  * Asia Pacific revenue jumps in August 2026 (a regional campaign), the clear top month over
    month growth in Q3; every other region and month moves much less.
  * 14 customers signed up more than once with the same email in different case or with stray
    whitespace (three of them three times). Two near misses are NOT duplicates under the stated rule:
    a Gmail address with and without a dot, and two different people with the same name.
  * Electronics has a much higher refund rate in Q3 than any other category.
  * Retention decays with months since signup, so cohort retention has a realistic shape.

Ground truth is computed from the written rows, and every checker recomputes it with the stdlib.
"""

from __future__ import annotations

import argparse
import csv
import io
import random
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from common import case, py_checker, write_json, write_text  # noqa: E402

REGIONS = ["North America", "Europe", "Asia Pacific", "Latin America"]
REGION_WEIGHTS = [38, 30, 20, 12]
CATEGORIES = {
    "Electronics": [("USB-C Hub 7-in-1", 49.99), ("Noise Cancelling Headphones", 189.00), ("Mechanical Keyboard", 119.50),
                    ("1080p Webcam", 64.99), ("Portable SSD 1TB", 99.00), ("Wireless Mouse", 29.99)],
    "Home": [("Ceramic Pour-Over Set", 38.00), ("Linen Duvet Cover", 129.00), ("Cast Iron Skillet", 44.50),
             ("Desk Lamp", 54.00), ("Glass Storage Set", 32.99)],
    "Apparel": [("Merino Wool Socks", 18.00), ("Rain Shell Jacket", 149.00), ("Organic Cotton Tee", 24.00),
                ("Everyday Sneakers", 95.00), ("Fleece Pullover", 72.00)],
    "Outdoors": [("Trail Backpack 28L", 109.00), ("Insulated Bottle", 34.00), ("Camp Stove", 79.99),
                 ("Headlamp", 39.95)],
    "Beauty": [("Vitamin C Serum", 42.00), ("Mineral Sunscreen", 26.50), ("Hair Repair Mask", 31.00),
               ("Lip Balm Trio", 12.00)],
}
REFUND_P = {"Electronics": 0.05, "Home": 0.035, "Apparel": 0.06, "Outdoors": 0.03, "Beauty": 0.025}
Q3_ELECTRONICS_REFUND = 0.17
FIRST = ["Ava", "Liam", "Noah", "Emma", "Mia", "Lucas", "Sofia", "Mateo", "Yuki", "Hiro", "Priya", "Arjun", "Chloe",
         "Leon", "Elif", "Omar", "Zara", "Ivan", "Lena", "Diego", "Mei", "Kenji", "Amara", "Felix", "Nora", "Tariq",
         "Ines", "Jonas", "Aisha", "Marco", "Sara", "Theo", "Hana", "Ravi", "Clara", "Joao"]
LAST = ["Garcia", "Smith", "Muller", "Rossi", "Tanaka", "Kim", "Patel", "Silva", "Nguyen", "Brown", "Dubois", "Novak",
        "Yilmaz", "Haddad", "Okafor", "Larsen", "Costa", "Sato", "Chen", "Lopez", "Fischer", "Kowalski", "Singh", "Ali"]
DOMAINS = ["gmail.com", "outlook.com", "yahoo.com", "proton.me", "icloud.com", "fastmail.com"]
MONTHS = [f"2026-{m:02d}" for m in range(1, 10)]


def month_add(month: str, k: int) -> str:
    y, m = map(int, month.split("-"))
    m += k
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return f"{y}-{m:02d}"


def rand_day(rng: random.Random, month: str) -> date:
    y, m = map(int, month.split("-"))
    start = date(y, m, 1)
    end = date(y + (m == 12), m % 12 + 1, 1)
    return start + timedelta(days=rng.randrange((end - start).days))


def to_csv(header: list[str], rows: list[list]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue()


def generate(seed: int = 20260930) -> dict:
    rng = random.Random(seed)
    products = []
    pid = 100
    for cat, items in CATEGORIES.items():
        for name, price in items:
            pid += 1
            products.append({"product_id": f"P{pid}", "name": name, "category": cat, "unit_price": price})

    customers: list[dict] = []
    used_emails: set[str] = set()

    def new_customer(name: str, email: str, region: str, signup: date) -> dict:
        c = {"customer_id": f"C{len(customers) + 1001}", "name": name, "email": email, "region": region,
             "signup_date": signup.isoformat()}
        customers.append(c)
        return c

    signup_months = [month_add("2025-10", k) for k in range(12)]  # 2025-10 .. 2026-09
    signup_weights = [40, 45, 55, 70, 75, 80, 85, 80, 75, 70, 65, 50]
    for _ in range(1500):
        first, last = rng.choice(FIRST), rng.choice(LAST)
        while True:
            email = f"{first}.{last}{rng.randrange(1, 999)}@{rng.choice(DOMAINS)}".lower()
            if email not in used_emails:
                break
        used_emails.add(email)
        month = rng.choices(signup_months, signup_weights)[0]
        signup = rand_day(rng, month)
        if month == "2026-09":
            signup = min(signup, date(2026, 9, 20))
        new_customer(f"{first} {last}", email, rng.choices(REGIONS, REGION_WEIGHTS)[0], signup)

    # Planted duplicates: the same person signs up again with the email in a different case or padded.
    originals = rng.sample(customers[:1300], 14)
    variants = [lambda e: e.upper(), lambda e: " " + e, lambda e: e + " ", lambda e: e.title(),
                lambda e: e[0].upper() + e[1:], lambda e: "\t" + e.capitalize()]
    for i, orig in enumerate(originals):
        copies = 2 if i < 3 else 1
        for _ in range(copies):
            later = date.fromisoformat(orig["signup_date"]) + timedelta(days=rng.randrange(20, 140))
            later = min(later, date(2026, 9, 20))
            new_customer(orig["name"], rng.choice(variants)(orig["email"]), orig["region"], later)
    # Near misses that are not duplicates under "trim and lowercase".
    a = new_customer("Dana Whitfield", "dana.whitfield@gmail.com", "Europe", date(2026, 2, 3))
    new_customer("Dana Whitfield", "danawhitfield@gmail.com", "Europe", date(2026, 5, 19))
    new_customer("Alex Kim", "alex.kim@outlook.com", "North America", date(2026, 1, 12))
    new_customer("Alex Kim", "akim.design@proton.me", "Asia Pacific", date(2026, 3, 30))
    assert a  # keep linters quiet about the unused binding

    rng.shuffle(customers)  # file order is not signup order
    customers.sort(key=lambda c: c["customer_id"])  # but ids stay sorted, as an export would be

    # ------------------------------------------------------------ orders
    # Each region gets a planned number of orders per month (steady growth, plus the planted campaign);
    # who places them is drawn by recency of signup, so retention decays naturally.
    orders = []
    oid = 50000
    k_weight = [1.0, 0.62, 0.52, 0.47, 0.44, 0.41, 0.39, 0.38, 0.37, 0.36, 0.35, 0.35]
    base = {"North America": 430, "Europe": 330, "Asia Pacific": 210, "Latin America": 130}
    curve = dict(zip(MONTHS, [0.55, 0.62, 0.70, 0.76, 0.82, 0.88, 0.92, 0.96, 1.0]))
    for month in MONTHS:
        month_end = rand_day(rng, month).replace(day=28)
        for region in REGIONS:
            n = round(base[region] * curve[month] * rng.uniform(0.97, 1.03))
            if region == "Asia Pacific" and month == "2026-08":
                n = round(n * 1.42)  # regional launch campaign
            pool = [c for c in customers if c["region"] == region and c["signup_date"] <= month_end.isoformat()]
            weights = []
            for c in pool:
                sm = c["signup_date"][:7]
                k = (int(month[:4]) - int(sm[:4])) * 12 + int(month[5:]) - int(sm[5:])
                weights.append(k_weight[min(k, len(k_weight) - 1)])
            for c in rng.choices(pool, weights, k=n):
                day = rand_day(rng, month)
                if day < date.fromisoformat(c["signup_date"]):
                    day = date.fromisoformat(c["signup_date"])
                for _line in range(1 + (rng.random() < 0.35)):
                    prod = rng.choice(products)
                    qty = rng.choices([1, 2, 3, 4], [70, 20, 7, 3])[0]
                    price = prod["unit_price"]
                    if rng.random() < 0.12:
                        price = round(price * 0.85, 2)  # promo price
                    r = rng.random()
                    refund_p = REFUND_P[prod["category"]]
                    if prod["category"] == "Electronics" and month >= "2026-07":
                        refund_p = Q3_ELECTRONICS_REFUND
                    status = "cancelled" if r < 0.03 else "refunded" if r < 0.03 + refund_p else "completed"
                    oid += 1
                    orders.append({"order_id": f"O{oid}", "customer_id": c["customer_id"],
                                   "order_date": day.isoformat(), "product_id": prod["product_id"], "quantity": qty,
                                   "unit_price": f"{price:.2f}", "status": status})
    orders.sort(key=lambda o: (o["order_date"], o["order_id"]))
    return {"customers": customers, "products": products, "orders": orders}


# ---------------------------------------------------------------- ground truth (cents, no floats)


def cents(price: str) -> int:
    whole, frac = price.split(".")
    return int(whole) * 100 + int(frac)


def truth_of(data: dict) -> dict:
    cust = {c["customer_id"]: c for c in data["customers"]}
    prod = {p["product_id"]: p for p in data["products"]}
    rev: dict[tuple[str, str], int] = defaultdict(int)
    for o in data["orders"]:
        if o["status"] == "completed":
            rev[(cust[o["customer_id"]]["region"], o["order_date"][:7])] += o["quantity"] * cents(o["unit_price"])
    growth = {}
    for region in REGIONS:
        for m in ("2026-07", "2026-08", "2026-09"):
            prev = rev[(region, month_add(m, -1))]
            growth[(region, m)] = (rev[(region, m)] - prev) / prev * 100
    best = max(growth, key=growth.get)  # type: ignore[arg-type]
    runner_up = sorted(growth.values())[-2]
    assert best == ("Asia Pacific", "2026-08") and growth[best] - runner_up > 15, (growth, best)
    revenue = {r: {m: rev[(r, m)] / 100 for m in ("2026-06", "2026-07", "2026-08", "2026-09")} for r in REGIONS}

    groups: dict[str, list[str]] = defaultdict(list)
    for c in data["customers"]:
        groups[c["email"].strip().lower()].append(c["customer_id"])
    dups = sorted(sorted(g) for g in groups.values() if len(g) > 1)
    assert len(dups) == 14 and sum(len(g) == 3 for g in dups) == 3

    active: dict[str, set[str]] = defaultdict(set)
    for o in data["orders"]:
        if o["status"] == "completed":
            active[o["order_date"][:7]].add(o["customer_id"])
    cohorts = {}
    for m in ("2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"):
        members = [c["customer_id"] for c in data["customers"] if c["signup_date"][:7] == m]
        row = {"size": len(members)}
        for k in (1, 2, 3):
            row[f"m{k}"] = round(sum(cid in active[month_add(m, k)] for cid in members) / len(members), 4)
        cohorts[m] = row

    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for o in data["orders"]:
        if o["order_date"] >= "2026-07-01" and o["status"] in ("completed", "refunded"):
            c = counts[prod[o["product_id"]]["category"]]
            c[0] += 1
            c[1] += o["status"] == "refunded"
    rates = {cat: round(r / n, 4) for cat, (n, r) in sorted(counts.items())}
    highest = max(rates, key=rates.get)  # type: ignore[arg-type]
    assert rates[highest] - sorted(rates.values())[-2] > 0.05
    return {
        "growth": {"region": best[0], "month": best[1], "growth_pct": round(growth[best], 2), "revenue": revenue},
        "dups": {"duplicate_groups": dups, "extra_records": sum(len(g) - 1 for g in dups)},
        "cohorts": cohorts,
        "refunds": {"rates": rates, "highest": highest},
    }


# ---------------------------------------------------------------- agent, prompts, cases

SYSTEM_PROMPT = """\
You are a meticulous data analyst. The data files are in your workspace under data/, and data/README.md \
describes them.

How to work:
1. Inspect before you compute: headers, types, row counts, value ranges, missing values and duplicates.
2. Do every computation with a script saved in the workspace (Python standard library: csv, collections, \
statistics, decimal) so the result is reproducible. Never estimate a number by eye. pandas is not installed \
and there is no network, so do not try to install packages.
3. Follow the definitions the request gives exactly (which rows count, how to round, the output structure). \
When the request names an output file and structure, write exactly that.
4. Sanity check results (totals add up, rates between 0 and 1) and state assumptions and caveats.
5. Finish with a concise answer that leads with the key numbers."""

README = """\
# Store data export (2026-01-01 to 2026-09-30)

| File | Rows | Columns |
|---|---|---|
| `customers.csv` | one per account | customer_id, name, email, region, signup_date (YYYY-MM-DD) |
| `products.csv` | one per product | product_id, name, category, unit_price (list price, USD) |
| `orders.csv` | one per order line | order_id, customer_id, order_date (YYYY-MM-DD), product_id, quantity, unit_price (USD actually charged per unit), status |

`status` is `completed`, `refunded` or `cancelled`. Revenue means quantity x unit_price of completed
order lines. A customer belongs to the region on their customer record. Accounts are exported as they
are in the production database, without any cleanup.
"""

SUGGESTED = [
    {
        "title": "Revenue by region and month",
        "prompt": "Compute completed revenue per region per month for January to September 2026 and write "
                  "revenue_by_month.csv with the header month,region,revenue (month as YYYY-MM, revenue in "
                  "dollars with 2 decimals), sorted by month then region. Then summarize the trend in a few "
                  "sentences: which region is growing fastest and whether anything looks unusual.",
    },
    {
        "title": "Top 10 customers by spend",
        "prompt": "Who are our 10 best customers? Rank customer accounts (by customer_id) by total completed "
                  "revenue and write top_customers.csv with the header rank,customer_id,name,region,revenue "
                  "(revenue in dollars with 2 decimals, highest first). Mention anything that makes this list "
                  "less reliable than it looks.",
    },
    {
        "title": "Data quality review",
        "prompt": "Before we build dashboards on this export, review the three CSV files for data quality "
                  "problems: duplicates, inconsistent formatting, impossible values, referential integrity "
                  "between files, and anything else you notice. Write data_quality.md with each issue, how many "
                  "rows it affects, an example, and a recommended fix.",
    },
]

GROWTH_PROMPT = """\
Which region had the highest month over month revenue growth in Q3 2026?

Definitions: revenue is quantity x unit_price of completed order lines; a region's month over month growth \
for month M is (revenue in M minus revenue in the previous month) / revenue in the previous month. Consider \
each region and each Q3 month (July, August and September 2026; July is compared with June).

Write two files:
1. results.json with exactly this structure:
   {"region": "...", "month": "YYYY-MM", "growth_pct": growth as a percentage rounded to 2 decimals,
    "revenue": {"<region>": {"2026-06": dollars, "2026-07": dollars, "2026-08": dollars, "2026-09": dollars}, ...}}
   with every region in "revenue".
2. report.md: a short report for the leadership team that leads with the answer and the growth number, and \
includes a markdown table of monthly revenue and month over month growth per region for June to September."""

DUPS_PROMPT = """\
Our CRM seems to have duplicate customer accounts. Find customers that share the same email address after \
normalizing it by trimming surrounding whitespace and lowercasing (no other normalization). Write results.json:

{"duplicate_groups": [["C1234", "C1301"], ...], "extra_records": integer}

Each group lists the customer_ids that share a normalized email, sorted ascending; groups are sorted by their \
first id. Only include groups with two or more accounts. extra_records is how many accounts would be removed \
if we kept exactly one per group."""

COHORT_PROMPT = """\
Compute monthly cohort retention. A customer's cohort is the month of their signup_date. For the January to \
June 2026 cohorts, retention in month k (k = 1, 2, 3) is the share of the cohort's customers who have at least \
one completed order line in the calendar month k months after the signup month (for the 2026-01 cohort, m1 is \
February). Treat every customer_id as a separate customer. Write results.json:

{"cohorts": {"2026-01": {"size": integer, "m1": rate, "m2": rate, "m3": rate}, ..., "2026-06": {...}}}

Rates are fractions between 0 and 1 rounded to 4 decimals. Also tell me in a sentence or two how retention \
changes across cohorts."""

REFUND_PROMPT = """\
Finance wants to know which product category had the worst refund rate in Q3 2026 (order dates from July 1 to \
September 30). The refund rate of a category is refunded order lines / (completed + refunded order lines); \
cancelled lines do not count. Write results.json:

{"rates": {"<category>": rate rounded to 4 decimals, ...}, "highest": "<category>"}

with every category that has Q3 order lines."""

LOAD_DATA = r"""
import csv
from collections import defaultdict
def rows(name):
    with open("data/" + name, newline="") as f:
        return list(csv.DictReader(f))
def cents(price):
    whole, frac = price.strip().split(".")
    return int(whole) * 100 + int(frac)
"""

GROWTH_CHECK = r"""
r = load_json("results.json")
t = TRUTH
check(norm(field(r, "region", str)) == norm(t["region"]), f"region is {r['region']!r}, expected {t['region']!r}")
check(str(field(r, "month")).strip()[:7] == t["month"], f"month is {r['month']!r}, expected {t['month']!r}")
near(field(r, "growth_pct"), t["growth_pct"], 0.05, "growth_pct")
rev = field(r, "revenue", dict)
lowered = {norm(k): v for k, v in rev.items()}
for region, months in t["revenue"].items():
    check(norm(region) in lowered, f"revenue is missing region {region!r}")
    for m, dollars in months.items():
        got = lowered[norm(region)]
        check(isinstance(got, dict) and m in got, f"revenue[{region!r}] is missing {m}")
        near(got[m], dollars, 0.01, f"revenue[{region!r}][{m!r}]")
check(os.path.isfile("report.md"), "report.md was not written")
report = open("report.md").read()
check(t["region"].lower() in report.lower(), "report.md does not name the winning region")
g = t["growth_pct"]
check(any(s in report for s in {f"{g:.1f}", f"{g:.2f}", f"{round(g)}%"}), f"report.md does not state the growth ({g:.1f}%)")
check(re.search(r"^\s*\|?\s*:?-{3,}", report, re.M), "report.md has no markdown table")
"""

GROWTH_RUBRIC = """\
Grade report.md (in the workspace diff) as a short report for a leadership audience. Pass only if every point holds:
1. The first paragraph (or first bullet) names Asia Pacific as the region with the highest month over month revenue growth in Q3 2026, says it happened in August 2026, and gives the growth as a percentage (about {g:.1f}%).
2. It contains a markdown table with one row (or column) per region, North America, Europe, Asia Pacific and Latin America, showing monthly revenue for June, July, August and September 2026 and the month over month growth for the Q3 months. The table values must be consistent with results.json in the diff.
3. It states the definition used: revenue counts only completed order lines (quantity x unit_price) and growth compares each month with the previous month.
4. It offers at least one sensible interpretation or caveat (for example that the August jump is concentrated in one region, may be a one off campaign, or that September fell back), without inventing causes that are presented as facts.
5. It is concise: no more than about 400 words, no raw script dumps or long code blocks.
Fail if any number in the report contradicts results.json or the headline region or month is wrong."""

DUPS_CHECK = r"""
r = load_json("results.json")
groups = field(r, "duplicate_groups", list)
check(all(isinstance(g, list) for g in groups), "duplicate_groups must be a list of lists")
got = sorted(sorted(str(x).strip() for x in g) for g in groups)
want = TRUTH["duplicate_groups"]
missing = [g for g in want if g not in got]
extra = [g for g in got if g not in want]
check(not missing and not extra, f"{len(missing)} expected groups missing (e.g. {missing[:1]}), {len(extra)} unexpected (e.g. {extra[:1]})")
check(field(r, "extra_records", int) == TRUTH["extra_records"], f"extra_records is {r['extra_records']}, expected {TRUTH['extra_records']}")
"""

COHORT_CHECK = r"""
r = load_json("results.json")
cohorts = field(r, "cohorts", dict)
for month, want in TRUTH.items():
    check(month in cohorts, f"cohort {month} missing (have {sorted(cohorts)})")
    got = cohorts[month]
    check(field(got, "size", int) == want["size"], f"cohort {month} size is {got['size']}, expected {want['size']}")
    for k in ("m1", "m2", "m3"):
        near(field(got, k), want[k], 0.0011, f"cohort {month} {k}")
"""

REFUND_CHECK = r"""
r = load_json("results.json")
rates = {norm(k): v for k, v in field(r, "rates", dict).items()}
for cat, want in TRUTH["rates"].items():
    check(norm(cat) in rates, f"rates is missing {cat!r}")
    near(rates[norm(cat)], want, 0.00011, f"refund rate for {cat}")
check(norm(field(r, "highest", str)) == norm(TRUTH["highest"]), f"highest is {r['highest']!r}, expected {TRUTH['highest']!r}")
"""

REVENUE_BY_MONTH_CHECK = LOAD_DATA + r"""
region = {c["customer_id"]: c["region"] for c in rows("customers.csv")}
rev = defaultdict(int)
for o in rows("orders.csv"):
    if o["status"] == "completed":
        rev[(o["order_date"][:7], region[o["customer_id"]])] += int(o["quantity"]) * cents(o["unit_price"])
want = sorted(rev.items())
check(os.path.isfile("revenue_by_month.csv"), "revenue_by_month.csv was not written")
with open("revenue_by_month.csv", newline="") as f:
    got = [r for r in csv.reader(f) if r]
check([c.strip() for c in got[0]] == ["month", "region", "revenue"], f"bad header {got[0]}")
check(len(got) - 1 == len(want), f"{len(got) - 1} rows, expected {len(want)}")
for row, ((month, reg), c) in zip(got[1:], want):
    check(row[0].strip()[:7] == month and row[1].strip() == reg, f"row {row} out of order, expected {month},{reg}")
    near(float(row[2]), c / 100, 0.01, f"revenue {month} {reg}")
"""

TOP_CUSTOMERS_CHECK = LOAD_DATA + r"""
spend = defaultdict(int)
for o in rows("orders.csv"):
    if o["status"] == "completed":
        spend[o["customer_id"]] += int(o["quantity"]) * cents(o["unit_price"])
want = sorted(spend.items(), key=lambda kv: -kv[1])[:10]
check(os.path.isfile("top_customers.csv"), "top_customers.csv was not written")
with open("top_customers.csv", newline="") as f:
    got = [r for r in csv.DictReader(f)]
check(len(got) == 10, f"{len(got)} rows, expected 10")
for i, (row, (cid, c)) in enumerate(zip(got, want), 1):
    check(row.get("customer_id", "").strip() == cid, f"rank {i} is {row.get('customer_id')}, expected {cid}")
    near(float(row["revenue"]), c / 100, 0.01, f"revenue of {cid}")
"""


def agent_spec() -> dict:
    return {
        "slug": "data-analyst",
        "name": "Data analyst",
        "description": "Answers business questions about the store's CSV exports with reproducible scripts, "
                       "machine readable results and short reports.",
        "system_prompt": SYSTEM_PROMPT,
        "tools": ["bash", "read_file", "write_file", "edit_file"],
        "network": False,
        "suggested_prompts": SUGGESTED,
    }


def build(out: Path) -> dict:
    data = generate()
    truth = truth_of(data)
    seed = out / "seed" / "data"
    write_text(seed / "README.md", README)
    cust = [[c["customer_id"], c["name"], c["email"], c["region"], c["signup_date"]] for c in data["customers"]]
    write_text(seed / "customers.csv", to_csv(["customer_id", "name", "email", "region", "signup_date"], cust))
    prods = [[p["product_id"], p["name"], p["category"], f"{p['unit_price']:.2f}"] for p in data["products"]]
    write_text(seed / "products.csv", to_csv(["product_id", "name", "category", "unit_price"], prods))
    keys = ["order_id", "customer_id", "order_date", "product_id", "quantity", "unit_price", "status"]
    write_text(seed / "orders.csv", to_csv(keys, [[o[k] for k in keys] for o in data["orders"]]))

    g = truth["growth"]
    cases = [
        case("q3-mom-growth", "train", GROWTH_PROMPT, py_checker(GROWTH_CHECK, g)),
        case("q3-growth-report-quality", "train", GROWTH_PROMPT, GROWTH_RUBRIC.format(g=g["growth_pct"]), "judge"),
        case("duplicate-customers", "train", DUPS_PROMPT, py_checker(DUPS_CHECK, truth["dups"])),
        case("cohort-retention", "train", COHORT_PROMPT, py_checker(COHORT_CHECK, truth["cohorts"])),
        case("q3-refund-rate", "holdout", REFUND_PROMPT, py_checker(REFUND_CHECK, truth["refunds"])),
    ]
    write_json(out / "evals.json", cases)
    write_json(out / "agent.json", agent_spec())
    write_json(out / "prompt_checks.json", {
        "Revenue by region and month": py_checker(REVENUE_BY_MONTH_CHECK),
        "Top 10 customers by spend": py_checker(TOP_CUSTOMERS_CHECK),
    })
    return truth


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--out", type=Path, default=HERE)
    args = ap.parse_args()
    t = build(args.out)
    print(f"data-analyst: wrote seed, evals.json, agent.json and prompt_checks.json under {args.out}")
    g = t["growth"]
    print(f"  top Q3 growth: {g['region']} {g['month']} {g['growth_pct']}%; duplicate groups "
          f"{len(t['dups']['duplicate_groups'])}; highest Q3 refund rate {t['refunds']['highest']}")


if __name__ == "__main__":
    main()
