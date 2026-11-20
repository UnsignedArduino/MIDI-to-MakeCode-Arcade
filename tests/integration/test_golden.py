"""
Converts the fixture through the library API and compares it to the golden files.

The goldens were captured from the CLI before the library refactor. `replace.ts` was
regenerated once to drop a trailing space from its "melodics replaced" comment.
"""

from collections.abc import Callable

import pytest
from helpers import FIXTURE_MIDI, GOLDEN

from midi2mkcd import convert, tuning

MIDI = FIXTURE_MIDI.read_bytes()

CASES: dict[str, Callable[[], str]] = {
    "hex.ts": lambda: convert(MIDI).to_typescript("hex"),
    "play.ts": lambda: convert(MIDI).to_typescript("play"),
    "visualizer.ts": lambda: convert(MIDI).to_typescript("visualizer"),
    "replace.ts": lambda: tuning.convert_replaced(MIDI, melodics=5).to_typescript(
        "play"
    ),
    "sample_0-2.ts": lambda: tuning.sample_melodics(MIDI, [0, 1, 2]),
}


@pytest.mark.parametrize(("golden", "generate"), CASES.items(), ids=CASES.keys())
def test_api_matches_golden(golden: str, generate: Callable[[], str]) -> None:
    assert generate() == (GOLDEN / golden).read_text(encoding="utf-8")


def test_to_hex_is_the_bare_digits() -> None:
    assert f"hex`{convert(MIDI).to_hex()}`" == (GOLDEN / "hex.ts").read_text(
        encoding="utf-8"
    )
