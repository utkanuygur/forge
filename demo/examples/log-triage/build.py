"""Build the log-triage example: seed logs, agent.json, evals.json and prompt_checks.json.

    uv run python demo/examples/log-triage/build.py [--out DIR]

Everything is generated from a fixed random seed, so every run writes the same bytes.
The logs cover one day (2026-09-14, UTC) of a small storefront: nginx in front of
checkout-api and catalog-api, a Postgres database, a deploy pipeline and a certificate
monitor. Four incidents are planted, each with one unambiguous root cause:

  1. 14:02 deploy of checkout-api 2.14.0 makes POST /api/v1/checkout return 500 (KeyError
     'currency') until the rollback to 2.13.4 finishes at 14:39.
  2. 03:10 to 03:25 the reporting-cron job runs a full scan aggregate over `orders`;
     statements take 2 to 9 seconds and checkout-api queries slow down behind it.
  3. The certificate for payments.acme.example expires in 7 days, grafana.internal in 18.
  4. 18:05 to 18:21 a scraper at 203.0.113.77 hammers the catalog and gets rate limited (429).

Ground truth is computed from the generated lines themselves (not from the plan), and each
reference solution in solutions/ recomputes it independently from the files.
"""

from __future__ import annotations

import argparse
import random
import zlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from common import case, py_checker, write_json, write_text  # noqa: E402

DAY = datetime(2026, 9, 14, tzinfo=timezone.utc)
UTC = timezone.utc

DEPLOY_START = DAY.replace(hour=14, minute=2, second=10)
DEPLOY_LIVE = DAY.replace(hour=14, minute=2, second=51)
ROLLBACK_START = DAY.replace(hour=14, minute=38, second=55)
ROLLBACK_DONE = DAY.replace(hour=14, minute=39, second=32)
SLOW_START = DAY.replace(hour=3, minute=10, second=4)
SLOW_END = DAY.replace(hour=3, minute=24, second=51)
SCRAPE_START = DAY.replace(hour=18, minute=5, second=0)
SCRAPE_END = DAY.replace(hour=18, minute=21, second=40)

SCRAPER_IP = "203.0.113.77"
SCRAPER_UA = "Mozilla/5.0 (compatible; PriceScraper/2.3; +http://pricescraper.example/bot)"
GOOD_VERSION, BAD_VERSION = "2.13.4", "2.14.0"

UAS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
    "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
    "AcmeShop/5.2.1 (Android 15; Pixel 8)",
    "AcmeShop/5.2.0 (iOS 18.0; iPhone15,2)",
]
# (method, path template, weight, upstream service)
ROUTES = [
    ("GET", "/api/v1/products", 26, "catalog"),
    ("GET", "/api/v1/products/{id}", 22, "catalog"),
    ("GET", "/api/v1/search", 12, "catalog"),
    ("POST", "/api/v1/cart/items", 11, "checkout"),
    ("GET", "/api/v1/cart", 8, "checkout"),
    ("GET", "/api/v1/orders", 7, "checkout"),
    ("POST", "/api/v1/checkout", 6, "checkout"),
    ("POST", "/api/v1/login", 5, "checkout"),
    ("GET", "/static/app.js", 3, "static"),
]
UPSTREAMS = {"catalog": ["10.0.3.21:8080", "10.0.3.22:8080"], "checkout": ["10.0.2.11:8080", "10.0.2.12:8080"]}
# Requests per minute by hour of day (UTC): quiet nights, busy afternoons.
HOURLY_RATE = [0.9, 0.7, 0.6, 0.6, 0.6, 0.7, 1.0, 1.4, 1.9, 2.4, 2.7, 2.9,
               3.1, 3.2, 3.4, 3.3, 3.1, 3.0, 3.2, 3.4, 3.0, 2.4, 1.8, 1.3]
SEARCH_TERMS = ["usb-c+cable", "desk+lamp", "running+shoes", "coffee+grinder", "wool+socks", "backpack", "monitor+arm"]
CERTS = [  # host, not_after, issuer
    ("www.acme.example", "2026-12-02T11:04:31Z", "R11"),
    ("api.acme.example", "2026-12-02T11:04:31Z", "R11"),
    ("payments.acme.example", "2026-09-21T23:59:59Z", "DigiCert Global G2 TLS RSA SHA256 2020 CA1"),
    ("grafana.internal.acme.example", "2026-10-03T08:30:00Z", "Acme Internal CA"),
    ("status.acme.example", "2026-10-16T09:00:00Z", "R10"),
    ("cdn.acme.example", "2027-03-18T00:00:00Z", "Amazon RSA 2048 M02"),
]


def iso(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_ms(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def clf(t: datetime) -> str:
    return t.strftime("%d/%b/%Y:%H:%M:%S +0000")


class Gen:
    def __init__(self, seed: int = 20260914) -> None:
        self.rng = random.Random(seed)
        self.access: list[tuple[datetime, str, dict]] = []
        self.app: list[tuple[datetime, str]] = []
        self.pg: list[tuple[datetime, str]] = []
        self.nginx_err: list[tuple[datetime, str]] = []
        self.ips = [f"{a}.{b}.{c}.{d}" for a, b, c, d in
                    ((self.rng.choice([24, 67, 73, 98, 172, 185, 91]), self.rng.randrange(1, 255),
                      self.rng.randrange(1, 255), self.rng.randrange(1, 255)) for _ in range(140))]
        self.req_n = 0

    def req_id(self) -> str:
        self.req_n += 1
        return f"{self.rng.getrandbits(48):012x}"

    def version_at(self, t: datetime) -> str:
        return BAD_VERSION if DEPLOY_LIVE <= t < ROLLBACK_DONE else GOOD_VERSION

    # ------------------------------------------------------------ nginx + app

    def request(self, t: datetime, method: str, tmpl: str, service: str, *, ip: str | None = None,
                ua: str | None = None, status: int | None = None, rt: float | None = None) -> None:
        rng = self.rng
        path = tmpl.replace("{id}", str(rng.randrange(1000, 1600)))
        if tmpl == "/api/v1/search":
            path += "?q=" + rng.choice(SEARCH_TERMS)
        if tmpl == "/api/v1/products" and rng.random() < 0.5:
            path += f"?page={rng.randrange(1, 9)}"
        ip = ip or rng.choice(self.ips)
        ua = ua or rng.choice(UAS)
        rid = self.req_id()
        if rt is None:
            rt = round(rng.lognormvariate(-2.6, 0.55), 3)  # ~75 ms median
            if tmpl == "/api/v1/checkout":
                rt = round(rt * 3.2, 3)
        if status is None:
            status = 200
            if method == "POST" and tmpl in ("/api/v1/cart/items",):
                status = 201
            if tmpl == "/api/v1/login" and rng.random() < 0.12:
                status = 401
            elif tmpl == "/api/v1/products/{id}" and rng.random() < 0.03:
                status = 404
            elif tmpl == "/api/v1/checkout" and rng.random() < 0.05:
                status = 402  # card declined
        # The slow query window drags checkout-api reads on orders.
        if tmpl == "/api/v1/orders" and SLOW_START <= t <= SLOW_END + timedelta(seconds=30):
            rt = round(rng.uniform(1.4, 4.2), 3)
        size = {200: rng.randrange(400, 9000), 201: rng.randrange(120, 400)}.get(status, rng.randrange(60, 300))
        if tmpl == "/static/app.js":
            size, upstream = 184_211, "-"
        elif status in (429, 503):  # rejected at the edge, never reached an upstream
            upstream = "-"
        else:
            upstream = rng.choice(UPSTREAMS[service])
        ref = "https://www.acme.example/" if service != "static" and rng.random() < 0.6 else "-"
        line = (f'{ip} - - [{clf(t)}] "{method} {path} HTTP/1.1" {status} {size} "{ref}" "{ua}" '
                f'rt={rt:.3f} upstream={upstream} req_id={rid}')
        self.access.append((t, line, {"method": method, "tmpl": tmpl, "status": status, "ip": ip, "ua": ua}))
        if service == "checkout" and upstream != "-":
            self.app_request(t, method, tmpl, status, rt, rid)

    def app_request(self, t: datetime, method: str, tmpl: str, status: int, rt: float, rid: str) -> None:
        version = self.version_at(t)
        start = t - timedelta(seconds=rt)
        route = f"{method} {tmpl}"
        if status == 500:
            self.app.append((start + timedelta(milliseconds=self.rng.randrange(20, 60)), ({
                "ts": None, "level": "ERROR", "service": "checkout-api", "version": version, "request_id": rid,
                "route": route, "msg": "unhandled exception while handling request",
                "exc_type": "KeyError", "exc": "'currency'",
                "stack": "checkout/views.py:41 in create_checkout > checkout/payments.py:88 in build_intent > "
                         "intent = PaymentIntent(amount=cart.total_cents, currency=cart.meta['currency'])",
            })))
        if status == 402:
            self.app.append((start + timedelta(milliseconds=30), ({
                "ts": None, "level": "WARN", "service": "checkout-api", "version": version, "request_id": rid,
                "route": route, "msg": "payment declined by provider", "decline_code": "insufficient_funds"})))
        if tmpl == "/api/v1/orders" and rt > 1.0:
            self.app.append((t - timedelta(milliseconds=5), ({
                "ts": None, "level": "WARN", "service": "checkout-api", "version": version, "request_id": rid,
                "route": route, "msg": "slow database call", "query": "orders_by_customer",
                "db_ms": round(rt * 1000 - self.rng.randrange(10, 40), 1)})))
        level = "ERROR" if status >= 500 else "INFO"
        self.app.append((t, ({
            "ts": None, "level": level, "service": "checkout-api", "version": version, "request_id": rid,
            "route": route, "msg": "request completed", "status": status, "duration_ms": round(rt * 1000, 1)})))

    def baseline_traffic(self) -> None:
        rng = self.rng
        weights = [w for _, _, w, _ in ROUTES]
        for minute in range(24 * 60):
            base = DAY + timedelta(minutes=minute)
            rate = HOURLY_RATE[minute // 60] * 0.85
            n = sum(1 for _ in range(8) if rng.random() < rate / 8)
            for _ in range(n):
                t = base + timedelta(seconds=rng.randrange(0, 60), milliseconds=rng.randrange(0, 1000))
                method, tmpl, _, service = rng.choices(ROUTES, weights)[0]
                if tmpl == "/api/v1/checkout" and DEPLOY_LIVE <= t < ROLLBACK_DONE:
                    continue  # the incident generator owns checkout traffic in the window
                self.request(t, method, tmpl, service)
            if minute % 5 == 0:  # load balancer health check
                t = base + timedelta(seconds=2)
                line = (f'10.0.0.2 - - [{clf(t)}] "GET /healthz HTTP/1.1" 200 2 "-" "ELB-HealthChecker/2.0" '
                        f'rt=0.001 upstream=10.0.2.11:8080 req_id={self.req_id()}')
                self.access.append((t, line, {"method": "GET", "tmpl": "/healthz", "status": 200, "ip": "10.0.0.2",
                                              "ua": "ELB-HealthChecker/2.0"}))

    def bad_deploy(self) -> None:
        """Checkout traffic during the bad version, including customers retrying."""
        rng = self.rng
        t = DEPLOY_LIVE + timedelta(seconds=7)
        while t < ROLLBACK_DONE:
            fails = rng.random() < 0.72
            self.request(t, "POST", "/api/v1/checkout", "checkout", status=500 if fails else 200,
                         rt=round(rng.uniform(0.09, 0.32), 3) if fails else None)
            t += timedelta(seconds=rng.randrange(9, 26), milliseconds=rng.randrange(0, 1000))
        # A couple of minutes of healthy checkouts right after the rollback.
        for k in range(6):
            self.request(ROLLBACK_DONE + timedelta(seconds=11 + 23 * k), "POST", "/api/v1/checkout", "checkout",
                         status=200)
        # Only the bad version's new code path logs this at startup.
        self.app.append((DEPLOY_LIVE - timedelta(seconds=4), ({
            "ts": None, "level": "INFO", "service": "checkout-api", "version": BAD_VERSION,
            "msg": "starting checkout-api", "workers": 4, "commit": "9f3c2ab"})))
        self.app.append((ROLLBACK_DONE - timedelta(seconds=3), ({
            "ts": None, "level": "INFO", "service": "checkout-api", "version": GOOD_VERSION,
            "msg": "starting checkout-api", "workers": 4, "commit": "51d0e7c"})))

    def scraper(self) -> None:
        rng = self.rng
        t = SCRAPE_START
        sent_in_minute: dict[int, int] = {}
        while t <= SCRAPE_END:
            key = int((t - DAY).total_seconds() // 60)
            sent_in_minute[key] = sent_in_minute.get(key, 0) + 1
            limited = sent_in_minute[key] > 30  # 30 requests per minute per IP
            tmpl = "/api/v1/products" if rng.random() < 0.6 else "/api/v1/products/{id}"
            self.request(t, "GET", tmpl, "catalog", ip=SCRAPER_IP, ua=SCRAPER_UA,
                         status=429 if limited else 200, rt=0.0 if limited else None)
            t += timedelta(milliseconds=rng.randrange(900, 1700))
            if t > SCRAPE_END:
                break
        # An impatient customer double submitting gets rate limited twice in the afternoon (distractor).
        base = DAY.replace(hour=16, minute=44, second=10)
        for k in range(33):
            self.request(base + timedelta(milliseconds=1500 * k), "POST", "/api/v1/cart/items", "checkout",
                         ip="198.51.100.23", ua=UAS[2], status=429 if k in (31, 32) else 201)

    def search_blip(self) -> None:
        """The search-api deploy at 09:15 causes three 503s on search (a distractor, not an incident)."""
        base = DAY.replace(hour=9, minute=15, second=31)
        for k in range(3):
            self.request(base + timedelta(seconds=4 * k), "GET", "/api/v1/search", "catalog", status=503, rt=0.002)
            self.nginx_err.append((base + timedelta(seconds=4 * k), (
                f"[error] 31#31: *{40000 + k} no live upstreams while connecting to upstream, client: "
                f"{self.rng.choice(self.ips)}, server: api.acme.example, request: \"GET /api/v1/search HTTP/1.1\", "
                f"upstream: \"http://catalog\", host: \"api.acme.example\"")))

    # ------------------------------------------------------------ postgres

    def postgres(self) -> None:
        rng = self.rng
        normal = [
            ("checkout-api", "SELECT id, status, total_cents, created_at FROM orders WHERE customer_id = $1 "
                             "ORDER BY created_at DESC LIMIT 20"),
            ("checkout-api", "INSERT INTO orders (customer_id, status, total_cents) VALUES ($1, $2, $3) RETURNING id"),
            ("checkout-api", "UPDATE carts SET updated_at = now() WHERE id = $1"),
            ("catalog-api", "SELECT p.* FROM products p JOIN inventory i ON i.product_id = p.id "
                            "WHERE i.available > 0 ORDER BY p.rank LIMIT 48 OFFSET $1"),
            ("catalog-api", "SELECT id, name FROM products WHERE search_vector @@ plainto_tsquery($1) LIMIT 20"),
        ]
        t = DAY + timedelta(seconds=rng.randrange(30, 200))
        while t < DAY + timedelta(days=1):
            app, stmt = rng.choice(normal)
            self.pg_stmt(t, app, round(rng.uniform(251, 880), 3), stmt)
            t += timedelta(seconds=rng.randrange(160, 420), milliseconds=rng.randrange(0, 1000))
        for h in range(0, 24, 1):  # checkpoints
            ct = DAY + timedelta(hours=h, minutes=rng.randrange(0, 60), seconds=rng.randrange(0, 60))
            self.pg.append((ct, f"{pg_ts(ct)} [61] @ LOG:  checkpoint complete: wrote {rng.randrange(200, 2400)} "
                                f"buffers ({rng.uniform(0.5, 6):.1f}%); write={rng.uniform(20, 160):.3f} s"))
        # The spike: reporting-cron aggregates over all of orders with no usable index.
        report = ("SELECT c.region, date_trunc('day', o.created_at) AS day, sum(o.total_cents) FROM orders o "
                  "JOIN customers c ON c.id = o.customer_id WHERE o.created_at >= now() - interval '400 days' "
                  "GROUP BY 1, 2 ORDER BY 1, 2")
        self.pg.append((SLOW_START - timedelta(seconds=2), f"{pg_ts(SLOW_START - timedelta(seconds=2))} [28211] "
                        "reporting-cron@shop LOG:  connection authorized: user=reporting database=shop"))
        t = SLOW_START
        while t <= SLOW_END:
            self.pg_stmt(t, "reporting-cron", round(rng.uniform(2100, 9400), 3), report, pid=28211)
            # Reads from checkout-api queue behind the scans.
            if rng.random() < 0.55:
                lt = t + timedelta(seconds=rng.randrange(2, 9))
                if lt < SLOW_END:
                    self.pg_stmt(lt, "checkout-api", round(rng.uniform(1100, 1900), 3), normal[0][1])
            t += timedelta(seconds=rng.randrange(18, 44), milliseconds=rng.randrange(0, 1000))
        last = max(x for x, line in self.pg if "duration:" in line and float(line.split("duration: ")[1].split()[0]) > 1000)
        assert last <= SLOW_END
        self.pg_stmt(SLOW_END, "reporting-cron", 9631.207, report, pid=28211)
        end = SLOW_END + timedelta(seconds=1)
        self.pg.append((end, f"{pg_ts(end)} [28211] reporting-cron@shop LOG:  disconnection: session time: "
                             "0:14:49.112 user=reporting database=shop"))

    def pg_stmt(self, t: datetime, app: str, ms: float, stmt: str, pid: int | None = None) -> None:
        pid = pid or self.rng.choice([28102, 28117, 28140, 28163])
        user = {"checkout-api": "checkout", "catalog-api": "catalog", "reporting-cron": "reporting"}[app]
        self.pg.append((t, f"{pg_ts(t)} [{pid}] {app}@shop user={user} LOG:  duration: {ms:.3f} ms  "
                           f"statement: {stmt}"))

    # ------------------------------------------------------------ deploys and certs

    def deploy_log(self) -> str:
        rows = [
            (DAY.replace(hour=9, minute=14, second=58), "INFO", "service=search-api version=1.8.2 env=prod "
             "action=start by=ci@acme.example pipeline=4411"),
            (DAY.replace(hour=9, minute=15, second=44), "INFO", "service=search-api version=1.8.2 env=prod "
             "action=finish status=success healthcheck=passed duration=46s"),
            (DEPLOY_START, "INFO", f"service=checkout-api version={BAD_VERSION} env=prod action=start "
             "by=ci@acme.example pipeline=4419 commit=9f3c2ab message=\"Build PaymentIntent from cart metadata\""),
            (DEPLOY_LIVE, "INFO", f"service=checkout-api version={BAD_VERSION} env=prod action=finish "
             "status=success healthcheck=passed duration=41s"),
            (DAY.replace(hour=14, minute=21, second=7), "INFO", "service=catalog-api version=3.2.0 env=prod "
             "action=config_reload key=search.boost_new_arrivals value=true by=maria@acme.example"),
            (ROLLBACK_START, "WARN", f"service=checkout-api version={GOOD_VERSION} env=prod action=rollback_start "
             f"from={BAD_VERSION} by=oncall-dev@acme.example reason=\"checkout errors\""),
            (ROLLBACK_DONE, "INFO", f"service=checkout-api version={GOOD_VERSION} env=prod action=rollback_finish "
             "status=success healthcheck=passed duration=37s"),
            (DAY.replace(hour=20, minute=30, second=12), "INFO", "service=catalog-api version=3.2.1 env=staging "
             "action=start by=ci@acme.example pipeline=4426"),
            (DAY.replace(hour=20, minute=31, second=2), "INFO", "service=catalog-api version=3.2.1 env=staging "
             "action=finish status=success healthcheck=passed duration=50s"),
        ]
        return "".join(f"{iso(t)} deployd[812]: {lvl:<5} {msg}\n" for t, lvl, msg in rows)

    def certwatch(self) -> tuple[str, list[dict]]:
        out = []
        last_check = None
        for h in (0, 6, 12, 18):
            check_t = DAY + timedelta(hours=h, seconds=1)
            last_check = check_t
            for host, not_after, issuer in CERTS:
                exp = datetime.fromisoformat(not_after.replace("Z", "+00:00"))
                days = int((exp - check_t).total_seconds() // 86400)
                lvl = "WARN" if days <= 30 else "INFO"
                note = " msg=\"certificate expires soon, renew\"" if days <= 30 else ""
                out.append(f"{iso(check_t)} certwatch {lvl:<5} host={host} port=443 issuer=\"{issuer}\" "
                           f"not_after={not_after} days_left={days}{note}\n")
        assert last_check is not None
        soon = []
        for host, not_after, _ in CERTS:
            exp = datetime.fromisoformat(not_after.replace("Z", "+00:00"))
            days = int((exp - last_check).total_seconds() // 86400)
            if days <= 30:
                soon.append({"host": host, "not_after": not_after, "days_left": days})
        soon.sort(key=lambda c: c["not_after"])
        return "".join(out), soon


def pg_ts(t: datetime) -> str:
    return t.strftime("%Y-%m-%d %H:%M:%S.") + f"{t.microsecond // 1000:03d} UTC"


def render_app(rows: list[tuple[datetime, dict]]) -> str:
    import json
    out = []
    for t, obj in sorted(rows, key=lambda r: r[0]):
        obj = dict(obj)
        obj["ts"] = iso_ms(t)
        out.append(json.dumps(obj, separators=(",", ":")) + "\n")
    return "".join(out)


README = """\
# Production logs for 2026-09-14 (UTC)

Exported from the storefront's production stack for the day of Monday 2026-09-14.
Every timestamp is UTC.

| File | What it is |
|---|---|
| `logs/nginx/access.log` | Edge proxy access log for api.acme.example. Format: `ip - - [time] "request" status bytes "referer" "user agent" rt=<seconds> upstream=<host:port or -> req_id=<id>` |
| `logs/nginx/error.log` | Edge proxy error log |
| `logs/app/checkout-api.jsonl` | checkout-api application log, one JSON object per line. `request_id` matches `req_id` in the access log |
| `logs/db/postgresql.log` | Postgres server log (`log_min_duration_statement = 250ms`), prefix `time [pid] application@database user=<role>` |
| `logs/deploy/deploy.log` | Deploy pipeline events for every service and environment |
| `logs/cron/certwatch.log` | TLS certificate monitor, runs every 6 hours |

Upstreams: `10.0.2.x` is checkout-api (cart, checkout, orders, login), `10.0.3.x` is catalog-api
(products, search). The edge rate limits each client IP to 30 requests per minute (HTTP 429).
"""


def build(out: Path) -> dict:
    g = Gen()
    g.baseline_traffic()
    g.bad_deploy()
    g.scraper()
    g.search_blip()
    g.postgres()
    certs_text, soon = g.certwatch()

    g.access.sort(key=lambda r: r[0])
    access_text = "".join(line + "\n" for _, line, _ in g.access)
    for t, line, meta in g.access:
        if meta["status"] == 500:
            g.nginx_err.append((t, (f"[error] 31#31: *{zlib.crc32(line.encode()) % 90000} upstream returned 500 while reading "
                                    f"response header from upstream, server: api.acme.example, request: "
                                    f"\"POST /api/v1/checkout HTTP/1.1\", host: \"api.acme.example\"")))
    g.nginx_err.append((DAY.replace(hour=2, minute=11, second=40), "[warn] 31#31: *1932 an upstream response is "
                        "buffered to a temporary file /var/cache/nginx/proxy_temp/4/00/0000000004 while reading upstream"))
    g.nginx_err.sort(key=lambda r: r[0])
    error_text = "".join(f"{t.strftime('%Y/%m/%d %H:%M:%S')} {msg}\n" for t, msg in g.nginx_err)
    g.pg.sort(key=lambda r: r[0])
    pg_text = "".join(line + "\n" for _, line in g.pg)

    seed = out / "seed"
    write_text(seed / "README.md", README)
    write_text(seed / "logs/nginx/access.log", access_text)
    write_text(seed / "logs/nginx/error.log", error_text)
    write_text(seed / "logs/app/checkout-api.jsonl", render_app(g.app))
    write_text(seed / "logs/db/postgresql.log", pg_text)
    write_text(seed / "logs/deploy/deploy.log", g.deploy_log())
    write_text(seed / "logs/cron/certwatch.log", certs_text)

    # ---------------------------------------------------------------- ground truth from the lines
    checkout_5xx = [(t, m) for t, _, m in g.access if m["tmpl"] == "/api/v1/checkout" and m["status"] >= 500]
    other_5xx = [m for _, _, m in g.access if m["tmpl"] != "/api/v1/checkout" and m["status"] >= 500]
    assert all(DEPLOY_LIVE <= t <= ROLLBACK_DONE for t, _ in checkout_5xx)
    assert {m["tmpl"] for m in other_5xx} == {"/api/v1/search"} and len(other_5xx) == 3
    slow = [(t, line) for t, line in g.pg if "duration:" in line
            and float(line.split("duration: ")[1].split()[0]) > 1000]
    scrape = [(t, m) for t, _, m in g.access if m["ip"] == SCRAPER_IP]
    truth = {
        "deploy": {
            "endpoint": "POST /api/v1/checkout",
            "incident_start": iso(min(t for t, _ in checkout_5xx)),
            "incident_end": iso(max(t for t, _ in checkout_5xx)),
            "status_codes": sorted({m["status"] for _, m in checkout_5xx}),
            "failed_requests": len(checkout_5xx),
            "bad_version": BAD_VERSION,
            "rollback_version": GOOD_VERSION,
            "exception": "KeyError",
            "rollback_finished": iso(ROLLBACK_DONE),
        },
        "slow": {
            "spike_start": iso(min(t for t, _ in slow)),
            "spike_end": iso(max(t for t, _ in slow)),
            "application_name": "reporting-cron",
            "table": "orders",
            "slow_statements": len(slow),
            "max_duration_ms": max(float(line.split("duration: ")[1].split()[0]) for _, line in slow),
        },
        "certs": soon,
        "scraper": {
            "ip": SCRAPER_IP,
            "user_agent": SCRAPER_UA,
            "total_requests": len(scrape),
            "rate_limited": sum(1 for _, m in scrape if m["status"] == 429),
            "first_seen": iso(min(t for t, _ in scrape)),
            "last_seen": iso(max(t for t, _ in scrape)),
        },
    }
    write_json(out / "evals.json", eval_cases(truth))
    write_json(out / "agent.json", agent_spec())
    write_json(out / "prompt_checks.json", prompt_checks(truth))
    return truth


# ---------------------------------------------------------------- agent, prompts, cases

SYSTEM_PROMPT = """\
You are an on-call site reliability engineer. Production logs exported from the stack are in your workspace \
under logs/, and README.md describes each file and its format.

How to work:
1. Read README.md, then look at the files before drawing conclusions: their size, their time range and a few \
sample lines.
2. Answer with evidence. Use grep, awk and short Python scripts to count and filter; never estimate a number \
you can compute. Correlate across sources (edge log, application log, database log, deploys) to separate the \
root cause from symptoms and coincidences.
3. When a task asks for a file with a given structure, write exactly that structure: the field names, types and \
formats it specifies. Times are ISO 8601 in UTC, like 2026-09-14T14:03:00Z.
4. Finish with a short summary: what happened, when, the root cause, and the evidence (file and line examples).

You have no network access. Do not modify the files under logs/."""


SUGGESTED = [
    {
        "title": "Summarize today's incidents",
        "prompt": "Go through all of today's logs and write incidents.md: one section per real production "
                  "incident with a timeline (UTC), impact, root cause and the evidence you used. Ignore noise "
                  "that had no user impact, and say briefly what you ruled out.",
    },
    {
        "title": "5xx error rate per endpoint",
        "prompt": "From logs/nginx/access.log, compute the 5xx error rate per endpoint for the whole day and "
                  "write error_rates.csv with the header endpoint,requests,errors_5xx,error_rate. An endpoint is "
                  "the method plus the path without the query string, with numeric path segments replaced by "
                  "{id} (for example GET /api/v1/products/{id}). error_rate is errors_5xx / requests rounded to "
                  "4 decimals. Include every endpoint, sorted by errors_5xx descending, then endpoint. Then tell "
                  "me which endpoint is the worst and why.",
    },
    {
        "title": "Hourly traffic report",
        "prompt": "Build an hourly traffic report from logs/nginx/access.log: write traffic.csv with the header "
                  "hour,requests,errors_5xx,max_rt_ms where hour is 00 to 23 (UTC), requests counts every line in "
                  "that hour, and max_rt_ms is the largest rt= value in milliseconds as an integer. Then point "
                  "out the hours that look unusual and explain why.",
    },
]


def agent_spec() -> dict:
    return {
        "slug": "log-triage",
        "name": "Log triage",
        "description": "Investigates production logs (edge, app, database, deploys, certificates) and reports "
                       "incidents with root causes and evidence.",
        "system_prompt": SYSTEM_PROMPT,
        "tools": ["bash", "read_file", "write_file", "edit_file"],
        "network": False,
        "suggested_prompts": SUGGESTED,
    }


DEPLOY_PROMPT = """\
Customers reported that checkout was failing for a while this afternoon. Investigate the logs and write \
answer.json with exactly these fields:

{
  "endpoint": "METHOD /path of the failing endpoint, for example GET /api/v1/cart",
  "incident_start": "time of the first 5xx response on that endpoint, ISO 8601 UTC",
  "incident_end": "time of the last 5xx response on that endpoint, ISO 8601 UTC",
  "status_codes": [list of the distinct 5xx status codes it returned, as integers],
  "failed_requests": number of 5xx responses on that endpoint in the access log (integer),
  "bad_version": "the checkout-api version that caused it",
  "rollback_version": "the version that fixed it",
  "exception": "the exception class raised in the application"
}"""

SLOW_PROMPT = """\
The orders page was very slow for some customers in the early morning. Find out why using the database log and \
write answer.json with exactly these fields:

{
  "spike_start": "time of the first statement slower than 1000 ms, ISO 8601 UTC",
  "spike_end": "time of the last statement slower than 1000 ms, ISO 8601 UTC",
  "application_name": "the Postgres application_name that caused the slowdown",
  "table": "the table its statements were scanning",
  "slow_statements": number of statements of any application slower than 1000 ms (integer),
  "max_duration_ms": the longest statement duration in milliseconds (number)
}"""

CERT_PROMPT = """\
Before the weekly ops review: which TLS certificates are about to expire? Use the most recent certificate check \
in the logs and write answer.json with exactly this structure:

{
  "certificates": [
    {"host": "hostname", "not_after": "expiry time as logged, ISO 8601 UTC", "days_left": integer as of the last check}
  ]
}

Include every certificate with 30 days or less left at the last check, soonest expiry first."""

SCRAPER_PROMPT = """\
Catalog latency complaints came in around the early evening, and we suspect a single client was hammering us. \
Find it and write answer.json with exactly these fields:

{
  "ip": "client IP address",
  "user_agent": "its full User-Agent string",
  "total_requests": number of requests from that IP in the access log (integer),
  "rate_limited": how many of them got HTTP 429 (integer),
  "first_seen": "time of its first request, ISO 8601 UTC",
  "last_seen": "time of its last request, ISO 8601 UTC"
}"""

DEPLOY_CHECK = """
a = load_json("answer.json")
t = TRUTH
check(norm(field(a, "endpoint", str)) == norm(t["endpoint"]), f"endpoint is {a['endpoint']!r}, expected {t['endpoint']!r}")
near_time(field(a, "incident_start"), t["incident_start"], 120, "incident_start")
near_time(field(a, "incident_end"), t["incident_end"], 120, "incident_end")
codes = field(a, "status_codes", list)
check(sorted(set(codes)) == t["status_codes"], f"status_codes is {codes}, expected {t['status_codes']}")
check(field(a, "failed_requests", int) == t["failed_requests"], f"failed_requests is {a['failed_requests']}, expected {t['failed_requests']}")
check(str(field(a, "bad_version")).lstrip("v") == t["bad_version"], f"bad_version is {a['bad_version']!r}")
check(str(field(a, "rollback_version")).lstrip("v") == t["rollback_version"], f"rollback_version is {a['rollback_version']!r}")
check(str(field(a, "exception")).strip().split(":")[0].split(".")[-1] == t["exception"], f"exception is {a['exception']!r}")
"""

SLOW_CHECK = """
a = load_json("answer.json")
t = TRUTH
near_time(field(a, "spike_start"), t["spike_start"], 60, "spike_start")
near_time(field(a, "spike_end"), t["spike_end"], 60, "spike_end")
check(norm(field(a, "application_name", str)) == t["application_name"], f"application_name is {a['application_name']!r}")
check(norm(field(a, "table", str)).split(".")[-1] == t["table"], f"table is {a['table']!r}")
check(field(a, "slow_statements", int) == t["slow_statements"], f"slow_statements is {a['slow_statements']}, expected {t['slow_statements']}")
near(field(a, "max_duration_ms"), t["max_duration_ms"], 1.0, "max_duration_ms")
"""

CERT_CHECK = """
a = load_json("answer.json")
certs = field(a, "certificates", list)
want = TRUTH
check(len(certs) == len(want), f"{len(certs)} certificates listed, expected {len(want)}")
for i, (got, exp) in enumerate(zip(certs, want)):
    check(norm(field(got, "host", str)) == exp["host"], f"certificates[{i}].host is {got['host']!r}, expected {exp['host']!r} (soonest first)")
    near_time(field(got, "not_after"), exp["not_after"], 1, f"certificates[{i}].not_after")
    near(field(got, "days_left"), exp["days_left"], 1, f"certificates[{i}].days_left")
"""

SCRAPER_CHECK = """
a = load_json("answer.json")
t = TRUTH
check(str(field(a, "ip", str)).strip() == t["ip"], f"ip is {a['ip']!r}")
check(norm(field(a, "user_agent", str)) == norm(t["user_agent"]), f"user_agent is {a['user_agent']!r}")
check(field(a, "total_requests", int) == t["total_requests"], f"total_requests is {a['total_requests']}, expected {t['total_requests']}")
check(field(a, "rate_limited", int) == t["rate_limited"], f"rate_limited is {a['rate_limited']}, expected {t['rate_limited']}")
near_time(field(a, "first_seen"), t["first_seen"], 60, "first_seen")
near_time(field(a, "last_seen"), t["last_seen"], 60, "last_seen")
"""


def eval_cases(truth: dict) -> list[dict]:
    return [
        case("checkout-bad-deploy", "train", DEPLOY_PROMPT, py_checker(DEPLOY_CHECK, truth["deploy"])),
        case("orders-slow-queries", "train", SLOW_PROMPT, py_checker(SLOW_CHECK, truth["slow"])),
        case("expiring-certificates", "train", CERT_PROMPT, py_checker(CERT_CHECK, truth["certs"])),
        case("catalog-scraper", "holdout", SCRAPER_PROMPT, py_checker(SCRAPER_CHECK, truth["scraper"])),
    ]


# Checkers for the suggested prompts: used by verify_examples.py and the live smoke, never loaded as eval cases.
INCIDENTS_CHECK = """
check(os.path.isfile("incidents.md"), "incidents.md was not written")
text = open("incidents.md").read().lower()
for needle, why in TRUTH:
    check(needle in text, f"incidents.md does not mention {needle!r} ({why})")
"""

RECOMPUTE = r"""
import csv
from collections import defaultdict
LINE = re.compile(r'^(\S+) - - \[([^\]]+)\] "(\S+) (\S+) [^"]*" (\d{3}) \S+ "[^"]*" "[^"]*" rt=([\d.]+)')
rows = []
for line in open("logs/nginx/access.log"):
    m = LINE.match(line)
    check(m, f"seed log line did not parse: {line[:80]}")
    rows.append(m.groups())
"""

ERROR_RATES_CHECK = RECOMPUTE + r"""
def endpoint(method, path):
    path = path.split("?")[0]
    return method + " " + "/".join("{id}" if p.isdigit() else p for p in path.split("/"))
stats = defaultdict(lambda: [0, 0])
for ip, ts, method, path, status, rt in rows:
    s = stats[endpoint(method, path)]
    s[0] += 1
    s[1] += int(status) >= 500
want = sorted(stats.items(), key=lambda kv: (-kv[1][1], kv[0]))
check(os.path.isfile("error_rates.csv"), "error_rates.csv was not written")
with open("error_rates.csv", newline="") as f:
    got = list(csv.reader(f))
check(got and [c.strip() for c in got[0]] == ["endpoint", "requests", "errors_5xx", "error_rate"], f"bad header {got[:1]}")
body = [r for r in got[1:] if any(c.strip() for c in r)]
check(len(body) == len(want), f"{len(body)} endpoint rows, expected {len(want)}")
for row, (ep, (n, e)) in zip(body, want):
    check(row[0].strip() == ep, f"row {row} out of order or wrong endpoint, expected {ep}")
    check(int(row[1]) == n and int(row[2]) == e, f"row {row}: expected requests={n} errors_5xx={e}")
    near(float(row[3]), round(e / n, 4), 0.00011, f"error_rate for {ep}")
"""

TRAFFIC_CHECK = RECOMPUTE + r"""
hours = defaultdict(lambda: [0, 0, 0])
for ip, ts, method, path, status, rt in rows:
    h = hours[ts.split(":")[1]]
    h[0] += 1
    h[1] += int(status) >= 500
    h[2] = max(h[2], round(float(rt) * 1000))
check(os.path.isfile("traffic.csv"), "traffic.csv was not written")
with open("traffic.csv", newline="") as f:
    got = list(csv.reader(f))
check(got and [c.strip() for c in got[0]] == ["hour", "requests", "errors_5xx", "max_rt_ms"], f"bad header {got[:1]}")
body = {r[0].strip().zfill(2): r for r in got[1:] if r and r[0].strip()}
check(sorted(body) == [f"{h:02d}" for h in range(24)], f"hours present: {sorted(body)}")
for hour, (n, e, mx) in sorted(hours.items()):
    r = body[hour]
    check(int(r[1]) == n and int(r[2]) == e, f"hour {hour}: got {r[1:3]}, expected requests={n} errors_5xx={e}")
    check(abs(int(float(r[3])) - mx) <= 1, f"hour {hour}: max_rt_ms {r[3]}, expected {mx}")
"""


def prompt_checks(truth: dict) -> dict:
    needles = [
        (truth["deploy"]["bad_version"], "the bad checkout-api deploy"),
        ("keyerror", "the exception behind the checkout 500s"),
        (truth["deploy"]["rollback_version"], "the rollback"),
        ("reporting-cron", "the job behind the slow queries"),
        ("payments.acme.example", "the certificate about to expire"),
        (truth["scraper"]["ip"], "the scraper"),
    ]
    return {
        "Summarize today's incidents": py_checker(INCIDENTS_CHECK, needles),
        "5xx error rate per endpoint": py_checker(ERROR_RATES_CHECK),
        "Hourly traffic report": py_checker(TRAFFIC_CHECK),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--out", type=Path, default=HERE)
    args = ap.parse_args()
    truth = build(args.out)
    print(f"log-triage: wrote seed, evals.json, agent.json and prompt_checks.json under {args.out}")
    print(f"  checkout 5xx={truth['deploy']['failed_requests']} slow statements={truth['slow']['slow_statements']} "
          f"scraper requests={truth['scraper']['total_requests']} (429: {truth['scraper']['rate_limited']})")


if __name__ == "__main__":
    main()
