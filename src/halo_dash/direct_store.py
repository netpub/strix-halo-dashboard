from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


class DirectStore:
    def __init__(self, path: Path, limit: int = 200):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.limit = limit
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS direct_requests (
                id TEXT PRIMARY KEY,
                backend TEXT,
                model TEXT,
                prompt TEXT,
                prompt_tokens INTEGER,
                output_tokens INTEGER,
                started_at TEXT,
                ended_at TEXT,
                status TEXT,
                tps REAL
            )
            """
        )
        self.conn.commit()

    def upsert(self, row: dict) -> None:
        self.conn.execute(
            """
            INSERT INTO direct_requests (
                id, backend, model, prompt, prompt_tokens, output_tokens,
                started_at, ended_at, status, tps
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                output_tokens = excluded.output_tokens,
                ended_at = excluded.ended_at,
                status = excluded.status,
                tps = excluded.tps,
                prompt = excluded.prompt
            """,
            (
                row["id"],
                row.get("backend"),
                row.get("model"),
                row.get("prompt"),
                row.get("prompt_tokens") or 0,
                row.get("output_tokens") or 0,
                row.get("started_at"),
                row.get("ended_at"),
                row.get("status") or "done",
                row.get("tps"),
            ),
        )
        self.conn.execute(
            """
            DELETE FROM direct_requests
            WHERE id NOT IN (
                SELECT id FROM direct_requests ORDER BY started_at DESC LIMIT ?
            )
            """,
            (self.limit,),
        )
        self.conn.commit()

    def delete(self, row_id: str) -> None:
        self.conn.execute("DELETE FROM direct_requests WHERE id = ?", (row_id,))
        self.conn.commit()

    def recent(self, limit: int = 80) -> list[dict]:
        cursor = self.conn.execute(
            """
            SELECT id, backend, model, prompt, prompt_tokens, output_tokens,
                   started_at, ended_at, status, tps
            FROM direct_requests
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = []
        for item in cursor.fetchall():
            rows.append(
                {
                    "id": item["id"],
                    "started_at": item["started_at"],
                    "ended_at": item["ended_at"],
                    "status": item["status"] or "done",
                    "source": "direct",
                    "model": item["model"] or item["backend"],
                    "backend": item["backend"],
                    "prompt": item["prompt"] or "",
                    "user": None,
                    "prompt_tokens": item["prompt_tokens"] or 0,
                    "output_tokens": item["output_tokens"] or 0,
                    "tps": item["tps"],
                    "duration_s": _duration(item["started_at"], item["ended_at"]),
                }
            )
        return rows

    def close(self) -> None:
        self.conn.close()


def _duration(started: str | None, ended: str | None) -> float | None:
    if not started or not ended:
        return None
    try:
        return max(0.0, (datetime.fromisoformat(ended) - datetime.fromisoformat(started)).total_seconds())
    except ValueError:
        return None
