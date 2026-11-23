# https://github.com/microsoft/pxt/blob/master/pxtsim/sound/sequencer.ts
# Ported from microsoft/pxt @ 19bd210 (2026-11-19): the frequency table,
# tickToMs, the volume formulas and what Sequencer.onTick plays on each tick.
# The real-time parts (the metronome worker, listeners, looping, cancelling,
# the spatial audio player) are not ported; sequence_song walks the ticks
# offline instead.
#
# Timing (pxtsim/sound/metronome.ts): the metronome worker calls
# setInterval(tick, tickToMs(bpm, tpb, 1)), and setInterval converts its delay
# to an integer number of ms (WebIDL `long`), so at 120 BPM and 24 ticks/beat a
# tick is 20 ms, not 20.833 ms. Gate lengths still use the unrounded tickToMs.
# See metronome_interval_ms.
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from midi2mkcd.arcade.music_types import Song
from midi2mkcd.arcade.sim_song import (
    _js_to_int32,
    render_drum_instrument,
    render_instrument,
)

logger = logging.getLogger(__name__)

# Integer Hz. Index 0 is B0 (MIDI 23), index 37 is C4 (MIDI 60, 262 Hz).
# fmt: off
FREQUENCIES: tuple[int, ...] = (
    31, 33, 35, 37, 39, 41, 44, 46, 49, 52, 55, 58, 62, 65, 69, 73,
    78, 82, 87, 92, 98, 104, 110, 117, 123, 131, 139, 147, 156, 165, 175, 185, 196, 208,
    220, 233, 247, 262, 277, 294, 311, 330, 349, 370, 392, 415, 440, 466, 494, 523, 554,
    587, 622, 659, 698, 740, 784, 831, 880, 932, 988, 1047, 1109, 1175, 1245, 1319, 1397,
    1480, 1568, 1661, 1760, 1865, 1976, 2093, 2217, 2349, 2489, 2637, 2794, 2960, 3136,
    3322, 3520, 3729, 3951, 4186, 4435, 4699, 4978, 5274, 5588, 5920, 6272, 6645, 7040,
    7459, 7902,
)
# fmt: on

# Sequencer.globalVolume starts at 1024, but in a game _SimulatorSequencer
# sets it to music.volume(), which defaults to 128 (CFG_SPEAKER_VOLUME in
# pxt-common-packages libs/mixer/melody.ts; music.setVolume allows 0-255).
SEQUENCER_DEFAULT_VOLUME = 1024
MUSIC_DEFAULT_VOLUME = 128
FULL_VOLUME = 1024


def tick_to_ms(beats_per_minute: float, ticks_per_beat: float, ticks: float) -> float:
    """Port of `tickToMs`."""
    return ((60000 / beats_per_minute) / ticks_per_beat) * ticks


def metronome_interval_ms(song: Song) -> int:
    """How far apart ticks actually fire: `setInterval` truncates
    `tickToMs(bpm, tpb, 1)` to an integer (WebIDL `long`, i.e. ToInt32) and
    clamps negative delays to 0.

    Not modelled: HTML raises delays below 4 ms to 4 ms once timers nest more
    than 5 deep, which only matters for tempos far above anything the
    converter writes."""
    return max(
        _js_to_int32(tick_to_ms(song.beats_per_minute, song.ticks_per_beat, 1)), 0
    )


def note_frequency(note: int) -> float:
    """`frequencies[note]`. An index outside the table is `undefined` in JS,
    which renderInstrument turns into NaN and then 0 Hz; NaN stands in for it
    here (render_instrument handles it)."""
    if 0 <= note < len(FREQUENCIES):
        return FREQUENCIES[note]
    return math.nan


def clamp_volume(volume: float) -> float:
    """`Math.min(Math.max(volume, 0), 1024)`, as in setVolume,
    setTrackVolume and setDrumTrackVolume."""
    return min(max(volume, 0), FULL_VOLUME)


def melodic_track_volume(
    global_volume: float, velocity: float, track_volume: float = FULL_VOLUME
) -> float:
    """Port of `getMelodicTrackVolume` (pass already-clamped volumes)."""
    return global_volume * (track_volume / 1024) * (velocity / 128)


def drum_track_volume(
    global_volume: float,
    velocity: float,
    track_volume: float = FULL_VOLUME,
    drum_volume: float = FULL_VOLUME,
) -> float:
    """Port of `getDrumTrackVolume` (pass already-clamped volumes)."""
    return melodic_track_volume(global_volume, velocity, track_volume) * (
        drum_volume / 1024
    )


def max_tick(song: Song) -> int:
    """Port of `Sequencer.maxTick`."""
    return song.measures * song.beats_per_measure * song.ticks_per_beat


@dataclass(frozen=True)
class SequencedSound:
    """One `playNoteAsync` / `playDrumAsync` call made by `onTick`."""

    tick: int
    track_index: int
    instructions: bytes


def sequence_song(
    song: Song, global_volume: float = MUSIC_DEFAULT_VOLUME
) -> list[SequencedSound]:
    """Every sound `Sequencer.onTick` starts while playing `song` once (not
    looping), in the order it starts them.

    `global_volume` is what the game passed to setVolume (music.volume(),
    default 128); it is clamped to 0-1024 like setVolume. Track and drum
    volumes are left at their defaults (1024), as a song played with
    `music.play` has them.

    What onTick does, reproduced here:

    - Ticks 0, 1, ... are played until the tick count reaches maxTick, then it
      stops with `stop(true)` (sounding notes keep playing). Tick 0 is played
      even when maxTick is 0, since the check comes after the first tick.
    - On each tick, each track's note events are scanned in order; an event
      plays if its startTick equals the tick, and the scan **stops at the
      first event that starts later**. So an event placed after a later-
      starting event in the track's list never plays (encoded songs are
      sorted, so this only matters for hand-made buffers).
    - Every note of an event is a separate call with `velocity ?? 128`.
      Melodic notes use `frequencies[note]` and gate
      `tickToMs(endTick - startTick)`; drum notes play `track.drums[note]`.
      A track counts as a drum track whenever it has a drums list, even an
      empty one (an empty JS array is truthy).
    - A drum index past the track's list, or a drum with no steps, throws
      inside the async play call in the simulator, so it is silent. Those
      are skipped here (logged at DEBUG).
    """
    global_volume = clamp_volume(global_volume)
    tick_count = max(max_tick(song), 1)

    # (tick, track index, event index, note index, sound)
    played: list[tuple[int, int, int, int, SequencedSound]] = []
    for track_index, track in enumerate(song.tracks):
        latest_start = -math.inf
        for event_index, event in enumerate(track.notes):
            # The scan for tick t reaches this event only if no earlier event
            # starts after t; it plays at t = its own startTick.
            reachable = latest_start <= event.start_tick
            latest_start = max(latest_start, event.start_tick)
            if not reachable:
                logger.debug(
                    "Track %d event %d (tick %d) comes after a later event; "
                    "the simulator never plays it",
                    track_index,
                    event_index,
                    event.start_tick,
                )
                continue
            if not 0 <= event.start_tick < tick_count:
                continue

            velocity = event.velocity if event.velocity is not None else 128
            for note_index, note in enumerate(event.notes):
                if track.drums is not None:
                    if not 0 <= note.note < len(track.drums):
                        logger.debug(
                            "Track %d tick %d: drum index %d out of range; silent",
                            track_index,
                            event.start_tick,
                            note.note,
                        )
                        continue
                    drum = track.drums[note.note]
                    if not drum.steps:
                        logger.debug(
                            "Track %d tick %d: drum %d has no steps; silent",
                            track_index,
                            event.start_tick,
                            note.note,
                        )
                        continue
                    instructions = render_drum_instrument(
                        drum, drum_track_volume(global_volume, velocity)
                    )
                else:
                    instructions = render_instrument(
                        track.instrument,
                        note_frequency(note.note),
                        tick_to_ms(
                            song.beats_per_minute,
                            song.ticks_per_beat,
                            event.end_tick - event.start_tick,
                        ),
                        melodic_track_volume(global_volume, velocity),
                    )
                played.append(
                    (
                        event.start_tick,
                        track_index,
                        event_index,
                        note_index,
                        SequencedSound(event.start_tick, track_index, instructions),
                    )
                )

    played.sort(key=lambda p: p[:4])
    return [p[4] for p in played]
