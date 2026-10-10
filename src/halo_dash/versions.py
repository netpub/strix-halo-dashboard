from __future__ import annotations

import asyncio

import httpx

from halo_dash.config import Backend
from halo_dash.llama import _get


async def fetch_litellm_version(client: httpx.AsyncClient, base: str) -> str | None:
    root = base.rstrip("/")
    direct = await _get(client, f"{root}/version")
    version = version_from_payload(direct)
    if version:
        return version
    if isinstance(direct, httpx.Response) and direct.status_code not in {404, 405}:
        return None
    return version_from_openapi(await _get(client, f"{root}/openapi.json"))


async def fetch_llama_versions(client: httpx.AsyncClient, backends: list[Backend]) -> list[dict[str, str | None]]:
    async def one(backend: Backend) -> dict[str, str | None]:
        response = await _get(client, f"{backend.url.rstrip('/')}/props")
        return {"name": backend.name, "version": build_info_from_props(response)}

    return list(await asyncio.gather(*(one(backend) for backend in backends)))


def version_from_payload(response: object) -> str | None:
    payload = _json(response)
    if payload is None:
        return None
    for key in ("version", "litellm_version"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip() and value.strip().lower() != "unknown":
            return value.strip()
    return None


def version_from_openapi(response: object) -> str | None:
    payload = _json(response)
    info = payload.get("info") if payload else None
    if not isinstance(info, dict):
        return None
    value = info.get("version")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def build_info_from_props(response: object) -> str | None:
    payload = _json(response)
    if payload is None:
        return None
    value = payload.get("build_info")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _json(response: object) -> dict | None:
    if not isinstance(response, httpx.Response) or response.status_code >= 400:
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None
