"""Cron parsing and next-run calculation for scheduled tasks."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class CronField:
    values: frozenset[int]

    def matches(self, value: int) -> bool:
        return value in self.values


def _parse_field(raw: str, minimum: int, maximum: int) -> CronField | None:
    values: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            return None
        step = 1
        if "/" in part:
            part, step_raw = part.split("/", 1)
            if not step_raw.isdigit() or int(step_raw) <= 0:
                return None
            step = int(step_raw)
        if part == "*":
            start, end = minimum, maximum
        elif "-" in part:
            start_raw, end_raw = part.split("-", 1)
            if not start_raw.isdigit() or not end_raw.isdigit():
                return None
            start, end = int(start_raw), int(end_raw)
        elif part.isdigit():
            start = end = int(part)
        else:
            return None
        if start < minimum or end > maximum or start > end:
            return None
        values.update(range(start, end + 1, step))
    return CronField(frozenset(values))


@dataclass(frozen=True)
class CronExpression:
    minute: CronField
    hour: CronField
    day: CronField
    month: CronField
    weekday: CronField

    def matches(self, dt: datetime) -> bool:
        cron_weekday = (dt.weekday() + 1) % 7
        return (
            self.minute.matches(dt.minute)
            and self.hour.matches(dt.hour)
            and self.day.matches(dt.day)
            and self.month.matches(dt.month)
            and self.weekday.matches(cron_weekday)
        )


def parse_cron_expression(raw: str) -> CronExpression | None:
    parts = raw.split()
    if len(parts) != 5:
        return None
    minute = _parse_field(parts[0], 0, 59)
    hour = _parse_field(parts[1], 0, 23)
    day = _parse_field(parts[2], 1, 31)
    month = _parse_field(parts[3], 1, 12)
    weekday = _parse_field(parts[4], 0, 7)
    if weekday and 7 in weekday.values:
        weekday = CronField(frozenset(0 if v == 7 else v for v in weekday.values))
    if not all((minute, hour, day, month, weekday)):
        return None
    return CronExpression(minute, hour, day, month, weekday)


def compute_next_cron_run(cron: str, after_ms: int) -> int | None:
    expr = parse_cron_expression(cron)
    if not expr:
        return None
    dt = datetime.fromtimestamp(after_ms / 1000).replace(second=0, microsecond=0)
    dt += timedelta(minutes=1)
    limit = dt + timedelta(days=366 * 5)
    while dt <= limit:
        if expr.matches(dt):
            return int(dt.timestamp() * 1000)
        dt += timedelta(minutes=1)
    return None
