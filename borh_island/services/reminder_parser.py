"""Natural-language RU/EN parser for timers and repeating reminders."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class ParsedReminder:
    title: str
    due_at: datetime
    repeat_rule: str = "none"  # none | hourly | daily | weekdays | weekly


_REL_UNIT_MAP: dict[str, int] = {
    "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "с": 1, "сек": 1, "секунд": 1, "секунду": 1, "секунды": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "м": 60, "мин": 60, "минут": 60, "минуту": 60, "минуты": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "ч": 3600, "час": 3600, "часа": 3600, "часов": 3600,
    "d": 86400, "day": 86400, "days": 86400,
    "д": 86400, "дн": 86400, "день": 86400, "дня": 86400, "дней": 86400,
}

_UNIT_PATTERN = "|".join(sorted(_REL_UNIT_MAP.keys(), key=len, reverse=True))
_DUR_TOKEN_RE = re.compile(rf"(\d+(?:[.,]\d+)?)\s*({_UNIT_PATTERN})(?![A-Za-zА-Яа-яЁё])", re.IGNORECASE)


def parse_duration_seconds(text: str) -> int | None:
    """Parse duration expressions like '10m', '1h 30m', '25 мин 10 сек', 'полчаса'."""
    low = text.strip().lower()
    if not low:
        return None
    if re.search(r"\b(полчаса|half\s+an?\s+hour)\b", low):
        return 1800
    if re.search(r"\b(полтора\s+часа)\b", low):
        return 5400

    matches = list(_DUR_TOKEN_RE.finditer(low))
    if not matches:
        # Pure integer without unit in explicit timer contexts -> minutes by default
        if re.fullmatch(r"\d{1,4}", low):
            return int(low) * 60
        return None
    total = 0.0
    for m in matches:
        val = float(m.group(1).replace(",", "."))
        unit = m.group(2).lower()
        total += val * _REL_UNIT_MAP[unit]
    secs = int(round(total))
    return secs if secs > 0 else None


def _extract_repeat(text: str) -> tuple[str, str]:
    low = text
    rules: list[tuple[str, str]] = [
        (r"\b(каждый\s+будн[ий]{1,2}\s*(?:день)?|по\s+будням|every\s+weekday)\b", "weekdays"),
        (r"\b(каждый\s+день|ежедневно|every\s+day|daily)\b", "daily"),
        (r"\b(каждую\s+неделю|еженедельно|каждый\s+(?:понедельник|вторник|сред[уа]|четверг|пятниц[уа]|суббот[уа]|воскресенье)|every\s+week|weekly)\b", "weekly"),
        (r"\b(каждый\s+час|ежечасно|every\s+hour|hourly)\b", "hourly"),
    ]
    for pat, rule in rules:
        m = re.search(pat, low, re.IGNORECASE)
        if m:
            cleaned = (low[:m.start()] + " " + low[m.end():]).strip()
            return rule, re.sub(r"\s+", " ", cleaned)
    return "none", text


def parse_reminder_phrase(phrase: str, now: datetime | None = None) -> ParsedReminder | None:
    """Parse natural-language reminder/timer text in RU or EN into a ParsedReminder."""
    raw = phrase.strip()
    if not raw:
        return None
    ref = now or datetime.now()
    repeat_rule, work = _extract_repeat(raw)

    # 1. Explicit ISO or DD.MM.YYYY date + optional time
    date_m = re.search(r"\b(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\b", work)
    iso_m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", work)
    target_date = None
    if iso_m:
        y, mo, d = int(iso_m.group(1)), int(iso_m.group(2)), int(iso_m.group(3))
        target_date = datetime(y, mo, d)
        work = (work[:iso_m.start()] + " " + work[iso_m.end():]).strip()
    elif date_m:
        d, mo = int(date_m.group(1)), int(date_m.group(2))
        y = int(date_m.group(3)) if date_m.group(3) else ref.year
        target_date = datetime(y, mo, d)
        if not date_m.group(3) and target_date.date() < ref.date():
            target_date = datetime(y + 1, mo, d)
        work = (work[:date_m.start()] + " " + work[date_m.end():]).strip()

    # 2. Relative day words ("завтра", "послезавтра", "сегодня", "tomorrow", "today")
    day_offset = None
    day_words = [
        (r"\b(послезавтра|day\s+after\s+tomorrow)\b", 2),
        (r"\b(завтра|tomorrow)\b", 1),
        (r"\b(сегодня|today)\b", 0),
    ]
    for pat, off in day_words:
        m = re.search(pat, work, re.IGNORECASE)
        if m:
            day_offset = off
            work = (work[:m.start()] + " " + work[m.end():]).strip()
            break

    # 3. Relative offset ("через 15 мин", "in 2 hours", "полчаса")
    rel_m = re.search(
        rf"(?:^|\s)(?:через|in)\s+((?:\d+(?:[.,]\d+)?\s*(?:{_UNIT_PATTERN})\s*)+|полчаса|полтора\s+часа)",
        work,
        re.IGNORECASE,
    )
    if rel_m:
        secs = parse_duration_seconds(rel_m.group(1))
        if secs:
            due = ref + timedelta(seconds=secs)
            title = (work[:rel_m.start()] + " " + work[rel_m.end():]).strip(" ,:-")
            return ParsedReminder(title=title or raw, due_at=due, repeat_rule=repeat_rule)

    # 4. Time of day ("в 18:30", "at 6:30 pm", "9am", "14:15")
    time_m = re.search(
        r"(?:(?:^|\s)(?:в|at|к)\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?(?=\s|$|[,.!?])",
        work,
        re.IGNORECASE,
    )
    if time_m and (time_m.group(2) is not None or time_m.group(3) is not None or re.search(r"(?:^|\s)(?:в|at|к)\s+" + re.escape(time_m.group(1)), work, re.IGNORECASE)):
        hh = int(time_m.group(1))
        mm = int(time_m.group(2) or 0)
        ampm = (time_m.group(3) or "").lower()
        if ampm == "pm" and hh < 12:
            hh += 12
        elif ampm == "am" and hh == 12:
            hh = 0
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            base = target_date or (ref + timedelta(days=day_offset or 0))
            due = base.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if target_date is None and day_offset is None and due <= ref:
                due += timedelta(days=1)
            title = (work[:time_m.start()] + " " + work[time_m.end():]).strip(" ,:-")
            title = re.sub(r"^(?:напомни(?:ть)?(?:\s+мне)?|remind\s+me(?:\s+to)?)\s+", "", title, flags=re.IGNORECASE).strip()
            return ParsedReminder(title=title or raw, due_at=due, repeat_rule=repeat_rule)

    # 5. Fallback: any duration tokens directly inside string
    secs = parse_duration_seconds(work)
    if secs:
        stripped = _DUR_TOKEN_RE.sub("", work).strip(" ,:-")
        stripped = re.sub(r"^(?:таймер(?:\s+на)?|timer(?:\s+for)?)\s+", "", stripped, flags=re.IGNORECASE).strip()
        return ParsedReminder(title=stripped or raw, due_at=ref + timedelta(seconds=secs), repeat_rule=repeat_rule)

    # 6. Fallback hourly repeat with no explicit time
    if repeat_rule == "hourly":
        title = re.sub(r"\s+", " ", work).strip(" ,:-") or raw
        return ParsedReminder(title=title, due_at=ref + timedelta(hours=1), repeat_rule=repeat_rule)

    return None


def next_occurrence(current_due: datetime, repeat_rule: str) -> datetime | None:
    """Compute next due datetime for repeating reminders."""
    if repeat_rule == "hourly":
        return current_due + timedelta(hours=1)
    if repeat_rule == "daily":
        return current_due + timedelta(days=1)
    if repeat_rule == "weekly":
        return current_due + timedelta(weeks=1)
    if repeat_rule == "weekdays":
        nxt = current_due + timedelta(days=1)
        while nxt.weekday() >= 5:
            nxt += timedelta(days=1)
        return nxt
    return None
