import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))


def pytest_configure(config):
    config.addinivalue_line("markers", "chromium: needs a Chromium binary (skipped without one)")


def pytest_collection_modifyitems(config, items):
    """A skipped browser test is not a passed one: the report says which host ran them."""
    marked = [item for item in items if "chromium" in item.keywords]
    if not marked:
        return
    from wpb_fakes import mod
    if mod("proof.runner").find_chromium() is not None:
        return
    skip = pytest.mark.skip(reason="no Chromium on this host")
    for item in marked:
        item.add_marker(skip)
