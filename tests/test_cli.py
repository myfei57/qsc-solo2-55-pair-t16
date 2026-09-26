"""命令行入口与端到端演示。"""

from __future__ import annotations

import json

from cspplant.cli import main


def test_demo_runs_end_to_end(tmp_path, capsys):
    exit_code = main(["demo", "--fast", "--storage-root", str(tmp_path / "state"), "--cycles", "200"])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["launch"]["ok"] is True
    assert payload["launch"]["stage"] == "done"
    assert payload["launch"]["load_mw"] == 30.0
    assert payload["launch"]["flow_kg_s"] == 58.0
    assert payload["concurrency"]["effective_pct"] == payload["concurrency"]["ceiling_pct"]
    assert payload["washing"]["ok"] is True
    assert payload["handoff"]["max_pumps_running"] == 1
    assert payload["trip"]["latched"] is True
    assert payload["reset"]["ok"] is True
    assert all(item["ok"] for item in payload["probe"].values())
    assert payload["status"]["receiver"]["state"] == "cold"


def test_status_reports_every_subsystem(tmp_path, capsys):
    exit_code = main(["status", "--storage-root", str(tmp_path / "state")])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    for key in ("field", "receiver", "salt", "steam", "wash", "alarm", "audit", "store"):
        assert key in payload
    assert payload["topology"]["order"][0] == "irradiance"


def test_config_command_prints_effective_config(tmp_path, capsys):
    exit_code = main(["config"])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["limits"]["receiver"]["temp_max_c"] == 720.0
    assert payload["limits"]["heliostat"]["mirror_area_m2"] > 0


def test_reset_command_reports_removed_flag(tmp_path, capsys):
    exit_code = main(["reset", "--storage-root", str(tmp_path / "state")])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["platform_state_removed"] is False


def test_load_config_file_is_used(tmp_path, capsys):
    from cspplant.config import config_to_mapping, default_config

    path = tmp_path / "csp.json"
    path.write_text(json.dumps(config_to_mapping(default_config())), encoding="utf-8")
    exit_code = main(
        ["status", "--config", str(path), "--storage-root", str(tmp_path / "state")]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["platform"] == default_config().name
