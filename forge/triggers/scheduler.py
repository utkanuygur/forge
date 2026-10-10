"""Asyncio scheduler for `schedule` triggers.

Every tick, each enabled schedule computes its slots after `last_slot_at` (set to the creation or re-enable time,
then advanced to each slot it handles, and persisted before a run starts so a restart never fires a slot twice):
- slots older than MISS_WINDOW are skipped and recorded once as a `missed` fire;
- of the slots due within MISS_WINDOW only the latest fires (late but once);
- with `skip_if_running`, a slot is recorded as `skipped` while the trigger's previous run is still active.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Callable

from forge import db
from forge.triggers import core, store, text
from forge.triggers.cron import Cron, CronError, get_zone

log = logging.getLogger("forge.triggers.scheduler")

MISS_WINDOW = 600.0
MAX_SCAN = 1000


def template_fields(slot: float, tz: str) -> dict[str, str]:
    d = datetime.fromtimestamp(slot, get_zone(tz))
    return {"date": d.strftime("%Y-%m-%d"), "time": d.strftime("%H:%M"), "datetime": d.isoformat(timespec="minutes"),
            "weekday": d.strftime("%A"), "timezone": tz}


class Scheduler:
    def __init__(self, clock: Callable[[], float] = core.now, interval: float | None = None) -> None:
        self.clock = clock
        self.interval = interval if interval is not None else float(os.environ.get("FORGE_SCHEDULER_INTERVAL") or 15)
        self.task: asyncio.Task | None = None

    def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self.task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # noqa: BLE001
                log.exception("scheduler tick failed")
            await asyncio.sleep(self.interval)

    async def tick(self) -> list[core.Fired]:
        now = self.clock()
        out = []
        for t in store.list_enabled("schedule"):
            try:
                if (r := await self.check(t, now)) is not None:
                    out.append(r)
            except Exception as exc:  # noqa: BLE001
                log.exception("schedule %s failed", t["id"])
                store.update(t["id"], last_error=f"{type(exc).__name__}: {exc}")
        return out

    async def check(self, t: dict, now: float) -> core.Fired | None:
        cfg = t["config"]
        tz = cfg.get("timezone") or "UTC"
        try:
            cron = Cron.parse(cfg["cron"])
        except (CronError, KeyError) as exc:
            store.update(t["id"], last_error=f"invalid schedule: {exc}")
            return None
        base = t["last_slot_at"] if t["last_slot_at"] is not None else t["created_at"]
        first = cron.next_after(base, tz)
        if first is None or first > now:
            return None
        missed: list[float] = []
        due: list[float] = []
        s: float | None = first
        while s is not None and s <= now and len(missed) + len(due) < MAX_SCAN:
            (missed if s < now - MISS_WINDOW else due).append(s)
            s = cron.next_after(s, tz)
        many = s is not None and s <= now
        if many:
            # Too many slots to walk (for example every minute for days): jump to the recent window.
            s = cron.next_after(now - MISS_WINDOW, tz)
            due = []
            while s is not None and s <= now:
                due.append(s)
                s = cron.next_after(s, tz)
        latest = due[-1] if due else (now - MISS_WINDOW if many else missed[-1])
        if not store.claim_slot(t["id"], latest):
            return None
        if missed:
            count = f"{len(missed)}+" if many else str(len(missed))
            when = datetime.fromtimestamp(missed[0], get_zone(tz)).strftime("%Y-%m-%d %H:%M")
            store.add_fire(t["id"], "missed", detail=f"Skipped {count} scheduled time(s) from {when} {tz} that were "
                                                      "more than 10 minutes late (the server was not running)")
        if not due:
            return None
        slot = due[-1]
        if cfg.get("skip_if_running", True) and t["last_run_id"]:
            prev = db.get_run(t["last_run_id"])
            if prev and prev["status"] in core.ACTIVE:
                detail = f"Skipped because the previous run {prev['id']} is still running"
                f = store.add_fire(t["id"], "skipped", detail=detail)
                return core.Fired("skipped", detail, fire=f)
        return await core.fire(t, text.render(cfg.get("input_template") or "", template_fields(slot, tz)))


scheduler: Scheduler | None = None


def start() -> Scheduler | None:
    global scheduler
    if os.environ.get("FORGE_SCHEDULER", "1") == "0":
        return None
    scheduler = Scheduler()
    scheduler.start()
    return scheduler


async def stop() -> None:
    global scheduler
    if scheduler is not None:
        await scheduler.stop()
        scheduler = None
