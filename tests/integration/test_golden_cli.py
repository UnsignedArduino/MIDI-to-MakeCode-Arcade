"""
Converts the fixture with the current CLI and compares it to the golden files.

The goldens were captured before the library refactor, so these pin its output. They
read the output back as text, since `-o` writes CRLF on Windows and the goldens are LF.
"""

import sys
from pathlib import Path

import pytest
from helpers import FIXTURE_MIDI, GOLDEN, INSTRUMENT_PARAMS

from midi2mkcd.main import main

CASES = {
    "hex.ts": [],
    "play.ts": ["--test-generate-code"],
    "visualizer.ts": ["--generate-extra-code"],
    "replace.ts": ["--test-replace-all-melodics-with", "5", "--test-generate-code"],
    "sample_0-2.ts": ["--test-sample-melodic-instruments", "0-2"],
}


@pytest.mark.parametrize(("golden", "extra_args"), CASES.items(), ids=CASES.keys())
def test_cli_matches_golden(
    golden: str,
    extra_args: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / golden
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "midi2mkcd",
            "-i",
            str(FIXTURE_MIDI),
            "-p",
            str(INSTRUMENT_PARAMS),
            "-o",
            str(output),
            *extra_args,
        ],
    )
    main()
    expected = (GOLDEN / golden).read_text(encoding="utf-8")
    assert output.read_text(encoding="utf-8") == expected
