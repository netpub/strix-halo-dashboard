from datetime import datetime, timedelta, timezone
from pathlib import Path

from halo_dash.config import Backend
from halo_dash.host import HostReader, find_gpu_card
from halo_dash.llama import parse_slots, service_from_parts
from halo_dash.litellm_db import LiteLLMReader
from halo_dash.merge import LiveCall, SpendCall, compose_requests, find_spend_match
from halo_dash.textio import parse_prometheus


def test_prometheus_gauges_take_the_max():
    payload = """
# HELP llamacpp:predicted_tokens_seconds generation
llamacpp:predicted_tokens_seconds 40
llamacpp:requests_processing 1
llamacpp:requests_deferred 2
llamacpp:kv_cache_usage_ratio 0.25
"""
    parsed = parse_prometheus(payload)
    assert parsed["llamacpp:predicted_tokens_seconds"] == 40
    assert parsed["llamacpp:requests_processing"] == 1
    assert parsed["llamacpp:kv_cache_usage_ratio"] == 0.25


def test_parse_generating_slot():
    slots = parse_slots(
        [
            {
                "id": 0,
                "n_ctx": 32768,
                "is_processing": True,
                "id_task": 7,
                "n_prompt_tokens": 100,
                "n_prompt_tokens_processed": 100,
                "prompt": "Hallo Welt, wie geht es dir heute?",
                "next_token": [{"n_decoded": 12, "has_next_token": True}],
                "tg_tps": 51.6,
                "pp_tps": 800,
                "pp_progress": 1,
            }
        ]
    )
    assert slots[0].status == "generating"
    assert slots[0].output_tokens == 12
    assert slots[0].tps == 51.6
    service = service_from_parts(Backend("qwen", "http://127.0.0.1:8080", "qwen"), slots, {}, "qwen", None)
    assert service.state == "generating"
    assert service.context_total == 32768
    assert service.decode_tps == 51.6


def test_host_reads_sysfs_and_proc(tmp_path: Path):
    proc = tmp_path / "proc"
    sys_root = tmp_path / "sys"
    proc.mkdir()
    (proc / "stat").write_text("cpu  100 0 50 800 50 0 0 0 0\ncpu0 100 0 50 800 50 0 0 0 0\n", encoding="utf-8")
    (proc / "meminfo").write_text("MemTotal: 1000000 kB\nMemAvailable: 250000 kB\n", encoding="utf-8")
    (proc / "diskstats").write_text(" 259 0 nvme0n1 1 0 100 0 1 0 50 0 0 0 0 0 0 0 0\n", encoding="utf-8")
    (proc / "cpuinfo").write_text(
        "processor : 0\nphysical id : 0\ncore id : 0\nprocessor : 1\nphysical id : 0\ncore id : 1\n",
        encoding="utf-8",
    )
    other = sys_root / "class" / "drm" / "card0" / "device"
    amd = sys_root / "class" / "drm" / "card1" / "device"
    other.mkdir(parents=True)
    amd.mkdir(parents=True)
    (other / "gpu_busy_percent").write_text("1\n", encoding="utf-8")
    (other / "vendor").write_text("0x1234\n", encoding="utf-8")
    (amd / "gpu_busy_percent").write_text("64\n", encoding="utf-8")
    (amd / "vendor").write_text("0x1002\n", encoding="utf-8")
    (amd / "product_name").write_text("AMD Radeon 8060S\n", encoding="utf-8")
    (amd / "mem_info_gtt_used").write_text("1000\n", encoding="utf-8")
    (amd / "mem_info_gtt_total").write_text("5000\n", encoding="utf-8")
    (amd / "mem_info_vram_used").write_text("10\n", encoding="utf-8")
    (amd / "mem_info_vram_total").write_text("20\n", encoding="utf-8")
    hwmon = amd / "hwmon" / "hwmon0"
    hwmon.mkdir(parents=True)
    (hwmon / "temp1_label").write_text("edge\n", encoding="utf-8")
    (hwmon / "temp1_input").write_text("56000\n", encoding="utf-8")
    (hwmon / "power1_average").write_text("85000000\n", encoding="utf-8")
    (hwmon / "freq1_input").write_text("2400000000\n", encoding="utf-8")

    assert find_gpu_card(sys_root).name == "card1"
    reader = HostReader(sys_root, proc)
    first = reader.sample(1.0)
    assert first.cpu_percent == 0
    assert first.ram_total == 1000000 * 1024
    assert first.ram_used == 750000 * 1024
    assert first.gpu is not None
    assert first.gpu.name == "AMD Radeon 8060S"
    assert first.gpu.busy_percent == 64
    assert first.gpu.temp_c == 56
    assert first.gpu.power_w == 85
    assert first.gpu.gfx_clock_mhz == 2400
    assert first.cpu_threads == 2
    (proc / "stat").write_text("cpu  200 0 100 850 50 0 0 0 0\n", encoding="utf-8")
    (proc / "diskstats").write_text(" 259 0 nvme0n1 1 0 1100 0 1 0 1050 0 0 0 0 0 0 0 0\n", encoding="utf-8")
    second = reader.sample(2.0)
    assert second.cpu_percent == 75
    assert second.disk_read_bps == 1000 * 512
    assert second.disk_write_bps == 1000 * 512


def test_spend_logs_and_merge(tmp_path: Path):
    database = tmp_path / "litellm.db"
    import sqlite3

    conn = sqlite3.connect(database)
    conn.execute(
        """
        CREATE TABLE "LiteLLM_SpendLogs" (
            request_id TEXT PRIMARY KEY,
            model TEXT,
            model_group TEXT,
            status TEXT,
            startTime TEXT,
            endTime TEXT,
            prompt_tokens INTEGER,
            completion_tokens INTEGER,
            metadata TEXT,
            proxy_server_request TEXT,
            messages TEXT,
            user TEXT,
            end_user TEXT
        )
        """
    )
    started = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    conn.execute(
        """
        INSERT INTO "LiteLLM_SpendLogs" (
            request_id, model, model_group, status, startTime, endTime,
            prompt_tokens, completion_tokens, metadata, proxy_server_request, messages, user, end_user
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "req-1",
            "qwen3-30b",
            "qwen",
            "success",
            started.isoformat(),
            (started + timedelta(seconds=12)).isoformat(),
            120,
            40,
            '{"user_api_key_alias": "team-api"}',
            '{"body": {"messages": [{"role": "user", "content": "Wie hoch ist die GPU-Last gerade?"}]}}',
            None,
            "raw-user",
            None,
        ),
    )
    conn.commit()
    conn.close()

    reader = LiteLLMReader("sqlite:///" + database.as_posix())
    reader.refresh()
    assert reader.error is None
    assert reader.rows[0].prompt == "Wie hoch ist die GPU-Last gerade?"
    assert reader.rows[0].user == "team-api"
    assert reader.rows[0].output_tokens == 40

    live = LiveCall(
        key="direct:qwen:0:7:120000",
        backend="qwen",
        model="qwen3-30b",
        model_candidates=["qwen3-30b", "qwen"],
        prompt="<|im_start|>user\nWie hoch ist die GPU-Last gerade?\n<|im_end|>",
        prompt_tokens=120,
        output_tokens=40,
        started_at=started,
        ended_at=started + timedelta(seconds=12),
        status="generating",
        tps=51.6,
    )
    assert find_spend_match(live, reader.rows).id == "req-1"
    rows, hidden = compose_requests([live], [], reader.rows, [], {})
    assert len(rows) == 1
    assert rows[0]["source"] == "litellm"
    assert rows[0]["user"] == "team-api"
    assert rows[0]["tps"] == 51.6
    assert hidden == []


def test_direct_row_hides_when_spend_log_matches():
    started = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    spend = SpendCall(
        id="req-9",
        model="codestral",
        model_group="codestral",
        status="done",
        prompt="Schreibe einen systemd-Unit für den llama-server.",
        user="anna",
        prompt_tokens=80,
        output_tokens=20,
        started_at=started,
        ended_at=started + timedelta(seconds=5),
    )
    direct = {
        "id": "direct-9",
        "started_at": started.isoformat(),
        "ended_at": (started + timedelta(seconds=5)).isoformat(),
        "status": "done",
        "source": "direct",
        "model": "codestral",
        "backend": "codestral",
        "prompt": "Schreibe einen systemd-Unit für den llama-server auf diesem Host.",
        "user": None,
        "prompt_tokens": 80,
        "output_tokens": 20,
        "tps": 10,
        "duration_s": 5,
    }
    rows, hidden = compose_requests([], [], [spend], [direct], {})
    assert [row["id"] for row in rows] == ["req-9"]
    assert hidden == ["direct-9"]


def test_compose_requests_orders_by_start_time_not_status():
    older = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    newer = older + timedelta(minutes=5)
    error = SpendCall(
        id="old-error",
        model="qwen",
        model_group="qwen",
        status="error",
        prompt="Diese Anfrage ist fehlgeschlagen und älter.",
        user=None,
        prompt_tokens=10,
        output_tokens=0,
        started_at=older,
        ended_at=older + timedelta(seconds=1),
    )
    done = SpendCall(
        id="new-done",
        model="qwen",
        model_group="qwen",
        status="done",
        prompt="Diese Anfrage ist neuer und erfolgreich.",
        user=None,
        prompt_tokens=12,
        output_tokens=4,
        started_at=newer,
        ended_at=newer + timedelta(seconds=2),
    )
    rows, hidden = compose_requests([], [], [error, done], [], {})
    assert [row["id"] for row in rows] == ["new-done", "old-error"]
    assert hidden == []


def test_router_queries_only_loaded_models_without_autoload():
    import asyncio

    import httpx

    from halo_dash.llama import poll_llama

    seen: list[tuple[str, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, dict(request.url.params)))
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/models":
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "data": [
                        {"id": "loaded-model", "aliases": ["qwen3-coder-30b"], "status": {"value": "loaded"}},
                        {"id": "sleeping-model", "status": {"value": "sleeping"}},
                        {"id": "cold-model", "status": {"value": "unloaded"}},
                        {"id": "booting-model", "status": {"value": "loading"}},
                    ],
                },
            )
        if request.url.path == "/slots":
            return httpx.Response(
                200,
                json=[{"id": 0, "n_ctx": 8192, "is_processing": True, "id_task": 3, "n_prompt_tokens": 20, "n_prompt_tokens_processed": 20, "prompt": "Hallo Welt, das ist ein Test.", "next_token": [{"n_decoded": 4}], "tg_tps": 40}],
            )
        if request.url.path == "/metrics":
            return httpx.Response(200, text="llamacpp:requests_processing 1\nllamacpp:predicted_tokens_seconds 40\n")
        return httpx.Response(404)

    async def run() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            rows = await poll_llama(client, Backend("router", "http://router.example"))
        assert [row[0].model for row in rows] == ["loaded-model", "booting-model"]
        assert rows[0][0].name == "loaded-model"
        assert rows[0][0].state == "generating"
        assert rows[0][0].aliases == ["qwen3-coder-30b"]
        assert rows[1][0].state == "queued"
        slot_calls = [params for path, params in seen if path == "/slots"]
        metric_calls = [params for path, params in seen if path == "/metrics"]
        assert slot_calls == [{"model": "loaded-model", "autoload": "false"}]
        assert metric_calls == [{"model": "loaded-model", "autoload": "false"}]

    asyncio.run(run())


def test_single_server_does_not_send_model_param():
    import asyncio

    import httpx

    from halo_dash.llama import poll_llama

    seen: list[tuple[str, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, dict(request.url.params)))
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"object": "list", "data": [{"id": "vision-model", "object": "model"}]})
        if request.url.path == "/slots":
            return httpx.Response(200, json=[{"id": 0, "is_processing": False, "n_ctx": 4096}])
        if request.url.path == "/metrics":
            return httpx.Response(200, text="")
        return httpx.Response(404)

    async def run() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            rows = await poll_llama(client, Backend("vision", "http://vision.example"))
        assert len(rows) == 1
        assert rows[0][0].name == "vision"
        assert rows[0][0].model == "vision-model"
        assert rows[0][0].state == "idle"
        assert seen == [
            ("/health", {}),
            ("/v1/models", {}),
            ("/slots", {}),
            ("/metrics", {}),
        ]

    asyncio.run(run())


def test_host_identity_reads_mounted_os_release(tmp_path: Path, monkeypatch):
    mounted = tmp_path / "mounted"
    proc = tmp_path / "proc"
    image = tmp_path / "image"
    for directory in (mounted, proc, image):
        directory.mkdir()
    (mounted / "hostname").write_text("aihost01\n")
    (mounted / "os-release").write_text('PRETTY_NAME="Fedora Linux 42 (Server Edition)"\n')
    (image / "hostname").write_text("halo-dash\n")
    (image / "os-release").write_text('PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\n')
    monkeypatch.setattr(
        "halo_dash.identity._HOST_FILES",
        (
            (mounted / "hostname", mounted / "os-release"),
            (proc / "hostname", proc / "os-release"),
            (image / "hostname", image / "os-release"),
        ),
    )
    from halo_dash.identity import read_host_identity

    identity = read_host_identity(in_container=True)
    assert identity["hostname"] == "aihost01"
    assert identity["os"] == "Fedora Linux 42 (Server Edition)"


def test_os_release_falls_back_to_name_and_version():
    from halo_dash.identity import parse_os_release

    assert parse_os_release('NAME="Fedora Linux"\nVERSION="42 (Server Edition)"\n') == "Fedora Linux 42 (Server Edition)"


def test_version_endpoints():
    import asyncio

    import httpx

    from halo_dash.versions import fetch_litellm_version, fetch_llama_versions

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/version":
            return httpx.Response(404)
        if request.url.path == "/openapi.json":
            return httpx.Response(200, json={"openapi": "3.1.0", "info": {"title": "LiteLLM", "version": "1.82.3"}})
        if request.url.path == "/props":
            return httpx.Response(200, json={"role": "router", "build_info": "b8771-9ef1fab9c"})
        return httpx.Response(404)

    async def run() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            assert await fetch_litellm_version(client, "http://litellm.example") == "1.82.3"
            rows = await fetch_llama_versions(client, [Backend("router", "http://llama.example")])
        assert rows == [{"name": "router", "version": "b8771-9ef1fab9c"}]

    asyncio.run(run())


def test_language_defaults_to_english(tmp_path: Path, monkeypatch):
    from halo_dash.config import load_config

    monkeypatch.delenv("HALO_LANGUAGE", raising=False)
    path = tmp_path / "config.yaml"
    path.write_text("listen: 127.0.0.1:9\n", encoding="utf-8")
    assert load_config(path).language == "en"
    path.write_text("language: DE\nlisten: 127.0.0.1:9\n", encoding="utf-8")
    assert load_config(path).language == "de"
