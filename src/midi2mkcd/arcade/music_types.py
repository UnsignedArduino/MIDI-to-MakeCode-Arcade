# https://github.com/microsoft/pxt/blob/master/localtypings/pxtmusic.d.ts
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

from midi2mkcd.utils.logger import create_logger

logger = create_logger(name=__name__, level=logging.INFO)


# /**
#  * Byte encoding format for songs
#  * FIXME: should this all be word aligned?
#  *
#  * song(7 + length of all tracks bytes)
#  *     0 version
#  *     1 beats per minute
#  *     3 beats per measure
#  *     4 ticks per beat
#  *     5 measures
#  *     6 number of tracks
#  *     ...tracks
#  *     ...track velocities
#  *
#  * track(6 + instrument length + note length bytes)
#  *     0 id
#  *     1 flags
#  *     2 instruments byte length
#  *     4...instrument
#  *     notes byte length
#  *     ...note events
#  *
#  * instrument(28 bytes)
#  *     0 waveform
#  *     1 amp attack
#  *     3 amp decay
#  *     5 amp sustain
#  *     7 amp release
#  *     9 amp amp
#  *     11 pitch attack
#  *     13 pitch decay
#  *     15 pitch sustain
#  *     17 pitch release
#  *     19 pitch amp
#  *     21 amp lfo freq
#  *     22 amp lfo amp
#  *     24 pitch lfo freq
#  *     25 pitch lfo amp
#  *     27 octave
#  *
#  * drum(5 + 7 * steps bytes)
#  *     0 steps
#  *     1 start freq
#  *     3 start amp
#  *     5...steps
#  *
#  * drum step(7 bytes)
#  *     0 waveform
#  *     1 freq
#  *     3 volume
#  *     5 duration
#  *
#  * note event(5 + 1 * polyphony bytes)
#  *     0 start tick
#  *     2 end tick
#  *     4 polyphony
#  *     5...notes(1 byte each)
#  *
#  * note (1 byte)
#  *     lower six bits = note - (instrumentOctave - 2) * 12
#  *     upper two bits are the enharmonic spelling:
#  *          0 = normal
#  *          1 = flat
#  *          2 = sharp
#  *
#  * track velocity
#  *     0 track id
#  *     1...velocities
#  *
#  * velocty
#  *     1 byte
#  */


@dataclass
class Instrument:
    waveform: int
    octave: int
    amp_envelope: Envelope
    pitch_envelope: Optional[Envelope] = None
    amp_lfo: Optional[LFO] = None
    pitch_lfo: Optional[LFO] = None


@dataclass
class Envelope:
    attack: int
    decay: int
    sustain: int
    release: int
    amplitude: int


@dataclass
class LFO:
    frequency: int
    amplitude: int


@dataclass
class SongInfo:
    measures: int
    beats_per_measure: int
    beats_per_minute: int
    ticks_per_beat: int


@dataclass
class Song(SongInfo):
    tracks: List[Track]


@dataclass
class Track:
    id: int
    instrument: Instrument
    notes: List[NoteEvent]
    drums: Optional[List[DrumInstrument]] = None
    name: Optional[str] = None
    icon_uri: Optional[str] = None


@dataclass
class NoteEvent:
    notes: List[Note]
    start_tick: int
    end_tick: int
    velocity: Optional[int] = None


class EnharmonicSpelling(Enum):
    NORMAL = "normal"
    FLAT = "flat"
    SHARP = "sharp"


@dataclass
class Note:
    note: int
    enharmonic_spelling: EnharmonicSpelling


@dataclass
class DrumSoundStep:
    waveform: int
    frequency: int
    volume: int
    duration: int


@dataclass
class DrumInstrument:
    start_frequency: int
    start_volume: int
    steps: List[DrumSoundStep]
    name: Optional[str] = None
