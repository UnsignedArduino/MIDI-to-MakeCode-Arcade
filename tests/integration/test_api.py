import io
import logging
from copy import deepcopy
from importlib.metadata import version
from pathlib import Path

import pytest
from helpers import FIXTURE_MIDI, GOLDEN

from midi2mkcd import (
    InstrumentParamsError,
    InvalidMidiError,
    Midi2MkcdError,
    __version__,
    api,
    convert,
    default_instrument_params_text,
    load_instrument_params,
)

MIDI = FIXTURE_MIDI.read_bytes()
EXPECTED_HEX = (GOLDEN / "hex.ts").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "midi",
    [
        MIDI,
        bytearray(MIDI),
        memoryview(MIDI),
        io.BytesIO(MIDI),
        FIXTURE_MIDI,
        str(FIXTURE_MIDI),
    ],
    ids=["bytes", "bytearray", "memoryview", "BytesIO", "Path", "str path"],
)
def test_convert_accepts_every_midi_input(midi: object) -> None:
    assert convert(midi).to_typescript() == EXPECTED_HEX  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "params",
    [
        None,
        default_instrument_params_text(),
        load_instrument_params(default_instrument_params_text()),
    ],
    ids=["default", "yaml text", "mapping"],
)
def test_convert_accepts_every_params_input(params: object) -> None:
    assert convert(MIDI, params).to_typescript() == EXPECTED_HEX  # type: ignore[arg-type]


def test_convert_does_not_mutate_params() -> None:
    mapping = load_instrument_params(default_instrument_params_text())
    before = deepcopy(mapping)
    convert(MIDI, mapping)
    assert mapping == before


def test_result_metadata() -> None:
    result = convert(MIDI)
    assert result.note_event_count == sum(len(t.notes) for t in result.song.tracks)
    assert result.warnings == []
    assert result.comments == ()
    assert result.lyric_texts == ["Hel", "lo ", "café ", "world"]


def test_garbage_bytes_raise_invalid_midi() -> None:
    with pytest.raises(InvalidMidiError):
        convert(b"definitely not a MIDI file")


def test_missing_path_raises_os_error(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        convert(tmp_path / "missing.mid")


def test_malformed_yaml_raises_params_error() -> None:
    with pytest.raises(InstrumentParamsError):
        convert(MIDI, "melodic_instruments: [")


def test_unmapped_instrument_raises_params_error() -> None:
    mapping = load_instrument_params(default_instrument_params_text())
    del mapping.melodic_instruments[48]
    with pytest.raises(InstrumentParamsError, match="MIDI instruments 48"):
        convert(MIDI, mapping)


def test_input_errors_share_a_base_class() -> None:
    assert issubclass(InvalidMidiError, Midi2MkcdError)
    assert issubclass(InstrumentParamsError, Midi2MkcdError)


def test_small_song_has_no_warnings() -> None:
    result = convert(MIDI)
    assert result.warnings == []
    assert 0 < result.note_event_count < api.SIMULATOR_NOTE_WARNING_THRESHOLD


def test_simulator_warning_at_threshold(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    count = convert(MIDI).note_event_count
    # No fixture is anywhere near 15k notes, so lower the threshold to this song
    monkeypatch.setattr(api, "SIMULATOR_NOTE_WARNING_THRESHOLD", count)
    with caplog.at_level(logging.WARNING, logger="midi2mkcd"):
        result = convert(MIDI)
    assert len(result.warnings) == 1
    assert f"{count} note events" in result.warnings[0]
    assert [r.getMessage() for r in caplog.records] == result.warnings
    # One below the threshold stays quiet
    monkeypatch.setattr(api, "SIMULATOR_NOTE_WARNING_THRESHOLD", count + 1)
    assert convert(MIDI).warnings == []


def test_version_matches_installed_metadata() -> None:
    assert __version__ == version("midi2mkcd")
