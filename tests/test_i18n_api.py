"""Translations are complete in both languages; the local API serves ranked VALID data only."""
import json
import os
import re

from conftest import build_state, dex_pair
from core.models import Event
from i18n import LANGS, load, t

SRC = os.path.join(os.path.dirname(__file__), "..", "src")


def test_same_keys_in_every_language():
    keys = [set(load(lang)) for lang in LANGS]
    assert keys[0] == keys[1]
    for lang in LANGS:
        assert all(v.strip() for v in load(lang).values()), f"empty translation in {lang}"


def _literal_keys():
    pat = re.compile(r"""\bt\(\s*["']([a-z_]+\.[A-Za-z0-9_. ]+)["']""")
    found = set()
    for root, _, files in os.walk(SRC):
        for f in files:
            if f.endswith(".py"):
                with open(os.path.join(root, f), encoding="utf-8") as fh:
                    found |= set(pat.findall(fh.read()))
    return found


def test_every_literal_key_exists():
    missing = sorted(k for k in _literal_keys() if k not in load("vi"))
    assert not missing, missing


def test_every_dynamic_key_exists(good_state, bad_state):
    """Keys built at runtime (metrics, factors, risks, issues, events, filters, lifecycle, ...)."""
    from intel.events import ALL_EVENTS
    from intel.lifecycle import STAGES
    from risk.engine import CATEGORIES
    from social.narrative import ALL_TAGS
    vi = load("vi")
    need = set()
    for st in (good_state, bad_state, build_state(dex_pair(liq=7.1e-07)), build_state(dex_pair(), holders=False, dev=False)):
        need |= {f"metric.{m.key}" for m in st.metrics} | {f"note.{m.note}" for m in st.metrics if m.note}
        need |= {f"risk.{f.key}" for f in st.risk.factors} | {f"risk.{f.key}.detail" for f in st.risk.factors}
        need |= {f"missing.{k}" for k in st.risk.missing} | {f"issue.{i.key}" for i in st.quality.issues}
        need |= {f"filter.{k}" for k in st.filter_fails} | {f"score.{k}" for k in st.subscores}
        for sub in st.subscores.values():
            need |= {f"factor.{f.key}" for f in sub.factors} | ({f"note.{sub.note}"} if sub.note else set())
            need |= {f"note.{f.note}" for f in sub.factors if f.note}
        need |= {f"signal.{x.key}" for x in st.early.signals} | {f"note.{x.note}" for x in st.early.signals if x.note}
    need |= {f"event.{e}" for e in ALL_EVENTS} | {f"event.{e}.detail" for e in ALL_EVENTS}
    need |= {f"lifecycle.{s}" for s in STAGES} | {f"riskcat.{c}" for c in CATEGORIES}
    need |= {f"narrative.{n}" for n in ALL_TAGS}
    missing = sorted(k for k in need if k not in vi)
    assert not missing, missing


def test_templates_format_in_both_languages(bad_state):
    from alerts.report import event_text, issue_text, risk_text
    ev = Event(0, "m", "S", "VOLUME_SPIKE", "positive", {"before": 1000, "now": 5000})
    for lang in LANGS:
        from i18n import set_language
        set_language(lang)
        for f in bad_state.risk.factors:
            name, detail = risk_text(f)
            assert "{" not in detail, (lang, f.key, detail)
        assert "{" not in event_text(ev)[1]
        for i in build_state(dex_pair(liq=7.1e-07)).quality.issues:
            assert "{" not in issue_text(i), (lang, i.key)
    set_language("en")
    assert t("tab.new_coins", "vi") == "COIN MỚI" and t("tab.new_coins", "en") == "NEW COINS"


def test_api_routes_rank_valid_only(good_state):
    from api.server import ApiServer
    invalid = build_state(dex_pair(mint="BAD", liq=7.1e-07))
    api = ApiServer(lambda: [good_state, invalid], lambda: [], lambda: {"ok": True})
    code, top = api.route("/api/top", {})
    assert code == 200 and [r["mint"] for r in top] == [good_state.mint]
    code, detail = api.route(f"/api/tokens/{good_state.mint}", {})
    assert code == 200
    m = next(x for x in detail["metrics"] if x["key"] == "mc")
    assert {"value", "source", "timestamp", "age_s", "confidence"} <= set(m)
    code, rows = api.route("/api/tokens", {"status": ["INVALID"]})
    assert [r["mint"] for r in rows] == ["BAD"] and rows[0]["opportunity"] is None
    assert api.route("/api/nope", {})[0] == 404
    json.dumps(detail, default=str)
