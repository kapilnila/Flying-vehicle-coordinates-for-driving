"""Request metadata for the managed vehicle API. Coordinates stay in immutable history."""
import asyncio
from dataclasses import dataclass
import logging
import re
import time
import uuid

from starlette.datastructures import MutableHeaders

from .errors import ValidationError
from .storage import RecordStore, valid_vehicle_id

logger = logging.getLogger(__name__)
RETENTION_MS = 90 * 24 * 60 * 60 * 1000
DELETE_BATCH = 500
SUMMARY_COLUMNS = ("request_id", "started_at_ms", "method", "route", "status", "duration_ms",
                   "vehicle_id", "point_count", "replayed")


def cutoff_ms():
    return time.time_ns() // 1_000_000 - RETENTION_MS


@dataclass
class Attempt:
    request_id: str
    started_at_ms: int
    method: str
    route: str
    vehicle_id: str | None = None
    point_ids: tuple[str, str] | None = None
    point_count: int = 0
    replayed: bool = False


class RequestLog:
    def __init__(self, db):
        self.db = db

    def write(self, attempt, status, duration_ms):
        first, last = attempt.point_ids or (None, None)
        with self.db.transaction() as connection:
            self.db.execute(connection, """INSERT INTO request_logs
                (request_id, started_at_ms, method, route, status, duration_ms, vehicle_id,
                 point_count, replayed, first_point, last_point)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (attempt.request_id, attempt.started_at_ms, attempt.method, attempt.route,
                 status, duration_ms, attempt.vehicle_id, attempt.point_count, attempt.replayed,
                 first, last))

    def page(self, limit, cursor, vehicle_id, status, request_id, from_ms, to_ms):
        if from_ms is not None and to_ms is not None and from_ms > to_ms:
            raise ValidationError("from_ms must not exceed to_ms")
        clauses = ["started_at_ms >= ?"]
        args = [max(cutoff_ms(), from_ms or 0)]
        for clause, value in (("vehicle_id = ?", vehicle_id), ("status = ?", status),
                              ("request_id = ?", request_id), ("started_at_ms <= ?", to_ms)):
            if value is not None:
                clauses.append(clause)
                args.append(value)
        if cursor is not None:
            match = re.fullmatch(r"([0-9]{1,15}):([0-9a-f]{32})", cursor)
            if not match:
                raise ValidationError("invalid request log cursor")
            clauses.append("(started_at_ms, request_id) < (?, ?)")
            args.extend((int(match[1]), match[2]))
        sql = f"""SELECT {', '.join(SUMMARY_COLUMNS)} FROM request_logs
                  WHERE {' AND '.join(clauses)} ORDER BY started_at_ms DESC, request_id DESC LIMIT ?"""
        with self.db.connection() as connection:
            rows = self.db.execute(connection, sql, (*args, limit + 1)).fetchall()
        items = [dict(zip(SUMMARY_COLUMNS, row)) for row in rows[:limit]]
        for item in items:
            item["replayed"] = bool(item["replayed"])
        next_cursor = None
        if len(rows) > limit:
            last = items[-1]
            next_cursor = f"{last['started_at_ms']}:{last['request_id']}"
        return {"items": items, "next_cursor": next_cursor}

    def points(self, request_id):
        # A batch receives consecutive fixed-width IDs in one transaction.
        # The primary-key range is bounded by its size (at most 1,000 records).
        with self.db.connection() as connection:
            rows = self.db.execute(connection, """SELECT r.payload FROM request_logs l
                JOIN records r ON r.id BETWEEN l.first_point AND l.last_point
                WHERE l.request_id = ? AND l.started_at_ms >= ? ORDER BY r.id""",
                (request_id, cutoff_ms())).fetchall()
        items = [RecordStore._read_record(row[0]) for row in rows]
        for item in items:
            item.pop("request_key", None)
        return items

    def prune(self):
        with self.db.transaction() as connection:
            return self.db.execute(connection, """DELETE FROM request_logs WHERE request_id IN (
                SELECT request_id FROM request_logs WHERE started_at_ms < ?
                ORDER BY started_at_ms, request_id LIMIT ?)""", (cutoff_ms(), DELETE_BATCH)).rowcount

    async def maintain(self, stop):
        while not stop.is_set():
            delay = 3600
            try:
                if await asyncio.to_thread(self.prune) == DELETE_BATCH:
                    delay = 0.1
            except Exception as exc:
                logger.warning("Request log cleanup failed (%s)", type(exc).__name__)
                delay = 60
            try:
                await asyncio.wait_for(stop.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass


class VehicleRequestLogging:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or path.split("/")[:3] not in (
                ["", "v1", "ids"], ["", "v1", "records"], ["", "v1", "vehicles"]):
            return await self.app(scope, receive, send)

        start = time.monotonic()
        attempt = Attempt(uuid.uuid4().hex, time.time_ns() // 1_000_000, scope["method"], "unmatched")
        # Resolve templates even when authentication or capacity checks cut routing short.
        for route in scope["app"].routes:
            pattern = getattr(route, "path_regex", None)
            match = pattern.fullmatch(path.rstrip("/")) if pattern else None
            if match:
                attempt.route = route.path
                vehicle = match.groupdict().get("vehicle")
                if valid_vehicle_id(vehicle):
                    attempt.vehicle_id = vehicle
                break
        scope.setdefault("state", {})["attempt"] = attempt
        status = 500
        logged = False

        async def persist():
            nonlocal logged
            logged = True
            try:
                elapsed_ms = round((time.monotonic() - start) * 1000)
                await asyncio.to_thread(scope["app"].state.request_log.write, attempt, status, elapsed_ms)
            except Exception as exc:
                logger.warning("Request log write failed for %s (%s)", attempt.request_id, type(exc).__name__)

        async def observe(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)["X-Request-ID"] = attempt.request_id
            elif message["type"] == "http.response.body" and not message.get("more_body", False):
                await persist()
            await send(message)

        try:
            await self.app(scope, receive, observe)
        finally:
            if not logged:
                await persist()
