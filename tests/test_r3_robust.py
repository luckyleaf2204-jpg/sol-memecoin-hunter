"""Review round 3 (A1): the snapshot / lease path never dies silently and never skips the release + engine stop."""
import time

from fastapi.testclient import TestClient

import core.lease as L
import web.app as webapp
from core import snapshot as S
from test_s6_lease import _app, _wait


class Flaky(S.LocalStore):
    """A store whose lease reads fail like an HTTP store answering 5xx / timing out."""
    def __init__(self, root, exc=OSError):
        super().__init__(root)
        self.exc, self.fail = exc, False

    def get(self, name):
        if self.fail and name == L.LEASE:
            raise self.exc("store 503 / timeout")
        return super().get(name)


def _setup(tmp_path, monkeypatch, store):
    d = tmp_path / "d"
    d.mkdir()
    monkeypatch.setattr(webapp, "DATA_DIR", d)
    monkeypatch.setattr(S, "store_from_env", lambda: store)
    monkeypatch.setenv("RESEARCH_LOG", "0")
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)


def test_held_error_is_logged_and_the_hourly_loop_keeps_going(tmp_path, monkeypatch, capsys):
    store = Flaky(tmp_path / "store")
    _setup(tmp_path, monkeypatch, store)
    monkeypatch.setattr(S, "SNAPSHOT_EVERY_S", 0.05)
    app = _app(tmp_path, "a")
    with TestClient(app):
        st = app.state.hunter
        assert st["role"] == "ACTIVE"
        store.fail = True
        assert _wait(lambda: (st["snapshot"].get("last_error") or {}).get("reason") == "hourly")
        time.sleep(0.3)
        n_fail = capsys.readouterr().out.count("[snapshot] hourly FAILED")
        assert n_fail >= 2                                    # the loop survived the first failure
        store.fail = False
        assert _wait(lambda: st["snapshot"].get("last_error") is None)      # and recovers by itself


def test_shutdown_still_releases_and_stops_the_engine_when_held_fails(tmp_path, monkeypatch, capsys):
    store = Flaky(tmp_path / "store", exc=TimeoutError)
    _setup(tmp_path, monkeypatch, store)
    app = _app(tmp_path, "a")
    stopped = []
    eng = app.state.hunter["engine"]
    real_stop = eng.stop
    eng.stop = lambda: (stopped.append(1), real_stop())
    c = TestClient(app)
    c.__enter__()
    store.fail = True                                         # held() and release() both fail at SIGTERM
    c.__exit__(None, None, None)
    out = capsys.readouterr().out
    assert "[snapshot] shutdown FAILED: TimeoutError" in out and "[lease] release failed: TimeoutError" in out
    assert stopped == [1]
