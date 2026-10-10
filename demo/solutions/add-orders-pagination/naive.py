# Correct behavior and CHANGELOG, but never ran `make check`: old style typing imports
# (Optional/List) that the project's ruff rules reject, and an unused import.
from _util import changelog, edit

edit("src/acme/app.py", "from datetime import UTC, datetime\n",
     "from datetime import UTC, datetime\nfrom typing import List, Optional\n")
edit("src/acme/app.py", "from fastapi import FastAPI, HTTPException\n",
     "from fastapi import FastAPI, HTTPException, Query, Request, Response\n")
edit("src/acme/app.py", '''    def list_orders() -> list[Order]:
        return orders.list()
''', '''    def list_orders(
        response: Response,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
        status: Optional[OrderStatus] = None,
    ) -> List[Order]:
        matching = [o for o in orders.list() if status is None or o.status == status]
        response.headers["X-Total-Count"] = str(len(matching))
        return matching[offset : offset + limit]
''')
changelog("`GET /orders` now supports pagination (`limit`, `offset`) and a `status` filter.")
