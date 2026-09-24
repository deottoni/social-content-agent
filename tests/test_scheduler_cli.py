import json

import yaml

from conftest import StaticHost, make_post, write_config
from instagram_os import cli
from instagram_os.client import AuthError
from instagram_os.reasoner import RulesReasoner
from instagram_os.scheduler import Runtime, crontab_lines, run_job


def runtime(brand, store, fake):
    return Runtime(brand, client=fake, reasoner=RulesReasoner(), media_host=StaticHost(), store=store)


def test_daily_run_executes_every_stage(brand, store, fake):
    make_post(brand)
    rt = runtime(brand, store, fake)
    status, summary = run_job(rt, "daily-run")
    assert status == "OK"
    for job in ("token-check", "publish", "comments", "opportunities", "analytics"):
        assert f"{job}: OK" in summary
    assert len(fake.media) == 1
    assert {r["job"] for r in store.job_runs()} >= {"daily-run", "publish", "comments", "analytics"}


def test_overlapping_runs_are_skipped(brand, store, fake):
    rt = runtime(brand, store, fake)
    assert store.acquire_lock("job:publish")
    assert run_job(rt, "publish") == ("SKIPPED", "already running")


def test_job_failures_are_recorded_not_raised(brand, store, fake):
    fake.fail["get_account"] = [AuthError("expired", code=190)]
    status, summary = run_job(runtime(brand, store, fake), "token-check")
    assert status == "FAILED" and "AuthError" in summary
    assert store.job_runs(1)[0]["status"] == "FAILED"
    assert store.acquire_lock("job:token-check")  # lock released even on failure


def test_daily_run_stops_on_auth_failure(brand, store, fake):
    fake.fail["get_account"] = [AuthError("expired", code=190)]
    status, summary = run_job(runtime(brand, store, fake), "daily-run")
    assert "stopped: fix authentication first" in summary
    assert "publish:" not in summary


def test_plan_writes_brief_and_records_human_action(brand, store, fake):
    status, summary = run_job(runtime(brand, store, fake), "plan")
    assert status == "OK" and "human action needed" in summary
    assert "Planning brief" in (brand.instagram_dir / "plan-brief.md").read_text()


def test_plan_hook_command(brand, store, fake, tmp_path):
    marker = tmp_path / "ran"
    write_config(brand, scheduler={"plan_command": f"echo {{brand}} > {marker}"})
    status, _ = run_job(runtime(brand, store, fake), "plan")
    assert status == "OK" and marker.read_text().strip() == "test-brand"


def test_token_check_warns_before_expiry(brand, store, fake, monkeypatch):
    from datetime import timedelta
    from instagram_os.store import iso, utcnow
    monkeypatch.setenv("INSTAGRAM_TOKEN_EXPIRES_AT", iso(utcnow() + timedelta(days=3)))
    status, summary = run_job(runtime(brand, store, fake), "token-check")
    assert status == "OK" and "WARNING" in summary


def test_token_refresh_via_sink(brand, store, fake, monkeypatch, tmp_path):
    from datetime import timedelta
    from instagram_os.store import iso, utcnow
    sink = tmp_path / "token"
    write_config(brand, scheduler={"token_sink_command": f"cat > {sink}"})
    monkeypatch.setenv("INSTAGRAM_TOKEN_EXPIRES_AT", iso(utcnow() + timedelta(days=3)))
    status, summary = run_job(runtime(brand, store, fake), "token-check")
    assert "refreshed" in summary and sink.read_text() == "new-token"
    assert "new-token" not in (brand.instagram_dir / "activity.log").read_text()


def test_crontab_lines(brand):
    text = crontab_lines(brand)
    assert "--brand test-brand comments" in text and "*/30 * * * *" in text


def test_cli_end_to_end_dry_run(brand, tmp_path, monkeypatch, capsys):
    """The real CLI path with no credentials: dry-run everything, nothing leaves the machine."""
    registry = tmp_path / "brands.local.json"
    cfg = yaml.safe_load((brand.instagram_dir / "config.yaml").read_text())
    cfg["api"]["mode"] = "dry_run"
    (brand.instagram_dir / "config.yaml").write_text(yaml.safe_dump(cfg))
    for var in ("INSTAGRAM_ACCESS_TOKEN", "INSTAGRAM_ACCOUNT_ID", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    make_post(brand)
    args = ["--brand", "test-brand", "--registry", str(registry)]
    assert cli.main(args + ["daily-run"]) == 0
    assert cli.main(args + ["status"]) == 0
    out = capsys.readouterr().out
    assert "API mode: dry_run" in out and "third_party_comment" in out and "locked" in out
    assert cli.main(args + ["queue"]) == 0
    assert "QUEUED" in capsys.readouterr().out  # dry-run never marks PUBLISHED
    assert cli.main(args + ["log"]) == 0
    assert "Would publish" in capsys.readouterr().out
    assert cli.main(args + ["opportunities", "list"]) == 0
    assert cli.main(args + ["weekly-review"]) == 0


def test_cli_refuses_disabled_brand(brand, tmp_path, capsys):
    cfg = yaml.safe_load((brand.instagram_dir / "config.yaml").read_text())
    cfg["enabled"] = False
    (brand.instagram_dir / "config.yaml").write_text(yaml.safe_dump(cfg))
    assert cli.main(["--brand", "test-brand", "--registry", str(tmp_path / "brands.local.json"), "publish"]) == 2
    assert "not enabled" in capsys.readouterr().err


def test_cli_init_creates_config(tmp_path, capsys):
    brand_dir = tmp_path / "new-brand"
    brand_dir.mkdir()
    reg = tmp_path / "reg.json"
    reg.write_text(json.dumps({"new-brand": str(brand_dir)}))
    assert cli.main(["--brand", "new-brand", "--registry", str(reg), "init"]) == 0
    cfg = yaml.safe_load((brand_dir / "instagram" / "config.yaml").read_text())
    assert cfg["enabled"] is False and cfg["api"]["mode"] == "dry_run"
    assert (brand_dir / "instagram" / "facts.md").exists()


def test_cron_matching():
    from datetime import datetime
    from instagram_os.scheduler import cron_matches
    mon_0700 = datetime(2026, 9, 21, 7, 0)  # a Monday
    assert cron_matches("0 7 * * 1", mon_0700)
    assert not cron_matches("0 7 * * 2", mon_0700)
    assert cron_matches("*/30 * * * *", mon_0700.replace(minute=30))
    assert not cron_matches("*/30 * * * *", mon_0700.replace(minute=15))
    assert cron_matches("15 */4 * * *", mon_0700.replace(hour=8, minute=15))
    assert cron_matches("0 9 * * 0", datetime(2026, 9, 27, 9, 0)) and cron_matches("0 9 * * 7", datetime(2026, 9, 27, 9, 0))
    assert cron_matches("0 9 * * 1-5", mon_0700.replace(hour=9))


def test_tick_runs_due_jobs_once_and_catches_up_after_sleep(brand, store, fake):
    from datetime import datetime, timedelta
    from instagram_os.scheduler import tick
    write_config(brand, scheduler={"cron": {"comments": "*/30 * * * *", "weekly-review": "0 7 * * 1",
                                            "analytics": "0 6 * * *"}})
    rt = Runtime(brand, client=fake, store=store)
    t0 = datetime(2026, 9, 21, 7, 0).astimezone()  # Monday 07:00 local
    assert {j for j, _, _ in tick(rt, now=t0)} == {"comments", "weekly-review"}
    assert {j for j, _, _ in tick(rt, now=t0)} == set()        # same instant again: nothing re-runs
    # Laptop asleep for two days: every job that came due runs exactly once on wake.
    ran = [j for j, _, _ in tick(rt, now=t0 + timedelta(days=2))]
    assert sorted(ran) == sorted(set(brand.config["scheduler"]["cron"]) - {"weekly-review"})  # weekly: not due again yet
    assert ran[0] == "token-check"                              # auth is checked before anything else


def test_launchd_plist(brand):
    from instagram_os.scheduler import launchd_plist
    xml = launchd_plist(brand, python="/usr/bin/python3")
    assert "<string>tick</string>" in xml and "--brand" in xml and "StartInterval" in xml
    assert "com.social-content-agent.instagram.test-brand" in xml
    assert "TOKEN" not in xml.upper().replace("TOKEN_", "")  # never embeds secrets


def test_env_file_is_loaded_without_overriding_the_shell(tmp_path):
    from instagram_os.cli import load_env_file
    (tmp_path / "test-brand.env").write_text(
        "# secrets\nexport INSTAGRAM_ACCESS_TOKEN='abc'\nINSTAGRAM_ACCOUNT_ID=123\nINSTAGRAM_API_MODE=live\n")
    env = {"INSTAGRAM_OS_ENV_DIR": str(tmp_path), "INSTAGRAM_API_MODE": "dry_run"}
    assert load_env_file("test-brand", env) == tmp_path / "test-brand.env"
    assert env["INSTAGRAM_ACCESS_TOKEN"] == "abc" and env["INSTAGRAM_ACCOUNT_ID"] == "123"
    assert env["INSTAGRAM_API_MODE"] == "dry_run"
    assert load_env_file("other-brand", env) is None
