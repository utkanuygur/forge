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
