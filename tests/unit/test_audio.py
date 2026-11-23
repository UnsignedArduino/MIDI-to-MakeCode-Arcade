"""Tests for audio.py's Stage 1 wrappers (the ports themselves are checked
against real pxt output in test_sim_goldens.py)."""

import logging
import math

import pytest

from midi2mkcd.arcade.music_types import (
    DrumInstrument,
    DrumSoundStep,
    EnharmonicSpelling,
    Envelope,
    Instrument,
    Note,
    NoteEvent,
    Song,
    Track,
)
from midi2mkcd.arcade.sim_sequencer import sequence_song
from midi2mkcd.arcade.sim_song import render_drum_instrument, render_instrument
from midi2mkcd.audio import drum_instructions, note_instructions, song_events

INSTRUMENT = Instrument(
    waveform=3,
    octave=4,
    amp_envelope=Envelope(attack=0, decay=0, sustain=1024, release=0, amplitude=1024),
)
DRUM = DrumInstrument(100, 1024, [DrumSoundStep(5, 200, 0, 30)])


def notes(*indices: int) -> list[Note]:
    return [Note(i, EnharmonicSpelling.NORMAL) for i in indices]


def song(*tracks: Track) -> Song:
    return Song(
        measures=2,
        beats_per_measure=4,
        beats_per_minute=120,
        ticks_per_beat=24,
        tracks=list(tracks),
    )


def test_note_instructions_uses_the_frequency_table() -> None:
    assert note_instructions(INSTRUMENT, 37, 500, 127) == render_instrument(
        INSTRUMENT, 262, 500, 127
    )
    assert note_instructions(INSTRUMENT, 97, 500, 127) == render_instrument(
        INSTRUMENT, math.nan, 500, 127
    )


def test_drum_instructions() -> None:
    assert drum_instructions(DRUM, 64) == render_drum_instrument(DRUM, 64)
    with pytest.raises(ValueError):
        drum_instructions(DrumInstrument(100, 1024, []), 64)


def test_song_events_start_every_20_ms_per_tick() -> None:
    track = Track(
        0,
        INSTRUMENT,
        [NoteEvent(notes(37), 0, 24, velocity=64), NoteEvent(notes(37), 150, 160)],
    )
    events = song_events(song(track))
    assert [start for start, _ in events] == [0.0, 3000.0]  # not 3125
    assert [i for _, i in events] == [
        s.instructions for s in sequence_song(song(track))
    ]


def test_song_events_without_velocity() -> None:
    track = Track(0, INSTRUMENT, [NoteEvent(notes(37), 0, 24, velocity=64)])
    s = song(track)
    (with_velocity,) = song_events(s)
    (without,) = song_events(s, use_velocity=False)
    assert with_velocity[1] == render_instrument(INSTRUMENT, 262, 500, 64)
    assert without[1] == render_instrument(INSTRUMENT, 262, 500, 128)
    assert track.notes[0].velocity == 64  # the input song is unchanged


def test_song_events_global_volume_and_drums() -> None:
    melodic = Track(0, INSTRUMENT, [NoteEvent(notes(37), 0, 24)])
    drums = Track(1, INSTRUMENT, [NoteEvent(notes(0), 24, 25)], drums=[DRUM])
    events = song_events(song(melodic, drums), global_volume=255)
    assert events == [
        (0.0, render_instrument(INSTRUMENT, 262, 500, 255)),
        (480.0, render_drum_instrument(DRUM, 255)),
    ]


def test_song_events_logs_out_of_range_notes(caplog: pytest.LogCaptureFixture) -> None:
    # Decoded note indices; the table covers 0-96
    track = Track(0, INSTRUMENT, [NoteEvent(notes(96, 97, 100), 0, 24)])
    with caplog.at_level(logging.INFO, logger="midi2mkcd.audio"):
        song_events(song(track))
    assert "2 melodic note(s)" in caplog.text
