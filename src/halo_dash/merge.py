from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


_SPACE = re.compile(r"\s+")


@dataclass
class LiveCall:
    key: str
    backend: str
    model: str | None
    model_candidates: list[str]
    prompt: str
    prompt_tokens: int
    output_tokens: int
    started_at: datetime
    ended_at: datetime | None
    status: str
    tps: float | None
    deadline: datetime | None = None


@dataclass
class SpendCall:
    id: str
    model: str
    model_group: str
    status: str
    prompt: str
    user: str | None
    prompt_tokens: int
    output_tokens: int
    started_at: datetime
    ended_at: datetime | None


def normalize(text: str) -> str:
    return _SPACE.sub(" ", text or "").strip().lower()


def prompts_match(slot_prompt: str, question: str) -> bool:
    slot_n = normalize(slot_prompt)
    question_n = normalize(question)
    if len(slot_n) < 8 or len(question_n) < 8:
        return False
    short, long = (slot_n, question_n) if len(slot_n) <= len(question_n) else (question_n, slot_n)
    needle = short[:240].rstrip(".!? ")
    if needle in long:
        return True
    return len(needle) >= 24 and needle[:48] in long


def models_match(candidates: list[str], spend_model: str, spend_group: str) -> bool:
    left = {normalize(item) for item in candidates if item}
    right = {normalize(item) for item in (spend_model, spend_group) if item}
    if not left or not right:
        return True
    for item in left:
        for other in right:
            if item == other or item in other or other in item:
                return True
    return False


def times_overlap(
    slot_start: datetime,
    slot_end: datetime | None,
    spend_start: datetime,
    spend_end: datetime | None,
    slack: timedelta = timedelta(seconds=20),
) -> bool:
    end = slot_end or spend_end or slot_start
    spend_finish = spend_end or spend_start
    return slot_start - slack <= spend_finish and spend_start <= end + slack


def find_spend_match(live: LiveCall, spends: list[SpendCall]) -> SpendCall | None:
    overlapping = [
        spend
        for spend in spends
        if times_overlap(live.started_at, live.ended_at, spend.started_at, spend.ended_at)
        and models_match(live.model_candidates, spend.model, spend.model_group)
    ]
    if live.prompt:
        prompted = [spend for spend in overlapping if prompts_match(live.prompt, spend.prompt)]
        if prompted:
            overlapping = prompted
        elif len(normalize(live.prompt)) >= 8:
            return None
    if not overlapping:
        return None
    if len(overlapping) == 1:
        return overlapping[0]
    return min(overlapping, key=lambda spend: abs((spend.started_at - live.started_at).total_seconds()))


def compose_requests(
    lives: list[LiveCall],
    finished: list[LiveCall],
    spends: list[SpendCall],
    directs: list[dict],
    enrich: dict[str, LiveCall],
) -> tuple[list[dict], list[str]]:
    used: set[str] = set()
    rows: list[dict] = []
    for live in lives:
        match = find_spend_match(live, spends)
        if match is not None:
            used.add(match.id)
            rows.append(_from_spend(match, live, status=live.status))
        else:
            rows.append(_from_live(live, "direct"))
    for live in finished:
        match = find_spend_match(live, spends)
        if match is not None:
            used.add(match.id)
            rows.append(_from_spend(match, live, status="done" if match.status != "error" else "error"))
        else:
            rows.append(_from_live(live, "direct"))
    for spend in spends:
        if spend.id in used:
            continue
        extra = enrich.get(spend.id)
        rows.append(_from_spend(spend, extra, status=spend.status))
    direct_ids_hidden: list[str] = []
    for direct in directs:
        if _direct_already_shown(direct, rows):
            direct_ids_hidden.append(str(direct["id"]))
            continue
        rows.append(direct)
    ordered = sorted(rows, key=_started_at, reverse=True)
    return ordered, direct_ids_hidden


def _from_live(live: LiveCall, source: str) -> dict:
    ended = live.ended_at
    duration = None
    if ended is not None:
        duration = max(0.0, (ended - live.started_at).total_seconds())
    elif live.tps and live.output_tokens:
        duration = None
    return {
        "id": live.key,
        "started_at": live.started_at.isoformat(),
        "ended_at": ended.isoformat() if ended else None,
        "status": "done" if ended else live.status,
        "source": source,
        "model": live.model or live.backend,
        "backend": live.backend,
        "prompt": _clip(live.prompt),
        "user": None,
        "prompt_tokens": live.prompt_tokens,
        "output_tokens": live.output_tokens,
        "tps": live.tps,
        "duration_s": duration if ended else _running_duration(live.started_at),
        "provisional": bool(ended and live.deadline and live.deadline > datetime.now(timezone.utc)),
    }


def _from_spend(spend: SpendCall, live: LiveCall | None, status: str) -> dict:
    ended = spend.ended_at
    duration = None
    if ended is not None:
        duration = max(0.0, (ended - spend.started_at).total_seconds())
    tps = live.tps if live and live.tps else None
    if tps is None and duration and spend.output_tokens:
        tps = round(spend.output_tokens / duration, 2)
    return {
        "id": spend.id,
        "started_at": spend.started_at.isoformat(),
        "ended_at": ended.isoformat() if ended else None,
        "status": status,
        "source": "litellm",
        "model": spend.model or spend.model_group,
        "backend": live.backend if live else None,
        "prompt": _clip(spend.prompt or (live.prompt if live else "")),
        "user": spend.user,
        "prompt_tokens": spend.prompt_tokens or (live.prompt_tokens if live else 0),
        "output_tokens": spend.output_tokens if spend.output_tokens or live is None else max(spend.output_tokens, live.output_tokens),
        "tps": tps,
        "duration_s": duration if duration is not None else ( _running_duration(spend.started_at) if status in {"prefill", "generating"} else None),
    }


def _direct_already_shown(direct: dict, rows: list[dict]) -> bool:
    prompt = str(direct.get("prompt") or "")
    started = _parse_dt(str(direct.get("started_at") or ""))
    for row in rows:
        if row.get("source") != "litellm":
            continue
        other = _parse_dt(str(row.get("started_at") or ""))
        if started and other and abs((started - other).total_seconds()) > 120:
            continue
        if prompts_match(prompt, str(row.get("prompt") or "")) or prompts_match(str(row.get("prompt") or ""), prompt):
            return True
    return False


def _started_at(row: dict) -> datetime:
    return _parse_dt(str(row.get("started_at") or "")) or datetime.min.replace(tzinfo=timezone.utc)


def _clip(text: str) -> str:
    cleaned = text.replace("\x00", "").strip()
    if len(cleaned) <= 2000:
        return cleaned
    return cleaned[:2000] + "…"


def _running_duration(started: datetime) -> float:
    return max(0.0, (datetime.now(timezone.utc) - started).total_seconds())


def _parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
