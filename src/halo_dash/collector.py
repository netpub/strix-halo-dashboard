from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

import httpx

from halo_dash.config import Config
from halo_dash.direct_store import DirectStore
from halo_dash.fixtures import fixture_snapshot
from halo_dash.host import HostReader, HostSample
from halo_dash.identity import read_host_identity
from halo_dash.litellm_db import LiteLLMReader
from halo_dash.llama import ServiceView, SlotView, poll_llama
from halo_dash.versions import fetch_litellm_version, fetch_llama_versions
from halo_dash.merge import LiveCall, compose_requests, find_spend_match

log = logging.getLogger(__name__)

_SERIES = (
    "decode_tps",
    "prefill_tps",
    "gpu",
    "gtt",
    "temp",
    "power",
    "cpu",
    "disk_read",
    "disk_write",
)
_GRACE = timedelta(seconds=20)
_RESUME = timedelta(seconds=5)
_SYSTEM_INTERVAL_S = 60.0


class Collector:
    def __init__(self, config: Config):
        self.config = config
        self.host = HostReader(config.sys_root, config.proc_root)
        self.direct: DirectStore | None = None
        self.litellm: LiteLLMReader | None = None
        self._lives: dict[tuple[str, int, int], LiveCall] = {}
        self._misses: dict[str, int] = {}
        self._finished: list[LiveCall] = []
        self._enrich: dict[str, LiveCall] = {}
        self._series: dict[str, list[float]] = {name: [] for name in _SERIES}
        self._last_spend = -1e9
        self._system_at = -1e9
        self._system = _empty_system()
        self._counted: set[str] = set()
        self._totals = {"requests": 0, "prompt_tokens": 0, "output_tokens": 0}
        self._step = 0
        self.snapshot = self._fixture_snapshot() if config.fixtures else self._blank()

    async def run(self) -> None:
        if self.config.fixtures:
            while True:
                self._step += 1
                self.snapshot = self._fixture_snapshot()
                await asyncio.sleep(self.config.poll_interval_s)
            return
        self.direct = DirectStore(self.config.direct_history_path, self.config.direct_history_limit)
        if self.config.litellm_database_url:
            self.litellm = LiteLLMReader(self.config.litellm_database_url)
        timeout = httpx.Timeout(2.5)
        async with httpx.AsyncClient(timeout=timeout) as client:
            while True:
                started = time.monotonic()
                try:
                    await self.tick(client)
                except Exception:
                    log.exception("Collector tick failed")
                elapsed = time.monotonic() - started
                await asyncio.sleep(max(0.05, self.config.poll_interval_s - elapsed))

    async def tick(self, client: httpx.AsyncClient) -> None:
        now_mono = time.monotonic()
        now = datetime.now(timezone.utc)
        host = self.host.sample(now_mono)
        groups = await asyncio.gather(*(poll_llama(client, backend) for backend in self.config.backends))
        polled = [pair for group in groups for pair in group]
        self._sync_lives(polled, now)
        if self.litellm is not None and now_mono - self._last_spend >= self.config.spend_poll_interval_s:
            await asyncio.to_thread(self.litellm.refresh)
            self._last_spend = now_mono
        spends = self.litellm.rows if self.litellm is not None else []
        self._resolve_finished(spends, now)
        directs = self.direct.recent() if self.direct is not None else []
        requests, hidden = compose_requests(
            list(self._lives.values()),
            self._finished,
            spends,
            directs,
            self._enrich,
        )
        if self.direct is not None:
            for row_id in hidden:
                self.direct.delete(row_id)
        self._count(requests)
        self._push_series(host, polled)
        if now_mono - self._system_at >= _SYSTEM_INTERVAL_S:
            self._system = await self._read_system(client)
            self._system_at = now_mono
        self.snapshot = self._snapshot(host, polled, requests, now)

    def _sync_lives(self, polled: list[tuple[ServiceView, list[SlotView]]], now: datetime) -> None:
        present: set[tuple[str, str]] = set()
        for service, slots in polled:
            pair = (service.name, service.model or "")
            if service.state == "error" and not slots:
                self._misses[service.name] = self._misses.get(service.name, 0) + 1
                if self._misses[service.name] < 3:
                    present.add(pair)
                    for identity in self._lives:
                        if identity[0] == service.name:
                            present.add((identity[0], identity[1]))
                    continue
            else:
                self._misses[service.name] = 0
            present.add(pair)
            seen: set[tuple[str, str, int, int]] = set()
            for slot in slots:
                if slot.status not in {"prefill", "generating"}:
                    continue
                task = slot.task_id if slot.task_id is not None else 0
                identity = (service.name, service.model or "", slot.slot_id, task)
                seen.add(identity)
                live = self._lives.get(identity) or self._take_finished(identity, now)
                if live is None:
                    live = LiveCall(
                        key=_live_key(service.name, service.model or "", slot.slot_id, task, now),
                        backend=service.name,
                        model=service.model,
                        model_candidates=_candidates(service),
                        prompt=slot.prompt,
                        prompt_tokens=slot.prompt_tokens,
                        output_tokens=slot.output_tokens,
                        started_at=now,
                        ended_at=None,
                        status=slot.status,
                        tps=_slot_tps(slot),
                    )
                else:
                    live.ended_at = None
                    live.deadline = None
                    live.status = slot.status
                    live.output_tokens = slot.output_tokens
                    live.prompt_tokens = slot.prompt_tokens or live.prompt_tokens
                    live.prompt = slot.prompt or live.prompt
                    live.tps = _slot_tps(slot)
                    live.model = service.model or live.model
                    live.model_candidates = _candidates(service)
                self._lives[identity] = live
            for identity, live in list(self._lives.items()):
                if (identity[0], identity[1]) != pair or identity in seen:
                    continue
                live.ended_at = now
                live.deadline = now + _GRACE
                self._finished.append(live)
                del self._lives[identity]
        for identity, live in list(self._lives.items()):
            if (identity[0], identity[1]) in present:
                continue
            live.ended_at = now
            live.deadline = now + _GRACE
            self._finished.append(live)
            del self._lives[identity]

    def _take_finished(self, identity: tuple[str, str, int, int], now: datetime) -> LiveCall | None:
        prefix = _live_key(identity[0], identity[1], identity[2], identity[3], now).rsplit("\n", 1)[0] + "\n"
        for index, live in enumerate(self._finished):
            if not live.key.startswith(prefix):
                continue
            if live.ended_at and now - live.ended_at <= _RESUME:
                return self._finished.pop(index)
        return None

    def _resolve_finished(self, spends, now: datetime) -> None:
        if self.direct is None:
            return
        keep: list[LiveCall] = []
        for live in self._finished:
            match = find_spend_match(live, spends)
            if match is not None:
                self._enrich[match.id] = live
                continue
            if live.deadline and now >= live.deadline:
                self.direct.upsert(
                    {
                        "id": live.key,
                        "backend": live.backend,
                        "model": live.model,
                        "prompt": live.prompt,
                        "prompt_tokens": live.prompt_tokens,
                        "output_tokens": live.output_tokens,
                        "started_at": live.started_at.isoformat(),
                        "ended_at": live.ended_at.isoformat() if live.ended_at else now.isoformat(),
                        "status": "done",
                        "tps": live.tps,
                    }
                )
                continue
            keep.append(live)
        self._finished = keep
        cutoff = now - timedelta(minutes=2)
        self._enrich = {
            key: live
            for key, live in self._enrich.items()
            if (live.ended_at or live.started_at) >= cutoff
        }

    def _count(self, requests: list[dict]) -> None:
        for row in requests:
            if row.get("provisional") or row["status"] not in {"done", "error"} or row["id"] in self._counted:
                continue
            self._counted.add(row["id"])
            self._totals["requests"] += 1
            self._totals["prompt_tokens"] += int(row.get("prompt_tokens") or 0)
            self._totals["output_tokens"] += int(row.get("output_tokens") or 0)

    def _push_series(self, host: HostSample, polled: list[tuple[ServiceView, list[SlotView]]]) -> None:
        primary = _primary([service for service, _slots in polled])
        gpu = host.gpu
        sample = {
            "decode_tps": primary.decode_tps if primary else 0,
            "prefill_tps": primary.prefill_tps if primary else 0,
            "gpu": gpu.busy_percent if gpu and gpu.busy_percent is not None else 0,
            "gtt": (gpu.gtt_used / 1_000_000_000) if gpu else 0,
            "temp": gpu.temp_c if gpu and gpu.temp_c is not None else 0,
            "power": gpu.power_w if gpu and gpu.power_w is not None else 0,
            "cpu": host.cpu_percent,
            "disk_read": host.disk_read_bps,
            "disk_write": host.disk_write_bps,
        }
        for name, value in sample.items():
            self._series[name].append(round(float(value or 0), 2))
            del self._series[name][:-120]

    def _snapshot(self, host: HostSample, polled, requests: list[dict], now: datetime) -> dict:
        services = [_service_dict(service) for service, _slots in polled]
        gpu = host.gpu
        return {
            "generated_at": now.isoformat(),
            "fixtures": False,
            "db_ok": self.litellm is None or self.litellm.error is None,
            "db_error": None if self.litellm is None else self.litellm.error,
            "overall": _overall(services),
            "language": self.config.language,
            "host": {
                "cpu_percent": round(host.cpu_percent, 1),
                "cpu_cores": host.cpu_cores,
                "cpu_threads": host.cpu_threads,
                "ram_used": host.ram_used,
                "ram_total": host.ram_total,
                "disk_read_bps": host.disk_read_bps,
                "disk_write_bps": host.disk_write_bps,
                "gpu": None
                if gpu is None
                else {
                    "name": gpu.name,
                    "busy_percent": gpu.busy_percent,
                    "gtt_used": gpu.gtt_used,
                    "gtt_total": gpu.gtt_total,
                    "vram_used": gpu.vram_used,
                    "vram_total": gpu.vram_total,
                    "temp_c": gpu.temp_c,
                    "power_w": gpu.power_w,
                    "gfx_clock_mhz": gpu.gfx_clock_mhz,
                },
            },
            "services": services,
            "series": {name: list(values) for name, values in self._series.items()},
            "requests": requests[:80],
            "totals": dict(self._totals),
            "system": self._system,
        }

    def _blank(self) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        return {
            "generated_at": now,
            "fixtures": False,
            "db_ok": True,
            "db_error": None,
            "overall": "idle",
            "language": self.config.language,
            "host": {
                "cpu_percent": 0,
                "cpu_cores": 0,
                "cpu_threads": 0,
                "ram_used": 0,
                "ram_total": 0,
                "disk_read_bps": 0,
                "disk_write_bps": 0,
                "gpu": None,
            },
            "services": [],
            "series": {name: [] for name in _SERIES},
            "requests": [],
            "totals": dict(self._totals),
            "system": _empty_system(),
        }

    def _fixture_snapshot(self) -> dict:
        snapshot = fixture_snapshot(self._step)
        snapshot["language"] = self.config.language
        return snapshot

    async def _read_system(self, client: httpx.AsyncClient) -> dict:
        identity = await asyncio.to_thread(read_host_identity)
        litellm_version = None
        llama_server: list[dict[str, str | None]] = []
        if self.config.litellm_url:
            litellm_version = await fetch_litellm_version(client, self.config.litellm_url)
        if self.config.backends:
            llama_server = await fetch_llama_versions(client, self.config.backends)
        return {
            "hostname": identity["hostname"],
            "ip": identity["ip"],
            "os": identity["os"],
            "litellm_version": litellm_version,
            "llama_server": llama_server,
        }


def _empty_system() -> dict:
    return {
        "hostname": None,
        "ip": None,
        "os": None,
        "litellm_version": None,
        "llama_server": [],
    }


def _live_key(name: str, model: str, slot_id: int, task: int, now: datetime) -> str:
    return "\n".join(["direct", name, model, str(slot_id), str(task), now.strftime("%H%M%S%f")])


def _candidates(service: ServiceView) -> list[str]:
    values = [service.model, service.configured_model, service.name, *service.aliases]
    return [value for value in values if value]


def _slot_tps(slot: SlotView) -> float | None:
    if slot.status == "generating":
        return slot.tps
    return slot.prefill_tps


def _service_dict(service: ServiceView) -> dict:
    return {
        "name": service.name,
        "url": service.url,
        "state": service.state,
        "model": service.model,
        "decode_tps": service.decode_tps,
        "prefill_tps": service.prefill_tps,
        "context_used": service.context_used,
        "context_total": service.context_total,
        "requests_processing": service.requests_processing,
        "requests_waiting": service.requests_waiting,
        "error": service.error,
        "prefill_progress": service.prefill_progress,
    }


def _primary(services: list[ServiceView]) -> ServiceView | None:
    for state in ("generating", "prefill", "queued", "idle", "error"):
        for service in services:
            if service.state == state:
                return service
    return None


def _overall(services: list[dict]) -> str:
    states = {service["state"] for service in services}
    for state in ("generating", "prefill", "queued", "error", "idle"):
        if state in states:
            return state
    return "idle"
