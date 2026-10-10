# What to try first

Forge ships four example agents, each with starter prompts you can run right away and evaluation cases with
known correct answers. Every agent runs in the Docker sandbox with no network.

```bash
make serve                                     # in one terminal
make examples                                  # or: uv run python demo/load_examples.py --base-url http://localhost:8000
```

Open an agent, go to the Runs tab (or the Playground), and click one of the chips above the input. Each
suggested prompt below says what a correct result looks like; the ones marked **checked** also have a shell
checker in `demo/examples/<slug>/prompt_checks.json` that you can run against the run's workspace.

Most starter prompts take 20 to 40 seconds and cost under $0.10 with the default Sonnet model; "Summarize today's incidents" reads most of the logs and costs about $1.

Eval cases are in the Evaluations tab. **Train** cases are the ones the optimizer may learn from; **held out**
cases are never shown to it and prove that what it learned generalizes. Shell checkers compare against
ground truth embedded in the checker (never in the workspace); judge cases are graded by the grader model
against a rubric with concrete pass or fail criteria.

## Log triage (`log-triage`)

One day (2026-09-14, UTC) of production logs for a small storefront: nginx access and error logs,
checkout-api JSON logs, a Postgres log, deploy events and a certificate monitor, about 5,400 lines generated
deterministically by `demo/examples/log-triage/build.py`. Four incidents are planted, each with one root cause.

| Suggested prompt | A correct result |
|---|---|
| Summarize today's incidents (**checked**) | `incidents.md` covers all four: checkout 500s from checkout-api 2.14.0 (`KeyError: 'currency'`) fixed by the rollback to 2.13.4 at 14:39; the 03:10 to 03:25 slowdown from `reporting-cron` scanning `orders`; the payments.acme.example certificate expiring 2026-09-21; the scraper at 203.0.113.77. The 09:15 search 503 blip is noise. |
| 5xx error rate per endpoint (**checked**) | `error_rates.csv` with every endpoint; `POST /api/v1/checkout` is worst with 91 5xx, then `GET /api/v1/search` with 3. |
| Hourly traffic report (**checked**) | `traffic.csv` with 24 rows; hour 14 has the 5xx, hour 18 is the busiest (the scraper), hour 03 has the slowest requests. |

| Eval case | Split | Checker | Correct answer (in `answer.json`) |
|---|---|---|---|
| checkout-bad-deploy | train | shell | `POST /api/v1/checkout`, 14:03:23 to 14:38:22 (within 2 min), `[500]`, 91 failed requests, bad 2.14.0, fixed by 2.13.4, `KeyError` |
| orders-slow-queries | train | shell | 03:10:04 to 03:24:51, `reporting-cron`, table `orders`, 47 statements over 1 s, max 9631.207 ms |
| expiring-certificates | train | shell | payments.acme.example (7 days), then grafana.internal.acme.example (18 days); status.acme.example (31) is excluded |
| catalog-scraper | **holdout** | shell | 203.0.113.77, PriceScraper/2.3 user agent, 771 requests, 261 rate limited, 18:05:00 to 18:21:39 |

## Data analyst (`data-analyst`)

A store export for January to September 2026 (`customers.csv`, `products.csv`, `orders.csv`, about 12,000
rows from `demo/examples/data-analyst/build.py`), solvable with the Python standard library (pandas is not
installed). Planted: an Asia Pacific campaign in August, 14 duplicate accounts by email case and whitespace
(plus two near misses that are not duplicates), and a Q3 refund spike in Electronics.

| Suggested prompt | A correct result |
|---|---|
| Revenue by region and month (**checked**) | `revenue_by_month.csv` with 36 rows; the summary points out the Asia Pacific jump in August and its fall back in September. |
| Top 10 customers by spend (**checked**) | `top_customers.csv` ranked by completed revenue; a good answer warns that duplicate accounts split some customers' spend. |
| Data quality review | `data_quality.md` finds the 14 duplicate email groups (17 extra accounts), explains that the Gmail dot and same name pairs are not duplicates under a trim and lowercase rule, and checks referential integrity (it holds). |

| Eval case | Split | Checker | Correct answer |
|---|---|---|---|
| q3-mom-growth | train | shell | `results.json`: Asia Pacific, 2026-08, 63.88% (within 0.05), monthly revenue June to September for every region (within a cent); `report.md` names the region, states the number and has a table |
| q3-growth-report-quality | train | judge | The same task graded for the report: leads with the answer, a correct table, the definitions, a sensible caveat, concise |
| duplicate-customers | train | shell | 14 groups (three with three accounts), 17 extra records; the Gmail dot variant must not be merged |
| cohort-retention | train | shell | January to June 2026 cohorts with sizes and m1 to m3 retention (within 0.001), for example 2026-01: 122 customers, m1 0.6885 |
| q3-refund-rate | **holdout** | shell | Electronics 0.1722, every other category under 0.06; cancelled lines excluded |

## Docs writer (`docs-writer`)

A small Python library, shelfcache (LRU cache with per entry expiry, a `@cached` decorator, JSON
persistence), with tests, docstrings (four missing on purpose) and a CHANGELOG whose Unreleased section has
two breaking changes. The README is a stub.

| Suggested prompt | A correct result |
|---|---|
| Add missing docstrings (**checked**) | Docstrings on `TTLCache.pop`, `clear`, `purge` and `serialize.load`; no code change (the checker compares the syntax trees without docstrings) and the tests pass. |
| Write a runnable example (**checked**) | `examples/<name>.py` uses `@cached(..., ttl=...)`, shows a hit, prints `stats()`, runs in under 10 seconds. |
| Write a contributing guide (**checked**) | `CONTRIBUTING.md` covers setup, `pytest`, Google style docstrings and adding a line under `## Unreleased` in the CHANGELOG. |

| Eval case | Split | Checker | Correct result |
|---|---|---|---|
| readme-quickstart-runs | train | shell | README with `pip install shelfcache` and a Quickstart ```python block that uses `TTLCache` and `@cached`, prints output and actually runs (the checker extracts and executes it) |
| readme-quickstart-quality | train | judge | Accurate description, Python 3.10+, keyword only `ttl`, truthful LRU demo, API overview, concise |
| api-docs-cache | train | judge | `docs/api/cache.md` with exact signatures, ValueError and KeyError conditions, hit, miss and expiry rules, CacheStats fields, nothing invented |
| api-docs-serialize | **holdout** | shell | `docs/api/serialize.md` documents `dump` and `load` with every parameter, TypeError and ValueError, and its examples dump and load a cache and run; library code unchanged |
| release-notes-0-5 | **holdout** | judge | `RELEASE_NOTES.md`: both breaking changes with before and after code first, all additions, the `expire()` deprecation, both fixes, nothing invented |

## Repo maintainer (`repo-maintainer`)

The reference demo (see [demo/README.md](../demo/README.md)): tickets against the acme-orders FastAPI service,
whose evals enforce the team's unwritten conventions. The suggested prompts are extra tickets, not eval cases.

| Suggested prompt | A correct result |
|---|---|
| Add a receipt endpoint (**checked**) | `GET /orders/{id}/receipt` returns text/plain with the id and status, item lines and Subtotal, Discount and Total formatted by `format_cents`; 404 for unknown orders; tests added; `make check` passes. Following the conventions also means a CHANGELOG line and logging through `acme.log`. |
| Add order stats (**checked**) | `GET /orders/stats` registered before `/orders/{order_id}`, all three statuses counted, `revenue_cents` over paid and shipped orders, tests added. |
| Track status history | Orders gain `status_history` entries for creation, pay and ship, with timestamps; tests cover each transition. |
| Explain the codebase | A read only answer: app, models, pricing, store and log modules; pending to paid to shipped; integer cents and floor rounded percentage discounts; where a new route, its tests and its CHANGELOG line go. No files changed. |

Eval cases (7, verified by `demo/verify.py`): add-refund-endpoint, fix-discount-rounding, add-order-notes and
add-shipping-fee are train; add-order-cancellation, add-coupon-codes and add-orders-pagination are held out.

## Verifying the checkers

```bash
uv run python demo/verify_examples.py          # every example checker: FAIL on the seed, FAIL on a plausible
                                               # wrong answer where one exists, PASS on the reference solution
uv run python demo/verify_examples.py --judge  # also grade the judge cases with the real grader (costs cents)
uv run python demo/verify.py                   # the repo-maintainer cases (21 expectations)
```

Reference solutions live in `demo/examples/<slug>/solutions/` and are never seeded into an agent's workspace.
Seeds and checkers are generated: edit `demo/examples/<slug>/build.py`, then run it.
