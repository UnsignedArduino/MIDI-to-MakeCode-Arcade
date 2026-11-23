"""Tests for the port of pxtsim/sound/sequencer.ts."""

import math

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
from midi2mkcd.arcade.sim_sequencer import (
    FREQUENCIES,
    metronome_interval_ms,
    note_frequency,
    sequence_song,
    tick_to_ms,
)
from midi2mkcd.arcade.sim_song import render_drum_instrument, render_instrument

INSTRUMENT = Instrument(
    waveform=3,
    octave=4,
    amp_envelope=Envelope(attack=0, decay=0, sustain=1024, release=0, amplitude=1024),
)
DRUM = DrumInstrument(
    start_frequency=100,
    start_volume=1024,
    steps=[DrumSoundStep(waveform=5, frequency=200, volume=0, duration=30)],
)


def notes(*indices: int) -> list[Note]:
    return [Note(i, EnharmonicSpelling.NORMAL) for i in indices]


def song(tracks: list[Track], measures: int = 1) -> Song:
    return Song(
        measures=measures,
        beats_per_measure=4,
        beats_per_minute=120,
        ticks_per_beat=24,
        tracks=tracks,
    )


def test_frequency_table() -> None:
    assert len(FREQUENCIES) == 97
    assert FREQUENCIES[0] == 31
    assert FREQUENCIES[37] == 262
    assert FREQUENCIES[96] == 7902
    assert note_frequency(37) == 262
    assert math.isnan(note_frequency(97))
    assert math.isnan(note_frequency(-1))  # not FREQUENCIES[-1]


def test_timing() -> None:
    s = song([])
    assert tick_to_ms(120, 24, 24) == 500
    assert metronome_interval_ms(s) == 20  # 20.833 truncated by setInterval


def test_melodic_notes_volume_and_gate() -> None:
    track = Track(
        id=0,
        instrument=INSTRUMENT,
        notes=[
            NoteEvent(notes(37, 41), start_tick=0, end_tick=24, velocity=64),
            NoteEvent(notes(37), start_tick=24, end_tick=48),  # no velocity: 128
        ],
    )
    sounds = sequence_song(song([track]))
    assert [(s.tick, s.track_index) for s in sounds] == [(0, 0), (0, 0), (24, 0)]
    # volume = 128 (music.volume()) * 64/128 = 64; gate = 500 ms
    assert sounds[0].instructions == render_instrument(INSTRUMENT, 262, 500, 64)
    assert sounds[1].instructions == render_instrument(INSTRUMENT, 330, 500, 64)
    assert sounds[2].instructions == render_instrument(INSTRUMENT, 262, 500, 128)


def test_global_volume_is_clamped() -> None:
    track = Track(0, INSTRUMENT, [NoteEvent(notes(37), 0, 24, velocity=128)])
    loud = sequence_song(song([track]), global_volume=5000)
    assert loud[0].instructions == render_instrument(INSTRUMENT, 262, 500, 1024)


def test_order_is_tick_then_track() -> None:
    a = Track(0, INSTRUMENT, [NoteEvent(notes(37), 0, 1), NoteEvent(notes(37), 5, 6)])
    b = Track(1, INSTRUMENT, [NoteEvent(notes(37), 0, 1)])
    sounds = sequence_song(song([a, b]))
    assert [(s.tick, s.track_index) for s in sounds] == [(0, 0), (0, 1), (5, 0)]


def test_event_after_a_later_event_never_plays() -> None:
    track = Track(
        0,
        INSTRUMENT,
        [
            NoteEvent(notes(37), 10, 11),
            NoteEvent(notes(37), 5, 6),  # the scan breaks at tick 10 first
            NoteEvent(notes(37), 10, 11),
            NoteEvent(notes(37), 20, 21),
        ],
    )
    assert [s.tick for s in sequence_song(song([track]))] == [10, 10, 20]


def test_events_at_or_after_max_tick_do_not_play() -> None:
    track = Track(
        0, INSTRUMENT, [NoteEvent(notes(37), 95, 96), NoteEvent(notes(37), 96, 97)]
    )
    assert [s.tick for s in sequence_song(song([track]))] == [95]  # maxTick 96


def test_tick_0_plays_even_with_no_measures() -> None:
    track = Track(
        0, INSTRUMENT, [NoteEvent(notes(37), 0, 1), NoteEvent(notes(37), 1, 2)]
    )
    assert [s.tick for s in sequence_song(song([track], measures=0))] == [0]


def test_drums() -> None:
    track = Track(
        id=0,
        instrument=INSTRUMENT,
        notes=[NoteEvent(notes(0, 1, 2), 0, 1, velocity=64)],
        drums=[DRUM, DrumInstrument(100, 1024, [])],  # index 1: no steps
    )
    sounds = sequence_song(song([track]))
    # index 1 has no steps and index 2 is past the list: both silent
    assert len(sounds) == 1
    assert sounds[0].instructions == render_drum_instrument(DRUM, 64)


def test_empty_drum_list_is_still_a_drum_track() -> None:
    track = Track(0, INSTRUMENT, [NoteEvent(notes(37), 0, 1)], drums=[])
    assert sequence_song(song([track])) == []
