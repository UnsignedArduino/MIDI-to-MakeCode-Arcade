import io
import logging
from collections import Counter
from copy import deepcopy
from importlib.metadata import version
from pathlib import Path

import pytest
from helpers import FIXTURE_MIDI, GOLDEN
from mido import Message, MidiFile, MidiTrack

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
from midi2mkcd.arcade.music import decode_song_from_hex

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


def long_drum_midi() -> tuple[bytes, Counter[int]]:
    """
    A drum part long enough to be split into two drum tracks by the 16-bit note byte
    limit, with different drums before and after the split. Returns the MIDI bytes
    and how many times each drum note is hit.
    """
    mid = MidiFile(type=0, ticks_per_beat=480)  # 120 BPM: one song tick = 20 ticks
    track = MidiTrack()
    mid.tracks.append(track)
    hits: Counter[int] = Counter()
    # One drum per note event is 6 note bytes, so ~10.9k events fill a track
    for i in range(12000):
        pair = (38, 42) if i < 11000 else (36, 49)
        drum = pair[i % 2]
        hits[drum] += 1
        gap = 0 if i == 0 else 20  # one song tick of silence between hits
        track.append(Message("note_on", channel=9, note=drum, velocity=100, time=gap))
        track.append(Message("note_off", channel=9, note=drum, velocity=0, time=20))
    buf = io.BytesIO()
    mid.save(file=buf)
    return buf.getvalue(), hits


def test_drum_map_is_right_for_every_drum_track() -> None:
    midi, hits = long_drum_midi()
    mapping = load_instrument_params(default_instrument_params_text())
    result = convert(midi, mapping)
    drum_map = result.drum_idx_to_midi_drum
    drum_tracks = [t for t in result.song.tracks if t.drums is not None]
    assert len(drum_tracks) == 2
    recovered: Counter[int] = Counter()
    for track in drum_tracks:
        assert track.drums is not None
        for i, drum in enumerate(track.drums):
            assert drum == mapping.drum_instruments[drum_map[i]]
        for event in track.notes:
            recovered.update(drum_map[n.note] for n in event.notes)
    assert recovered == hits


def every_note_midi(notes: range) -> bytes:
    """One melodic program playing each note in `notes` once, one after another."""
    mid = MidiFile(type=0, ticks_per_beat=480)
    track = MidiTrack()
    mid.tracks.append(track)
    for i, note in enumerate(notes):
        gap = 0 if i == 0 else 40  # two song ticks of silence between notes
        track.append(Message("note_on", channel=0, note=note, velocity=100, time=gap))
        track.append(Message("note_off", channel=0, note=note, velocity=0, time=40))
    buf = io.BytesIO()
    mid.save(file=buf)
    return buf.getvalue()


@pytest.mark.parametrize(
    ("notes", "tracks"),
    [
        # Shifted (MIDI - 23) -23..104: windows -24..39, 36..99, 96..159
        (range(128), 3),
        (range(23), 1),
        # 37..104: windows 36..99, 96..159
        (range(60, 128), 2),
        # -3..66 covers the old [0, 64) / [60, 124) overlap: windows -12..51, 48..111
        (range(20, 90), 2),
    ],
    ids=["all", "below table", "upper half", "across the old overlap"],
)
def test_range_splitting_keeps_every_note_once(notes: range, tracks: int) -> None:
    result = convert(every_note_midi(notes))
    # Decode the encoded song, so octave offsets and note bytes are checked too
    song = decode_song_from_hex(result.to_hex())
    played = Counter(n.note + 23 for t in song.tracks for e in t.notes for n in e.notes)
    assert played == Counter(notes)
    assert len(song.tracks) == tracks


def test_version_matches_installed_metadata() -> None:
    assert __version__ == version("midi2mkcd")
