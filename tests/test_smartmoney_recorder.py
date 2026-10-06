"""Smart-money recorder: decoding of pump.fun TradeEvent / CompleteEvent logs and compact storage (no network)."""
import base64
import struct

from smartmoney import recorder as R

MINT = bytes(range(1, 33))
USER = bytes(range(33, 65))


def trade_line(sol=2_000_000_000, tok=5_000_000_000, buy=True, ts=1_791_000_000):
    d = R.TRADE_DISC + MINT + struct.pack("<QQ", sol, tok) + bytes([1 if buy else 0]) + USER + struct.pack("<q", ts)
    d += b"\0" * 64                                          # newer fields (reserves, fees ...) are ignored
    return "Program data: " + base64.b64encode(d).decode()


def test_trade_event_decodes():
    ev = R.decode(trade_line(buy=False))
    assert ev == {"kind": "trade", "mint": R.b58(MINT), "sol": 2_000_000_000, "token": 5_000_000_000,
                  "is_buy": False, "wallet": R.b58(USER), "ts": 1_791_000_000}


def test_complete_event_and_noise():
    d = R.COMPLETE_DISC + USER + MINT + bytes(32) + struct.pack("<q", 1_791_000_123)
    ev = R.decode("Program data: " + base64.b64encode(d).decode())
    assert ev == {"kind": "complete", "mint": R.b58(MINT), "ts": 1_791_000_123}
    assert R.decode("Program log: Instruction: Buy") is None
    assert R.decode("Program data: !!!notbase64") is None
    assert R.decode("Program data: " + base64.b64encode(b"x" * 120).decode()) is None


def test_b58_known_value():
    assert R.b58(bytes(32)) == "1" * 32
    assert R.b58(bytes([0, 1])) == "12"


def test_store_interns_ids_skips_dust_and_records_gaps(tmp_path):
    s = R.Store(tmp_path / "t.db")
    s.add(R.decode(trade_line()), 100)
    s.add(R.decode(trade_line(sol=500_000)), 101)                # dust (< 0.001 SOL)
    s.add(R.decode(trade_line(buy=False)), 102)
    assert s.flush() == 2
    rows = s.db.execute("SELECT mint_id, wallet_id, is_buy, sol_lamports FROM trades").fetchall()
    assert rows[0][0] == rows[1][0] and rows[0][1] == rows[1][1] and [r[2] for r in rows] == [1, 0]
    assert s.db.execute("SELECT COUNT(*) FROM names").fetchone()[0] == 2
    s.gap(10.0, 70.0)
    assert s.db.execute("SELECT * FROM gaps").fetchall() == [(10.0, 70.0)]
    s2 = R.Store(tmp_path / "t.db")                              # restart keeps the id table
    assert s2._id("mint", R.b58(MINT)) == rows[0][0] and s2.n_trades == 2


def test_garbled_timestamp_is_dropped():
    # seen live 2026-10-06: one event decoded with ts = -2.9e17 and ended the 21-day window after 26 minutes
    assert R.decode(trade_line(ts=-288_230_376_136_558_712)) is None
    assert R.decode(trade_line(ts=4_000_000_000)) is None
    assert R.decode(trade_line(ts=1_791_000_000)) is not None
