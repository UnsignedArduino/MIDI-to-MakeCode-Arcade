import pytest

from midi2mkcd.api import ConversionResult
from midi2mkcd.arcade.music_types import Song
from midi2mkcd.render import GENERATED_BY, to_typescript, usage_report
from midi2mkcd.tuning import InstrumentUsage


def make_result(comments: tuple[str, ...] = ()) -> ConversionResult:
    return ConversionResult(
        song=Song(
            measures=1,
            beats_per_measure=4,
            beats_per_minute=120,
            ticks_per_beat=24,
            tracks=[],
        ),
        drum_idx_to_midi_drum=[],
        track_idx_to_midi_instrument=[],
        lyric_ticks=[],
        lyric_texts=[],
        note_event_count=0,
        warnings=[],
        comments=comments,
    )


def test_plain_hex_has_no_header() -> None:
    assert to_typescript(make_result(), "hex").startswith("hex`")


@pytest.mark.parametrize("fmt", ["code", "code_extra"])
def test_other_formats_have_the_header(fmt: str) -> None:
    out = to_typescript(make_result(), fmt)  # type: ignore[arg-type]
    assert out.splitlines()[0] == GENERATED_BY


def test_comments_add_the_header_even_to_hex() -> None:
    out = to_typescript(make_result(("hello",)), "hex")
    assert out.splitlines()[:3] == [GENERATED_BY, "// hello", out.splitlines()[2]]
    assert out.splitlines()[2].startswith("hex`")


def test_unknown_format_raises() -> None:
    with pytest.raises(ValueError, match="Unknown output format"):
        to_typescript(make_result(), "wav")  # type: ignore[arg-type]


def test_usage_report_table() -> None:
    usages = [
        InstrumentUsage("melodic", 0, 14, 53, 67, 60, 100, True),
        InstrumentUsage("drum", 36, 4, 36, 36, 100, 100, False),
    ]
    assert usage_report(usages) == (
        "kind     number  name                  notes  range  velocity  mapped\n"
        "melodic  0       Acoustic Grand Piano  14     53-67  60-100    yes\n"
        "drum     36      Bass Drum 1           4      36-36  100-100   NO"
    )


def test_usage_report_names_unknown_numbers() -> None:
    usages = [
        InstrumentUsage("melodic", 128, 1, 60, 60, 1, 1, False),
        InstrumentUsage("drum", 20, 1, 20, 20, 1, 1, False),
    ]
    rows = usage_report(usages).splitlines()[1:]
    assert [row.split()[2] for row in rows] == ["?", "?"]
