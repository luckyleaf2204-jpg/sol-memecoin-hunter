"""Graduations whose logs Solana truncated (fixture: a real migration transaction, 23/11/2026)."""
import json
from pathlib import Path

from radar.decoder.logs import records_from_logs
from radar.decoder.migration import graduation_from_tx, is_migration_log
from radar.services.stream import StreamService

FIX = json.loads((Path(__file__).parent.parent / "fixtures" / "migration_truncated.json").read_text())


def test_truncated_migration_has_no_event_but_is_recognised_and_resolved_from_the_full_tx():
    tx = FIX["tx"]
    logs = tx["meta"]["logMessages"]
    assert any(l.startswith("Log truncated") for l in logs)
    assert records_from_logs("s", 0, logs)["migrations"] == []            # what the stream saw before
    assert is_migration_log(logs)
    assert graduation_from_tx(tx)["mint"] == FIX["mint"]


def test_stream_queues_a_truncated_migration_for_a_full_read():
    s = StreamService(ctx=None)
    sig = FIX["tx"]["transaction"]["signatures"][0]
    s.handle({"signature": sig, "err": None, "logs": FIX["tx"]["meta"]["logMessages"]}, FIX["tx"]["slot"])
    assert s.pending_migration_fetch == [sig] and s.stats["migrations_truncated"] == 1
