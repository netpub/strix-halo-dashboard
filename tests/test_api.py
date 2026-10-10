from pathlib import Path

from fastapi.testclient import TestClient

from halo_dash.app import create_app
from halo_dash.config import Config


def test_snapshot_and_stream(tmp_path: Path):
    config = Config(
        fixtures=True,
        direct_history_path=tmp_path / "direct.sqlite",
        static_dir=None,
        poll_interval_s=60,
    )
    app = create_app(config)
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        snapshot = client.get("/api/snapshot")
        assert snapshot.status_code == 200
        body = snapshot.json()
        assert body["fixtures"] is True
        assert body["language"] == "en"
        assert body["overall"] == "generating"
        assert body["host"]["gpu"]["name"] == "AMD Radeon 8060S"
        assert body["system"]["hostname"] == "aihost01"
        assert body["system"]["litellm_version"] == "1.82.3"
        assert any(row["source"] == "litellm" for row in body["requests"])
        assert any(row["status"] == "generating" for row in body["requests"])
        listed = client.get("/api/requests")
        assert listed.status_code == 200
        assert listed.json()["requests"]
