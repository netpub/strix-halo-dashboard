from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from halo_dash.config import Backend
from halo_dash.textio import parse_prometheus


@dataclass
class SlotView:
    slot_id: int
    task_id: int | None
    status: str
    prompt: str
    prompt_tokens: int
    prompt_processed: int
    output_tokens: int
    n_ctx: int
    tps: float | None
    prefill_tps: float | None
    prefill_progress: float | None


@dataclass
class ServiceView:
    name: str
    url: str
    state: str
    model: str | None
    configured_model: str | None
    decode_tps: float
    prefill_tps: float
    context_used: int
    context_total: int
    requests_processing: int
    requests_waiting: int
    error: str | None
    prefill_progress: float | None
    aliases: list[str] = field(default_factory=list)


@dataclass
class RouterModel:
    name: str
    status: str
    aliases: list[str]


async def poll_llama(client: httpx.AsyncClient, backend: Backend) -> list[tuple[ServiceView, list[SlotView]]]:
    base = backend.url.rstrip("/")
    try:
        health = await client.get(f"{base}/health")
    except httpx.HTTPError as exc:
        return [(_down(backend, f"llama_unreachable|{exc.__class__.__name__}"), [])]
    if health.status_code >= 500:
        return [(_down(backend, f"llama_health|{health.status_code}"), [])]

    models_resp = await _get(client, f"{base}/v1/models")
    router_models = router_models_from_response(models_resp)
    if router_models is not None:
        return await _poll_router(client, backend, base, router_models)

    slots_resp, metrics_resp = await _gather_optional(client, [f"{base}/slots", f"{base}/metrics"])
    slots, slot_error = _slots_from_response(slots_resp)
    metrics = _metrics_from_response(metrics_resp)
    model = _model_from_response(models_resp) or backend.model
    service = service_from_parts(backend, slots, metrics, model, slot_error)
    return [(service, slots)]


async def _poll_router(
    client: httpx.AsyncClient,
    backend: Backend,
    base: str,
    models: list[RouterModel],
) -> list[tuple[ServiceView, list[SlotView]]]:
    loaded = [model for model in models if model.status == "loaded"]
    loading = [model for model in models if model.status == "loading"]
    if not loaded and not loading:
        return [(service_from_parts(backend, [], {}, None, None), [])]

    async def one_loaded(model: RouterModel) -> tuple[ServiceView, list[SlotView]]:
        params = {"model": model.name, "autoload": "false"}
        slots_resp, metrics_resp = await _gather_optional(
            client,
            [f"{base}/slots", f"{base}/metrics"],
            params=params,
        )
        if _is_not_loaded(slots_resp):
            slots, slot_error = [], None
            metrics: dict[str, float] = {}
        else:
            slots, slot_error = _slots_from_response(slots_resp)
            metrics = _metrics_from_response(metrics_resp)
        service = service_from_parts(backend, slots, metrics, model.name, slot_error)
        service.aliases = list(model.aliases)
        return service, slots

    loaded_rows = list(await _gather_models(loaded, one_loaded)) if loaded else []
    rows = loaded_rows + [_loading_service(backend, model) for model in loading]
    if len(rows) == 1:
        return rows
    named: list[tuple[ServiceView, list[SlotView]]] = []
    for service, slots in rows:
        service.name = service.model or service.name
        named.append((service, slots))
    return named


def service_from_parts(
    backend: Backend,
    slots: list[SlotView],
    metrics: dict[str, float],
    model: str | None,
    slot_error: str | None,
) -> ServiceView:
    active = [slot for slot in slots if slot.status in {"prefill", "generating"}]
    processing = int(metrics.get("llamacpp:requests_processing") or len(active))
    waiting = int(metrics.get("llamacpp:requests_deferred") or 0)
    decode_values = [slot.tps for slot in active if slot.tps]
    prefill_values = [slot.prefill_tps for slot in active if slot.prefill_tps]
    decode = max(decode_values) if decode_values else float(metrics.get("llamacpp:predicted_tokens_seconds") or 0)
    prefill = max(prefill_values) if prefill_values else float(metrics.get("llamacpp:prompt_tokens_seconds") or 0)
    context_total = max((slot.n_ctx for slot in slots if slot.n_ctx), default=0)
    ratio = metrics.get("llamacpp:kv_cache_usage_ratio")
    kv_tokens = int(metrics.get("llamacpp:kv_cache_tokens") or 0)
    if ratio is not None and context_total:
        context_used = int(ratio * context_total)
    elif kv_tokens:
        context_used = kv_tokens
    else:
        context_used = max((slot.prompt_processed + slot.output_tokens for slot in active), default=0)
        if context_total and context_used > context_total:
            context_used = context_total
    progress_values = [slot.prefill_progress for slot in active if slot.prefill_progress is not None]
    progress = max(progress_values) if progress_values else None
    if any(slot.status == "generating" for slot in active) or (processing > 0 and not active and decode > 0):
        state = "generating"
    elif any(slot.status == "prefill" for slot in active) or (processing > 0 and not active):
        state = "prefill"
    elif waiting > 0:
        state = "queued"
    else:
        state = "idle"
    if not active and processing == 0:
        decode = float(metrics.get("llamacpp:predicted_tokens_seconds") or 0)
        prefill = float(metrics.get("llamacpp:prompt_tokens_seconds") or 0)
    return ServiceView(
        name=backend.name,
        url=backend.url,
        state=state,
        model=model,
        configured_model=backend.model,
        decode_tps=round(decode, 2),
        prefill_tps=round(prefill, 2),
        context_used=context_used,
        context_total=context_total,
        requests_processing=processing,
        requests_waiting=waiting,
        error=slot_error,
        prefill_progress=progress,
    )


def parse_slots(payload: object) -> list[SlotView]:
    rows = payload
    if isinstance(payload, dict):
        rows = payload.get("slots") or payload.get("data") or []
    if not isinstance(rows, list):
        return []
    slots: list[SlotView] = []
    for row in rows:
        if isinstance(row, dict):
            slots.append(_one_slot(row))
    return slots


def _one_slot(row: dict) -> SlotView:
    next_token = row.get("next_token") or []
    decoded = 0
    if isinstance(next_token, list) and next_token and isinstance(next_token[0], dict):
        decoded = int(next_token[0].get("n_decoded") or 0)
    decoded = int(row.get("n_decoded") or row.get("tg_tokens_generated") or decoded or 0)
    prompt_tokens = int(row.get("n_prompt_tokens") or 0)
    processed = int(row.get("n_prompt_tokens_processed") or 0)
    processing = bool(row.get("is_processing"))
    state_name = str(row.get("state_name") or "").lower()
    if not processing and state_name in {"", "idle"}:
        status = "idle"
    elif state_name in {"generating", "prefill", "reading"}:
        status = "prefill" if state_name in {"prefill", "reading"} else "generating"
    elif processing and decoded > 0 and processed >= prompt_tokens:
        status = "generating"
    elif processing:
        status = "prefill"
    else:
        status = "idle"
    progress = row.get("pp_progress")
    if progress is None and prompt_tokens:
        progress = min(1.0, processed / prompt_tokens)
    tps = _optional_float(row.get("tg_tps"))
    prefill_tps = _optional_float(row.get("pp_tps"))
    task = row.get("id_task")
    return SlotView(
        slot_id=int(row.get("id") or 0),
        task_id=int(task) if task is not None else None,
        status=status,
        prompt=str(row.get("prompt") or ""),
        prompt_tokens=prompt_tokens,
        prompt_processed=processed,
        output_tokens=decoded,
        n_ctx=int(row.get("n_ctx") or 0),
        tps=tps,
        prefill_tps=prefill_tps,
        prefill_progress=float(progress) if progress is not None else None,
    )


def _slots_from_response(response: object) -> tuple[list[SlotView], str | None]:
    if isinstance(response, Exception):
        return [], f"slots_unreachable|{response.__class__.__name__}"
    if not isinstance(response, httpx.Response):
        return [], "slots_missing"
    if response.status_code == 404 or response.status_code == 501:
        return [], "slots_disabled"
    if response.status_code >= 400:
        return [], f"slots_status|{response.status_code}"
    try:
        return parse_slots(response.json()), None
    except ValueError:
        return [], "slots_json"


def _metrics_from_response(response: object) -> dict[str, float]:
    if isinstance(response, httpx.Response) and response.status_code < 400:
        return parse_prometheus(response.text)
    return {}


def router_models_from_response(response: object) -> list[RouterModel] | None:
    if not isinstance(response, httpx.Response) or response.status_code >= 400:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list) or not data:
        return None
    if not any(isinstance(item, dict) and isinstance(item.get("status"), dict) for item in data):
        return None
    models: list[RouterModel] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = str(item.get("id") or item.get("name") or "")
        if not name:
            continue
        status = item.get("status")
        value = str(status.get("value") or "") if isinstance(status, dict) else str(status or "")
        aliases = [str(alias) for alias in item.get("aliases") or [] if alias]
        models.append(RouterModel(name=name, status=value, aliases=aliases))
    return models


def _loading_service(backend: Backend, model: RouterModel) -> tuple[ServiceView, list[SlotView]]:
    service = service_from_parts(backend, [], {}, model.name, None)
    service.state = "queued"
    service.aliases = list(model.aliases)
    return service, []


def _is_not_loaded(response: object) -> bool:
    if not isinstance(response, httpx.Response) or response.status_code != 400:
        return False
    try:
        message = str(response.json()["error"]["message"])
    except (ValueError, KeyError, TypeError):
        return False
    return "not loaded" in message.lower()


async def _gather_models(models: list[RouterModel], one) -> list[tuple[ServiceView, list[SlotView]]]:
    import asyncio

    return list(await asyncio.gather(*(one(model) for model in models)))


def _model_from_response(response: object) -> str | None:
    if not isinstance(response, httpx.Response) or response.status_code >= 400:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list) and data and isinstance(data[0], dict):
        model_id = data[0].get("id")
        if model_id:
            return str(model_id)
    return None


async def _get(client: httpx.AsyncClient, url: str, params: dict[str, str] | None = None) -> object:
    try:
        return await client.get(url, params=params)
    except httpx.HTTPError as exc:
        return exc


async def _gather_optional(
    client: httpx.AsyncClient,
    urls: list[str],
    params: dict[str, str] | None = None,
) -> list[object]:
    import asyncio

    return list(await asyncio.gather(*(_get(client, url, params) for url in urls)))


def _down(backend: Backend, message: str) -> ServiceView:
    return ServiceView(
        name=backend.name,
        url=backend.url,
        state="error",
        model=backend.model,
        configured_model=backend.model,
        decode_tps=0,
        prefill_tps=0,
        context_used=0,
        context_total=0,
        requests_processing=0,
        requests_waiting=0,
        error=message,
        prefill_progress=None,
    )


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
