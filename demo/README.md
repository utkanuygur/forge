# Forge reference demo: repo-maintainer

This demo shows an agent getting measurably better at a real job. The agent,
`repo-maintainer`, picks up tickets against `acme-orders`, a small FastAPI order service.
It is a competent engineer from the first run: it reads the code, implements the
feature, writes tests. What it does not know are the team's unwritten conventions, and the
eval checkers enforce them. Forge turns failing runs and feedback into lessons; the next
version follows the conventions and passes cases it failed before, including holdout cases
the optimizer never saw.

## The conventions (presenter only, never show these to the agent)

They are followed consistently in the seed code but written down nowhere: no
CONTRIBUTING file, no comments, the README is silent, and the system prompt does not mention them.

| | Convention | Evidence in the seed repo | Typical violation |
|---|---|---|---|
| a | Logging goes through `acme.log.get_logger(__name__)`, never `print()` or `logging.getLogger` | `app.py` logs every state change | `print(f"AUDIT refund ...")` |
| b | Money is integer cents (`*_cents: int`), never float | every amount in `models.py`/`pricing.py` | `refunded_amount: float`, `SHIPPING_FEE = 4.99` |
| c | Every new or changed HTTP route gets a line under `## Unreleased` in `CHANGELOG.md` | the changelog history | no changelog entry |
| d | `make check` (ruff `E,F,I,B,UP` + pytest) passes | `Makefile`, `[tool.ruff]` in `pyproject.toml` | `Optional[...]`, unused imports, long lines |

## Eval cases (`evals.json`)

| Case | Split | Conventions it stresses |
|---|---|---|
| add-refund-endpoint | train | a, b, c, d |
| fix-discount-rounding | train | d |
| add-order-notes | train | c, d |
| add-shipping-fee | train | b, c, d |
| add-order-cancellation | holdout | a, c, d |
| add-coupon-codes | holdout | b, c, d |
| add-orders-pagination | holdout | c, d |

Every checker runs the same convention checks (no `print(` in `src`, no direct `logging`
use outside `acme/log.py`, no `float` in `src`, `make check` passes), plus a CHANGELOG
check when the ticket touches an HTTP route, plus a functional test that drives the app with
`fastapi.testclient`. Money fields are matched loosely (any `*_cents` int whose name contains
`refund`/`ship`), so naming is not over-constrained. Checkers are inline bash, so the agent
cannot see or edit them. Each prints a `FAIL: ...` line saying what went wrong.

The checkers are generated: edit `build_evals.py`, then `uv run python demo/build_evals.py`.

## Load

Start the Forge server, then:

```
uv run python demo/load.py                       # default http://localhost:8000
uv run python demo/load.py --base-url http://localhost:8000 --promote --rotate-key
```

The loader reads `acme-orders/` into `seed_files` (caches skipped), creates the agent from
`repo-maintainer.json` (or a new version if the slug exists and the prompt or seed repo
changed), syncs the eval cases by name, and prints the API key (only when created or rotated)
and the share token.

## Verify the evals

```
uv run python demo/verify.py                     # docker if forge-sandbox exists, else local
uv run python demo/verify.py --runner local --case add-coupon-codes -v
```

For every case, on a fresh git-initialized copy of `acme-orders`, the checker must
FAIL on the untouched seed, FAIL on `solutions/<case>/naive.py` (works, but breaks a
convention the way a capable newcomer would), and PASS on `solutions/<case>/reference.py`.
`solutions/` is for verification only and is never seeded into the agent's workspace.

## Suggested flow

1. `load.py`, then run evals for v1 in the UI: expect most cases to fail on conventions
   while the features themselves work.
2. Open a failing run, thumbs down with a comment like "we never use print, use our logger".
3. Run improve: the optimizer proposes lessons, the candidate is evaluated on train and
   holdout, and promoted if it passes more with no regressions.
4. Show the holdout gain: the lessons generalized to tickets the optimizer never saw.
