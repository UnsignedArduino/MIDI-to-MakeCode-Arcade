"""Shared helpers for the tests (a plain module, since pytest won't collect it)."""

from pathlib import Path

PACKAGE_ROOT = Path(__file__).parent.parent / "src" / "midi2mkcd"
FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_MIDI = FIXTURES / "fixture.mid"
GOLDEN = FIXTURES / "golden"
# Inputs and real-pxt outputs for the simulator ports (make_sim_cases.py,
# make_sim_goldens.mjs)
SIM_CASES = FIXTURES / "sim_cases.json"
SIM_GOLDEN = GOLDEN / "sim_instructions.json"
# The hand-tuned params the goldens were generated with (the bundled default)
INSTRUMENT_PARAMS = PACKAGE_ROOT / "resources" / "instrument_params.yaml"
