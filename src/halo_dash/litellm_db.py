from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from halo_dash.merge import SpendCall

log = logging.getLogger(__name__)

_WANTED = (
    "request_id",
    "model",
    "model_group",
    "status",
    "startTime",
    "endTime",
    "prompt_tokens",
    "completion_tokens",
    "metadata",
    "proxy_server_request",
    "messages",
    "user",
    "end_user",
)


class LiteLLMReader:
    """Reads LiteLLM_SpendLogs only. Writes nothing and does not keep a prompt copy past the poll."""

    def __init__(self, url: str):
        self.url = url
        self.engine = _engine(url)
        self.rows: list[SpendCall] = []
        self.error: str | None = None
        self._columns: set[str] | None = None

    def refresh(self, limit: int = 80) -> None:
        try:
            with self.engine.connect() as conn:
                if self._columns is None:
                    self._columns = _columns(conn, self.engine)
                statement = _select(self._columns, limit)
                result = conn.execute(statement)
                parsed = [_row_to_spend(dict(row._mapping)) for row in result]
            self.rows = [row for row in parsed if row is not None]
            self.error = None
        except Exception as exc:
            self.error = f"db_unreadable|{exc.__class__.__name__}: {exc}"
            log.warning("%s", self.error)


def _engine(url: str) -> Engine:
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://") :]
    elif url.startswith("postgresql://") and "+" not in url.split("://", 1)[0]:
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    if url.startswith("postgresql"):
        return create_engine(
            url,
            pool_pre_ping=True,
            pool_size=1,
            max_overflow=0,
            connect_args={"options": "-c default_transaction_read_only=on"},
        )
    return create_engine(url, connect_args={"check_same_thread": False})


def _columns(conn, engine: Engine) -> set[str]:
    if engine.dialect.name == "sqlite":
        found = conn.execute(text('PRAGMA table_info("LiteLLM_SpendLogs")')).fetchall()
        names = {row[1] for row in found}
    else:
        found = conn.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'LiteLLM_SpendLogs'
                """
            )
        ).fetchall()
        names = {row[0] for row in found}
    if "request_id" not in names or "startTime" not in names:
        raise RuntimeError("LiteLLM_SpendLogs table was not found")
    return names


def _select(columns: set[str], limit: int):
    chosen = [name for name in _WANTED if name in columns]
    quoted = ", ".join(f'"{name}"' for name in chosen)
    sql = (
        f'SELECT {quoted} FROM "LiteLLM_SpendLogs" '
        'ORDER BY "startTime" DESC LIMIT :limit'
    )
    return text(sql).bindparams(limit=limit)


def _row_to_spend(row: dict) -> SpendCall | None:
    request_id = row.get("request_id")
    started = _as_utc(row.get("startTime"))
    if not request_id or started is None:
        return None
    metadata = _as_json(row.get("metadata"))
    user = _user(metadata, row.get("user"), row.get("end_user"))
    prompt = _question(row.get("proxy_server_request"), row.get("messages"))
    status_raw = str(row.get("status") or "success").lower()
    status = "error" if status_raw in {"failure", "error", "failed"} else "done"
    return SpendCall(
        id=str(request_id),
        model=str(row.get("model") or ""),
        model_group=str(row.get("model_group") or ""),
        status=status,
        prompt=prompt,
        user=user,
        prompt_tokens=int(row.get("prompt_tokens") or 0),
        output_tokens=int(row.get("completion_tokens") or 0),
        started_at=started,
        ended_at=_as_utc(row.get("endTime")),
    )


def _user(metadata: object, user: object, end_user: object) -> str | None:
    if isinstance(metadata, dict):
        for key in ("user_api_key_alias", "user_api_key_team_alias", "user_api_key_user_id"):
            value = metadata.get(key)
            if value:
                return str(value)
        nested = metadata.get("spend_logs_metadata")
        if isinstance(nested, dict):
            for key in ("user", "alias"):
                if nested.get(key):
                    return str(nested[key])
    for value in (end_user, user):
        if value:
            return str(value)
    return None


def _question(proxy_server_request: object, messages: object) -> str:
    for source in (_messages_from_proxy(proxy_server_request), _as_json(messages), messages):
        text = _last_user_text(source)
        if text:
            return text
    return ""


def _messages_from_proxy(value: object) -> object:
    payload = _as_json(value)
    if not isinstance(payload, dict):
        return None
    body = payload.get("body") if isinstance(payload.get("body"), dict) else payload
    if isinstance(body, dict) and "messages" in body:
        return body.get("messages")
    return payload.get("messages")


def _last_user_text(messages: object) -> str:
    if isinstance(messages, dict):
        messages = messages.get("messages")
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        if str(message.get("role") or "") != "user":
            continue
        return _content_text(message.get("content"))
    return ""


def _content_text(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("text"):
                parts.append(str(item["text"]))
        return "\n".join(part.strip() for part in parts if part.strip())
    return ""


def _as_json(value: object) -> object:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return None


def _as_utc(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
