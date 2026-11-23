"""Audio rendering: an Arcade song played the way the MakeCode simulator plays it.

Stage 1 (this module so far) turns notes and songs into the simulator's
12-byte "play instructions", byte-identical to pxt. It is plain Python over
the line-by-line ports in `arcade/sim_song.py` and `arcade/sim_sequencer.py`,
and needs no optional dependencies.

Stage 2 (`synthesize`, `render_song`, `to_wav_bytes`; not written yet) turns
instructions into samples and needs numpy (the `audio` extra). numpy must only
ever be imported inside those functions, so this module stays importable
without it.

Not re-exported from the package root.
"""

from __future__ import annotations

import logging
import math
from dataclasses import replace

from midi2mkcd.arcade.music_types import DrumInstrument, Instrument, Song
from midi2mkcd.arcade.sim_sequencer import (
    MUSIC_DEFAULT_VOLUME,
    metronome_interval_ms,
    note_frequency,
    sequence_song,
)
from midi2mkcd.arcade.sim_song import render_drum_instrument, render_instrument

logger = logging.getLogger(__name__)

# A sound the simulator starts: (start time in ms, its play instructions)
SongEvent = tuple[float, bytes]


def note_instructions(
    instrument: Instrument, note_index: int, gate_ms: float, volume: float
) -> bytes:
    """Play instructions for one melodic note.

    `note_index` is the decoded Arcade note (MIDI note - 23, after the track's
    octave offset). An index outside the simulator's 0-96 frequency table
    plays at 0 Hz, as in the simulator. `volume` is the sequencer volume,
    `music.volume() * velocity / 128` (127 for velocity 127 at the default
    music volume)."""
    return render_instrument(instrument, note_frequency(note_index), gate_ms, volume)


def drum_instructions(drum: DrumInstrument, volume: float) -> bytes:
    """Play instructions for one drum hit. Raises ``ValueError`` for a drum
    with no steps (which the simulator can't play; it stays silent)."""
    return render_drum_instrument(drum, volume)


def song_events(
    song: Song,
    *,
    use_velocity: bool = True,
    global_volume: float = MUSIC_DEFAULT_VOLUME,
) -> list[SongEvent]:
    """Every sound the simulator starts while playing `song` once, in the
    order it starts them, as (start ms, instructions).

    Start times use the simulator's real tick spacing (the metronome's
    truncated `setInterval` delay: 20 ms for converted songs, so they play 4%
    fast), counted from the first tick. Gates use the exact tick length.

    `use_velocity=False` plays every note event at velocity 128, as Arcade
    versions that ignore the velocity section do. `global_volume` is
    `music.volume()` (default 128, a game can set 0-255; clamped to 0-1024 as
    in the simulator)."""
    if not use_velocity:
        song = replace(
            song,
            tracks=[
                replace(
                    track,
                    notes=[replace(event, velocity=None) for event in track.notes],
                )
                for track in song.tracks
            ],
        )

    interval = metronome_interval_ms(song)
    sounds = sequence_song(song, global_volume)
    events = [(float(s.tick * interval), s.instructions) for s in sounds]
    out_of_range = _count_out_of_range_notes(song)
    if out_of_range:
        logger.info(
            "%d melodic note(s) are outside the simulator's frequency table "
            "(MIDI 23-119) and play at 0 Hz",
            out_of_range,
        )
    logger.debug("%d sounds, tick interval %d ms", len(events), interval)
    return events


def _count_out_of_range_notes(song: Song) -> int:
    return sum(
        1
        for track in song.tracks
        if track.drums is None
        for event in track.notes
        for note in event.notes
        if math.isnan(note_frequency(note.note))
    )
