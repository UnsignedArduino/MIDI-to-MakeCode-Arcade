"""Shared helpers for the tests (a plain module, since pytest won't collect it)."""

from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_MIDI = FIXTURES / "fixture.mid"
GOLDEN = FIXTURES / "golden"
# The hand-tuned params the goldens were generated with
INSTRUMENT_PARAMS = Path(__file__).parent.parent / "examples" / "instrument_params.yaml"
