import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from midi2mkcd import cli

TESTS_DIR = Path(__file__).parent
TIERS = ("unit", "integration")


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """
    Mark each test with its tier by its directory, so `-m unit` selects them.

    A test outside these directories would be missed by `-m`, so fail collection
    instead of letting it run untiered.
    """
    for item in items:
        parts = item.path.relative_to(TESTS_DIR).parts
        if len(parts) < 2 or parts[0] not in TIERS:
            raise pytest.UsageError(
                f"{item.nodeid}: tests must live under "
                f"{', '.join(f'tests/{tier}' for tier in TIERS)}"
            )
        item.add_marker(parts[0])


@pytest.fixture(autouse=True)
def _restore_package_logger() -> Iterator[None]:
    """
    Undo `cli.configure_logging` after each test that runs the CLI.

    It sets the `midi2mkcd` logger's level and handler, which would otherwise leak
    into later tests in the same process (e.g. `-v` letting DEBUG records reach
    `caplog`), making results depend on test order and pytest-xdist's scheduling.
    """
    package_logger = logging.getLogger("midi2mkcd")
    level, handlers = package_logger.level, package_logger.handlers[:]
    yield
    package_logger.setLevel(level)
    package_logger.handlers[:] = handlers
    cli._cli_log_handler = None
