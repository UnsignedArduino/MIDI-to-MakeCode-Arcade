"""Shared helpers for the tests (a plain module, since pytest won't collect it)."""

from pathlib import Path

PACKAGE_ROOT = Path(__file__).parent.parent / "src" / "midi2mkcd"
FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_MIDI = FIXTURES / "fixture.mid"
GOLDEN = FIXTURES / "golden"
# The hand-tuned params the goldens were generated with (the bundled default)
INSTRUMENT_PARAMS = PACKAGE_ROOT / "resources" / "instrument_params.yaml"
