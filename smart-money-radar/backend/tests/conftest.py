import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def pytest_addoption(parser):
    parser.addoption("--live", action="store_true", default=False, help="run tests that call real external APIs")


def pytest_collection_modifyitems(config, items):
    skip_live = pytest.mark.skip(reason="needs --live")
    skip_int = pytest.mark.skip(reason="needs TEST_DATABASE_URL and TEST_REDIS_URL")
    have_db = bool(os.environ.get("TEST_DATABASE_URL") and os.environ.get("TEST_REDIS_URL"))
    for item in items:
        if "live" in item.keywords and not config.getoption("--live"):
            item.add_marker(skip_live)
        if "integration" in item.keywords and not have_db:
            item.add_marker(skip_int)
