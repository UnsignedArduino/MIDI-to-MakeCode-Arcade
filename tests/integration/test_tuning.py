import io

import pytest
from helpers import FIXTURE_MIDI, GOLDEN, PACKAGE_ROOT

from midi2mkcd import (
    InstrumentParamsError,
    default_instrument_params_text,
    load_instrument_params,
    tuning,
)
from midi2mkcd.render import GENERATED_BY

MIDI = FIXTURE_MIDI.read_bytes()
BLANK_PARAMS = PACKAGE_ROOT.parent.parent / "examples" / "blank_instrument_params.yaml"

VALID_MELODIC = """
  - instrument: {instrument}
    waveform: sine
    amp_envelope: {{attack: 10, decay: 100, sustain: 500, release: 100, amplitude: {amplitude}}}
    pitch_envelope: {{attack: 0, decay: 0, sustain: 0, release: 0, amplitude: 0}}
    amp_lfo: {{frequency: 0, amplitude: 0}}
    pitch_lfo: {{frequency: 0, amplitude: 0}}
"""


def test_replacing_with_instrument_zero_keeps_the_comment() -> None:
    result = tuning.convert_replaced(MIDI, melodics=0)
    assert result.comments == ("melodics replaced with MIDI instrument 0",)
    assert result.to_typescript("hex").splitlines()[:2] == [
        GENERATED_BY,
        "// melodics replaced with MIDI instrument 0",
    ]


def test_replacing_both_lists_melodics_then_drums() -> None:
    result = tuning.convert_replaced(MIDI, melodics=0, drums=38)
    assert result.comments == (
        "melodics replaced with MIDI instrument 0",
        "drums replaced with MIDI drum note 38",
    )


def test_allow_incomplete_loads_the_blank_params() -> None:
    text = BLANK_PARAMS.read_text(encoding="utf-8")
    with pytest.raises(InstrumentParamsError):
        load_instrument_params(text)
    load_instrument_params(text, allow_incomplete=True)


def test_sample_drums_plays_one_labelled_song_per_note() -> None:
    out = tuning.sample_drums(MIDI, [36, 38])
    lines = out.splitlines()
    assert lines[:3] == [
        GENERATED_BY,
        "// generated drum sample",
        "// drum notes [36, 38]",
    ]
    assert out.count("music.play(") == 2
    assert "// drums replaced with MIDI drum note 36\ninfo.setScore(36);" in out
    assert "// drums replaced with MIDI drum note 38\ninfo.setScore(38);" in out


def test_sample_melodics_reads_a_stream_once() -> None:
    assert tuning.sample_melodics(io.BytesIO(MIDI), [0, 1, 2]) == (
        GOLDEN / "sample_0-2.ts"
    ).read_text(encoding="utf-8")


def test_report_lists_what_the_fixture_uses() -> None:
    usages = tuning.report(MIDI)
    assert [(u.kind, u.number) for u in usages] == [
        ("melodic", 0),
        ("melodic", 48),
        ("drum", 36),
        ("drum", 38),
        ("drum", 42),
    ]
    assert all(u.mapped for u in usages)
    piano, strings = usages[0], usages[1]
    assert (piano.note_count, piano.lowest_note, piano.highest_note) == (14, 53, 67)
    assert (piano.min_velocity, piano.max_velocity) == (60, 100)
    assert (strings.note_count, strings.lowest_note, strings.highest_note) == (
        3,
        48,
        55,
    )
    drums = {u.number: u.note_count for u in usages if u.kind == "drum"}
    assert drums == {36: 4, 38: 4, 42: 16}


def test_report_marks_unmapped_instead_of_raising() -> None:
    mapping = load_instrument_params(default_instrument_params_text())
    del mapping.melodic_instruments[48]
    mapped = {(u.kind, u.number): u.mapped for u in tuning.report(MIDI, mapping)}
    assert mapped[("melodic", 48)] is False
    assert mapped[("melodic", 0)] is True


def test_check_params_finds_nothing_in_the_bundled_params() -> None:
    assert tuning.check_params(default_instrument_params_text()) == []


def test_check_params_reports_problems() -> None:
    text = (
        "melodic_instruments:"
        + VALID_MELODIC.format(instrument=0, amplitude=500)
        + VALID_MELODIC.format(instrument=0, amplitude=2000)
        + "  - instrument: 1\n"
        + "drum_instruments: []\n"
    )
    problems = tuning.check_params(text)
    assert problems == [
        "Invalid melodic instrument entry (instrument 1): missing field 'waveform'",
        "Duplicate melodic instrument 0 (2 entries; the last one is used)",
        "Melodic instrument 0 amp_envelope amplitude is 2000, expected 0-1024",
        "No entry for melodic instruments 1-127",
        "No entry for drum notes 27-87",
    ]


def test_check_params_on_a_mapping_skips_duplicates() -> None:
    mapping = load_instrument_params(default_instrument_params_text())
    del mapping.drum_instruments[40]
    assert tuning.check_params(mapping) == ["No entry for drum notes 40"]


def test_check_params_still_raises_for_unparseable_yaml() -> None:
    with pytest.raises(InstrumentParamsError):
        tuning.check_params("melodic_instruments: [")
