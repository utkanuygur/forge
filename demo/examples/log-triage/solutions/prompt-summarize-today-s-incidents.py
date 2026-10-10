"""Reference for the "Summarize today's incidents" suggested prompt (a hand written report)."""

from _logs import workspace

REPORT = """\
# Incidents on 2026-09-14 (UTC)

## 1. Checkout outage after checkout-api 2.14.0 (14:03 to 14:38)

Impact: POST /api/v1/checkout returned HTTP 500 for about 72% of attempts for 35 minutes.
Root cause: checkout-api 2.14.0 (deploy finished 14:02:51) raises `KeyError: 'currency'` in
checkout/payments.py build_intent. Rollback to 2.13.4 finished at 14:39:32 and errors stopped.

## 2. Slow orders page from the reporting job (03:10 to 03:25)

Impact: GET /api/v1/orders took 1.4 to 4.2 s. Root cause: reporting-cron ran a full aggregate over
`orders` joined with `customers` every 20 to 40 s, each statement 2 to 9.6 s, and checkout-api
reads queued behind it.

## 3. Certificate for payments.acme.example expires 2026-09-21

7 days left at the last check; grafana.internal.acme.example has 18 days left. Renew both.

## 4. Catalog scraper 203.0.113.77 (18:05 to 18:21)

PriceScraper/2.3 sent 771 requests; 261 were rate limited with 429.

## Ruled out

Three 503s on search at 09:15 during the search-api 1.8.2 deploy (seconds, no follow up), and two
429s for one customer double submitting the cart at 16:44.
"""
(workspace() / "incidents.md").write_text(REPORT)
