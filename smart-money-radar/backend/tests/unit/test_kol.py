"""Watchlist line parsing (no network, no database)."""
from radar.services.kol import parse_lines

A = "CyaE1VxvBrahnPWkqm5VsdCvyS2QmNht2UFrKJHga54o"
B = "2fg5QD1eD7rzNNCsvnhmXFm5hqNgwTTG8p7kQ6f3rx6f"


def test_parse_name_handle_and_links():
    rows, errors = parse_lines(f"{A} Cented @Cented7\n{B}, Cupsey, https://x.com/cupseyy\n# comment\n\nbad Foo\n{A} dup")
    assert errors == ["dòng 5: địa chỉ không hợp lệ: bad"]
    assert rows[0] == {"address": A, "label": "Cented", "x_handle": "Cented7"}
    assert rows[1] == {"address": B, "label": "Cupsey", "x_handle": "cupseyy"}
    assert len(rows) == 2                                   # duplicate address ignored


def test_label_defaults_to_handle_or_short_address():
    rows, _ = parse_lines(f"{A} @someone\n{B}")
    assert rows[0]["label"] == "someone" and rows[1]["label"] == "Ví 2fg5…rx6f"


def test_old_trades_never_alert():
    import asyncio, time
    from radar.services.kol import handle_trade
    called = []

    async def deliver(*a):
        called.append(a)

    class NoDb:            # handle_trade must return before touching the database
        pool = None
    p = {"wallet": A, "mint": B, "tx_type": "buy", "sol": 5, "ts": time.time() - 3600, "signature": "x"}
    asyncio.run(handle_trade(NoDb(), p, {"kol_alert_max_age_seconds": 300}, {A: {"label": "Cented"}}, deliver))
    assert called == []
