from pathlib import Path

import pytest

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
