"""`python -m pytest` runs the quick tests (well under a minute).
`python -m pytest --all` also runs the slow ones: every old Tide program on both engines and the
long searches. CI always runs --all."""
import pytest


def pytest_addoption(parser):
    parser.addoption("--all", action="store_true", help="also run the slow tests (CI does)")


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: skipped unless you pass --all")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--all"):
        return
    skip = pytest.mark.skip(reason="slow: run python -m pytest --all")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip)


def pytest_terminal_summary(terminalreporter, config):
    if not config.getoption("--all"):
        n = len(terminalreporter.stats.get("skipped", []))
        terminalreporter.write_line(f"Quick run. For every test, including the slow ones, run: python -m pytest --all ({n} skipped)")
