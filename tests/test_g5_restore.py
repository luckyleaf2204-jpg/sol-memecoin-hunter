"""G5 — restore: a corrupt manifest or leftover local files halt; a corrupt current generation falls back to the
newest older generation that verifies; /healthz is ok=false while halted."""
import gzip
import json

import pytest

from core import snapshot as S
from test_fix3_snapshot import _data


def test_corrupt_current_generation_falls_back_to_an_older_one(tmp_path):
    d, store = _data(tmp_path / "data"), S.LocalStore(tmp_path / "store")
    (d / "paper_bot.json").write_text(json.dumps({"cash": 1}), encoding="utf-8")
    S.snapshot(d, store, now=1.0)                                     # gen 1
    (d / "paper_bot.json").write_text(json.dumps({"cash": 2}), encoding="utf-8")
    man2 = S.snapshot(d, store, now=2.0)                              # gen 2 (current)
    store.put(man2["files"]["paper_bot.json"]["object"], gzip.compress(b"bitrot"))
    fresh = tmp_path / "fresh"
    r = S.restore(fresh, store)
    assert r["gen"] == 1 and r["fell_back_from"] == 2 and "fell back to generation 1" in r["status"]
    assert json.loads((fresh / "paper_bot.json").read_text()) == {"cash": 1} and r["errors"]


def test_no_valid_generation_halts(tmp_path):
    d, store = _data(tmp_path / "data"), S.LocalStore(tmp_path / "store")
    man = S.snapshot(d, store)
    store.put(man["files"]["paper_bot.json"]["object"], b"not gzip")
    with pytest.raises(S.SnapshotError, match="no generation verifies"):
        S.restore(tmp_path / "fresh", store)


@pytest.mark.parametrize("raw", [b"{not json", b"[]", json.dumps({"gen": 3}).encode()])
def test_corrupt_manifest_halts(tmp_path, raw):
    d, store = _data(tmp_path / "data"), S.LocalStore(tmp_path / "store")
    S.snapshot(d, store)
    store.put(S.MANIFEST, raw)
    with pytest.raises(S.SnapshotError, match="corrupt"):
        S.restore(tmp_path / "fresh", store)
    assert not (tmp_path / "fresh" / "paper_bot.json").exists()


def test_each_generation_has_its_own_manifest(tmp_path):
    d, store = _data(tmp_path / "data"), S.LocalStore(tmp_path / "store")
    m1, m2 = S.snapshot(d, store, now=1.0), S.snapshot(d, store, now=2.0)
    assert json.loads(store.get(f"manifest-gen-{m1['slot']}.json"))["gen"] == 1
    assert json.loads(store.get(f"manifest-gen-{m2['slot']}.json"))["gen"] == 2
    store.put(S.MANIFEST, b"{garbage")
    m3 = S.snapshot(d, store, now=3.0)                                 # unreadable main manifest:
    assert m3["gen"] == 3 and m3["slot"] == 0                           # numbering continues from the gen manifests
    assert json.loads(store.get(f"manifest-gen-{m2['slot']}.json"))["gen"] == 2     # gen 2 untouched
