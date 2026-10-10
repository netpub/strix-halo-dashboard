from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone


def fixture_snapshot(step: int) -> dict:
    angle = step / 6
    decode = 48 + 8 * math.sin(angle)
    prefill = 820 + 90 * math.sin(angle / 2)
    gpu = 72 + 22 * math.sin(angle / 1.4)
    cpu = 28 + 18 * math.sin(angle / 1.7)
    gtt_used = int((30 + 6 * math.sin(angle / 3)) * 1_000_000_000)
    temp = 54 + 4 * math.sin(angle / 2)
    power = 78 + 12 * math.sin(angle)
    disk_read = max(0.0, 1_200_000 + 900_000 * math.sin(angle * 1.3))
    disk_write = max(0.0, 280_000 + 180_000 * math.sin(angle * 0.7))
    output = 24 + step * 4
    started = datetime(2026, 10, 10, 12, 14, 7, tzinfo=timezone.utc) 
    active_started = datetime.now(timezone.utc) - timedelta(seconds=8 + step)
    series = {
        "decode_tps": _wave(step, decode, 6),
        "prefill_tps": _wave(step, prefill, 40),
        "gpu": _wave(step, gpu, 4),
        "gtt": _wave(step, gtt_used / 1_000_000_000, 0.4),
        "temp": _wave(step, temp, 0.6),
        "power": _wave(step, power, 2),
        "cpu": _wave(step, cpu, 3),
        "disk_read": _wave(step, disk_read, 200_000),
        "disk_write": _wave(step, disk_write, 40_000),
    }
    services = [
        {
            "name": "qwen",
            "url": "http://127.0.0.1:8080",
            "state": "generating",
            "model": "qwen3-30b",
            "decode_tps": round(decode, 1),
            "prefill_tps": round(prefill, 1),
            "context_used": 4600,
            "context_total": 32768,
            "requests_processing": 1,
            "requests_waiting": 0,
            "error": None,
            "prefill_progress": 1,
        },
        {
            "name": "codestral",
            "url": "http://127.0.0.1:8081",
            "state": "idle",
            "model": "codestral",
            "decode_tps": 0,
            "prefill_tps": 0,
            "context_used": 0,
            "context_total": 32768,
            "requests_processing": 0,
            "requests_waiting": 0,
            "error": None,
            "prefill_progress": None,
        },
    ]
    requests = [
        {
            "id": "live-qwen",
            "started_at": active_started.isoformat(),
            "ended_at": None,
            "status": "generating",
            "source": "direct",
            "model": "qwen3-30b",
            "backend": "qwen",
            "prompt": "Wie hoch ist die GPU-Last gerade, und welches Modell antwortet?",
            "user": None,
            "prompt_tokens": 2476,
            "output_tokens": output,
            "tps": round(decode, 1),
            "duration_s": 8 + step,
        },
        {
            "id": "spend-1",
            "started_at": started.isoformat(),
            "ended_at": (started + timedelta(seconds=70.9)).isoformat(),
            "status": "done",
            "source": "litellm",
            "model": "qwen3-30b",
            "backend": "qwen",
            "prompt": "Fasse das Meeting von gestern in fünf Punkten zusammen.",
            "user": "team-api",
            "prompt_tokens": 8284,
            "output_tokens": 2707,
            "tps": 45.2,
            "duration_s": 70.9,
        },
        {
            "id": "spend-2",
            "started_at": (started - timedelta(minutes=9)).isoformat(),
            "ended_at": (started - timedelta(minutes=9) + timedelta(seconds=386.3)).isoformat(),
            "status": "done",
            "source": "litellm",
            "model": "codestral",
            "backend": "codestral",
            "prompt": "Schreibe einen systemd-Unit für den llama-server auf dem Strix Halo.",
            "user": "anna",
            "prompt_tokens": 13700,
            "output_tokens": 19060,
            "tps": 51.0,
            "duration_s": 386.3,
        },
        {
            "id": "direct-1",
            "started_at": (started - timedelta(minutes=20)).isoformat(),
            "ended_at": (started - timedelta(minutes=20) + timedelta(seconds=42)).isoformat(),
            "status": "done",
            "source": "direct",
            "model": "qwen3-30b",
            "backend": "qwen",
            "prompt": "Erkläre GTT auf Strix Halo in zwei Sätzen.",
            "user": None,
            "prompt_tokens": 156,
            "output_tokens": 420,
            "tps": 47.1,
            "duration_s": 42,
        },
    ]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fixtures": True,
        "db_ok": True,
        "db_error": None,
        "overall": "generating",
        "language": "en",
        "host": {
            "cpu_percent": round(cpu, 1),
            "cpu_cores": 16,
            "cpu_threads": 32,
            "ram_used": 48_000_000_000,
            "ram_total": 128_000_000_000,
            "disk_read_bps": disk_read,
            "disk_write_bps": disk_write,
            "gpu": {
                "name": "AMD Radeon 8060S",
                "busy_percent": round(gpu, 1),
                "gtt_used": gtt_used,
                "gtt_total": 128_000_000_000,
                "vram_used": 160_000_000,
                "vram_total": 1_000_000_000,
                "temp_c": round(temp, 1),
                "power_w": round(power, 1),
                "gfx_clock_mhz": 2400,
            },
        },
        "services": services,
        "series": series,
        "requests": requests,
        "system": {
            "hostname": "aihost01",
            "ip": "192.168.50.21",
            "os": "Fedora Linux 42",
            "litellm_version": "1.82.3",
            "llama_server": [
                {"name": "router", "version": "b8771"},
                {"name": "vision", "version": "b8771"},
                {"name": "embed", "version": "b8120"},
            ],
        },
        "totals": {
            "requests": 4,
            "prompt_tokens": 2476 + 8284 + 13700 + 156,
            "output_tokens": output + 2707 + 19060 + 420,
        },
    }


def _wave(step: int, current: float, amplitude: float) -> list[float]:
    points = []
    length = min(120, step + 1)
    for index in range(length):
        past = index - (length - 1)
        points.append(round(current + amplitude * math.sin((step + past) / 6), 2))
    return points
