"""Fix 8 — trading.json (persisted, and restored from snapshots) can never override the code's strategy defaults;
only operational switches survive."""
import json

from trading.config import OPERATIONAL_KEYS, TradingConfig, production_config


def test_old_file_cannot_pin_outdated_defaults(tmp_path):
    p = tmp_path / "trading.json"
    old = {**TradingConfig().__dict__, "paper_fill_without_quote": True, "stop_loss_pct": 12.0,
           "entry_location_gate": False, "priority_fee_sol": 0.0, "kill_switch": True}
    p.write_text(json.dumps(old, default=str), encoding="utf-8")
    cfg = TradingConfig.load(p)
    assert cfg.paper_fill_without_quote is False and cfg.stop_loss_pct == 15.0 and cfg.entry_location_gate is True
    assert cfg.priority_fee_sol == 0.005 and cfg.kill_switch is True               # operational switch kept
    assert set(cfg.ignored_file_keys) == {"paper_fill_without_quote", "stop_loss_pct", "entry_location_gate",
                                          "priority_fee_sol"}
    assert cfg.sample_id() == TradingConfig(kill_switch=True).sample_id() == TradingConfig().sample_id()


def test_save_writes_only_operational_switches(tmp_path):
    p = tmp_path / "trading.json"
    c = TradingConfig(kill_switch=True)
    c.save(p)
    assert json.loads(p.read_text()) == {"kill_switch": True, "enabled": True} and set(OPERATIONAL_KEYS) == {"kill_switch", "enabled"}
    assert TradingConfig.load(p).kill_switch is True


def test_server_fingerprint_comes_from_code_and_env_only(tmp_path):
    p = tmp_path / "trading.json"
    p.write_text(json.dumps({"max_open_positions": 1, "lifecycle": False}), encoding="utf-8")
    cfg = TradingConfig.load(p)
    cfg.experimental = cfg.latency_probe = cfg.lifecycle = True                 # what web/app.py applies from env
    cfg.latency_slippage_model = "AUTO"
    assert cfg.sample_id() == production_config().sample_id() == "02e5bdcc03"
    assert TradingConfig.load(tmp_path / "missing.json").ignored_file_keys == []
