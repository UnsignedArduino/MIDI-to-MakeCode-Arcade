# https://github.com/microsoft/pxt/blob/master/pxtlib/music.ts
import struct
from typing import Tuple

from arcade.music_types import *
from utils.logger import create_logger

logger = create_logger(name=__name__, level=logging.INFO)


def set8BitNumber(buf: bytearray, offset: int, value: int):
    struct.pack_into("<B", buf, offset, value & 0xFF)


def get8BitNumber(buf: bytearray, offset: int) -> int:
    return struct.unpack_from("<B", buf, offset)[0]


def set16BitNumber(buf: bytearray, offset: int, value: int):
    struct.pack_into("<H", buf, offset, value & 0xFFFF)


def get16BitNumber(buf: bytearray, offset: int) -> int:
    return struct.unpack_from("<H", buf, offset)[0]


def encodeSong(song: Song) -> bytearray:
    """
    Encode a MakeCode Arcade song into a bytearray. Ported from
    https://github.com/microsoft/pxt/blob/master/pxtlib/music.ts#L96

    :param song: The MakeCode Arcade song.
    :return: A bytearray, convert this to hex to use in a MakeCode Arcade program.
    """
    encodedTracks = [encodeTrack(track) for track in song.tracks if
                     len(track.notes) > 0]
    encodedTrackVelocities: List[bytearray] = list(filter(lambda v: v is not None,
                                                          [encodeTrackVelocity(track)
                                                           for track in
                                                           song.tracks]))

    trackLength = sum(len(c) for c in (encodedTracks + encodedTrackVelocities))

    out = bytearray(7 + trackLength)
    set8BitNumber(out, 0, 0)  # encoding version
    set16BitNumber(out, 1, song.beatsPerMinute)
    set8BitNumber(out, 3, song.beatsPerMeasure)
    set8BitNumber(out, 4, song.ticksPerBeat)
    set8BitNumber(out, 5, song.measures)
    set8BitNumber(out, 6, len(encodedTracks))

    current = 7
    for track in encodedTracks:
        out[current:current + len(track)] = track
        current += len(track)

    for trackVelocity in encodedTrackVelocities:
        out[current:current + len(trackVelocity)] = trackVelocity
        current += len(trackVelocity)

    return out


def encodeInstrument(instrument: Instrument) -> bytearray:
    """
    Encode a MakeCode Arcade instrument into a bytearray. Ported from
    https://github.com/microsoft/pxt/blob/master/pxtlib/music.ts#L127

    :param instrument: The MakeCode Arcade instrument.
    :return: A bytearray.
    """
    out = bytearray(28)
    set8BitNumber(out, 0, instrument.waveform)
    set16BitNumber(out, 1, instrument.ampEnvelope.attack)
    set16BitNumber(out, 3, instrument.ampEnvelope.decay)
    set16BitNumber(out, 5, instrument.ampEnvelope.sustain)
    set16BitNumber(out, 7, instrument.ampEnvelope.release)
    set16BitNumber(out, 9, instrument.ampEnvelope.amplitude)
    if instrument.pitchEnvelope is not None:
        set16BitNumber(out, 11, instrument.pitchEnvelope.attack)
        set16BitNumber(out, 13, instrument.pitchEnvelope.decay)
        set16BitNumber(out, 15, instrument.pitchEnvelope.sustain)
        set16BitNumber(out, 17, instrument.pitchEnvelope.release)
        set16BitNumber(out, 19, instrument.pitchEnvelope.amplitude)
    else:
        set16BitNumber(out, 11, 0)
        set16BitNumber(out, 13, 0)
        set16BitNumber(out, 15, 0)
        set16BitNumber(out, 17, 0)
        set16BitNumber(out, 19, 0)
    if instrument.ampLFO is not None:
        set8BitNumber(out, 21, instrument.ampLFO.frequency)
        set16BitNumber(out, 22, instrument.ampLFO.amplitude)
    else:
        set8BitNumber(out, 21, 0)
        set16BitNumber(out, 22, 0)
    if instrument.pitchLFO is not None:
        set8BitNumber(out, 24, instrument.pitchLFO.frequency)
        set16BitNumber(out, 25, instrument.pitchLFO.amplitude)
    else:
        set8BitNumber(out, 24, 0)
        set16BitNumber(out, 25, 0)
    set8BitNumber(out, 27, instrument.octave if instrument.octave is not None else 0)
    return out


def encodeDrumInstrument(drum: DrumInstrument) -> bytearray:
    """
    Encode a MakeCode Arcade drum instrument into a bytearray. Ported from
    https://github.com/microsoft/pxt/blob/master/pxtlib/music.ts#L149

    :param drum: The MakeCode Arcade drum instrument.
    :return: A bytearray.
    """
    out = bytearray(5 + 7 * len(drum.steps))
    set8BitNumber(out, 0, len(drum.steps))
    set16BitNumber(out, 1, drum.startFrequency)
    set16BitNumber(out, 3, drum.startVolume)
    for i, step in enumerate(drum.steps):
        start = 5 + i * 7
        set8BitNumber(out, start, step.waveform)
        set16BitNumber(out, start + 1, step.frequency)
        set16BitNumber(out, start + 3, step.volume)
        set16BitNumber(out, start + 5, step.duration)
    return out


def encodeNoteEvent(event: NoteEvent, instrumentOctave: int,
                    isDrumTrack: bool) -> bytearray:
    """
    Encode a MakeCode Arcade note event into a bytearray. Ported from
    https://github.com/microsoft/pxt/blob/master/pxtlib/music.ts#L166

    :param event: The MakeCode Arcade `NoteEvent`.
    :param instrumentOctave: The instrument octave offset used for the note event.
    :param isDrumTrack: Whether this note event is for a drum track or not.
    :return: A bytearray.
    """
    out = bytearray(5 + len(event.notes))
    set16BitNumber(out, 0, event.startTick)
    set16BitNumber(out, 2, event.endTick)
    set8BitNumber(out, 4, len(event.notes))

    for i, note in enumerate(event.notes):
        set8BitNumber(out, 5 + i, encodeNote(note, instrumentOctave, isDrumTrack))

    return out


def encodeNote(note: Note, instrumentOctave: int, isDrumTrack: bool) -> int:
    """
    Encode a MakeCode Arcade note into a single byte. Ported from
    https://github.com/microsoft/pxt/blob/master/pxtlib/music.ts#L179

    :param note: The MakeCode Arcade `Note`.
    :param instrumentOctave: The instrument octave offset used for the note.
    :param isDrumTrack: Whether this note is for a drum track or not.
    :return: An int, which will fit into a single byte.
    """
    if isDrumTrack:
        return note.note

    flags = 0
    if note.enharmonicSpelling == EnharmonicSpelling.FLAT:
        flags = 1
    elif note.enharmonicSpelling == EnharmonicSpelling.SHARP:
        flags = 2

    return (note.note - (instrumentOctave - 2) * 12) | (flags << 6)


def encodeTrack(track: Track) -> bytearray:
    """
    Encode a MakeCode Arcade track into a bytearray. Ported from
    https://github.com/microsoft/pxt/blob/master/pxtlib/music.ts#L195

    :param track: The MakeCode Arcade `Track`.
    :return: A bytearray.
    """
    if track.drums:
        return encodeDrumTrack(track)
    else:
        return encodeMelodicTrack(track)


def encodeTrackVelocity(track: Track) -> Optional[bytearray]:
    """
    Encode a MakeCode Arcade track's velocity data into a bytearray. Ported from
    https://github.com/microsoft/pxt/blob/master/pxtlib/music.ts#L200

    :param track: The MakeCode Arcade `Track`.
    :return: A bytearray.
    """
    if not any([note.velocity is not None and note.velocity < 128 for note in
                track.notes]):
        return None

    out = bytearray(1 + len(track.notes))
    set8BitNumber(out, 0, track.id)
    for i, note in enumerate(track.notes):
        set8BitNumber(out, 1 + i, note.velocity if note.velocity is not None else 0)
    return out


def encodeMelodicTrack(track: Track) -> bytearray:
    """
    Encode a MakeCode Arcade melodic track into a bytearray. Ported from
    https://github.com/microsoft/pxt/blob/master/pxtlib/music.ts#L211

    :param track: The MakeCode Arcade `Track`, must be melodic.
    :return: A bytearray.
    """
    encodedInstrument = encodeInstrument(track.instrument)
    encodedNotes = [encodeNoteEvent(note, track.instrument.octave, False) for note in
                    track.notes]
    noteLength = sum(len(c) for c in encodedNotes)

    out = bytearray(6 + len(encodedInstrument) + noteLength)
    set8BitNumber(out, 0, track.id)
    set8BitNumber(out, 1, 0)

    set16BitNumber(out, 2, len(encodedInstrument))
    current = 4
    out[current:current + len(encodedInstrument)] = encodedInstrument
    current += len(encodedInstrument)

    set16BitNumber(out, current, noteLength)
    current += 2
    for note in encodedNotes:
        out[current:current + len(note)] = note
        current += len(note)

    return out


def encodeDrumTrack(track: Track) -> bytearray:
    """
    Encode a MakeCode Arcade drum track into a bytearray. Ported from
    https://github.com/microsoft/pxt/blob/master/pxtlib/music.ts#L235

    :param track: The MakeCode Arcade `Track`, must be a drum track.
    :return: A bytearray.
    """
    assert track.drums is not None
    encodedDrums = [encodeDrumInstrument(drum) for drum in track.drums]
    drumLength = sum(len(c) for c in encodedDrums)

    encodedNotes = [encodeNoteEvent(note, 0, True) for note in track.notes]
    noteLength = sum(len(c) for c in encodedNotes)

    out = bytearray(6 + drumLength + noteLength)
    set8BitNumber(out, 0, track.id)
    set8BitNumber(out, 1, 1)
    set16BitNumber(out, 2, drumLength)
    current = 4

    for drum in encodedDrums:
        out[current:current + len(drum)] = drum
        current += len(drum)

    set16BitNumber(out, current, noteLength)
    current += 2
    for note in encodedNotes:
        out[current:current + len(note)] = note
        current += len(note)

    return out


def decodeSong(buf: bytearray) -> Song:
    """
    Decode a MakeCode Arcade song from a bytearray. Ported from
    https://github.com/microsoft/pxt/blob/master/pxtlib/music.ts#L269

    :param buf: A bytearray of an entire song.
    :return: A MakeCode Arcade `Song`.
    """
    res = Song(beatsPerMinute=get16BitNumber(buf, 1),
               beatsPerMeasure=get8BitNumber(buf, 3),
               ticksPerBeat=get8BitNumber(buf, 4),
               measures=get8BitNumber(buf, 5),
               tracks=[])

    numTracks = get8BitNumber(buf, 6)
    current = 7

    for _ in range(numTracks):
        track, pointer = decodeTrack(buf, current)
        current = pointer
        res.tracks.append(track)

    while current < len(buf):
        current = decodeTrackVelocity(buf, res.tracks, current)

    return res


def decodeInstrument(buf: bytearray, offset: int) -> Instrument:
    """
    Decode a MakeCode Arcade instrument from a bytearray. Ported from
    https://github.com/microsoft/pxt/blob/master/pxtlib/music.ts#L294

    :param buf: A bytearray of an entire song.
    :param offset: The offset in the bytearray which to start reading the instrument
     data from.
    :return: A MakeCode Arcade `Instrument`.
    """
    return Instrument(
        waveform=get8BitNumber(buf, offset),
        ampEnvelope=Envelope(
            attack=get16BitNumber(buf, offset + 1),
            decay=get16BitNumber(buf, offset + 3),
            sustain=get16BitNumber(buf, offset + 5),
            release=get16BitNumber(buf, offset + 7),
            amplitude=get16BitNumber(buf, offset + 9),
        ),
        pitchEnvelope=Envelope(
            attack=get16BitNumber(buf, offset + 11),
            decay=get16BitNumber(buf, offset + 13),
            sustain=get16BitNumber(buf, offset + 15),
            release=get16BitNumber(buf, offset + 17),
            amplitude=get16BitNumber(buf, offset + 19),
        ),
        ampLFO=LFO(
            frequency=get8BitNumber(buf, offset + 21),
            amplitude=get16BitNumber(buf, offset + 22),
            # the original implementation is 22 instead of offset + 22
        ),
        pitchLFO=LFO(
            frequency=get8BitNumber(buf, offset + 24),
            amplitude=get16BitNumber(buf, offset + 25),
            # the original implementation is 25 instead of offset + 25
        ),
        octave=get8BitNumber(buf, offset + 27),
    )


def decodeTrack(buf: bytearray, offset: int) -> Tuple[Track, int]:
    """
    Decode a MakeCode Arcade track from a bytearray. Ported from
    https://github.com/microsoft/pxt/blob/master/pxtlib/music.ts#L323

    :param buf: A bytearray of an entire song.
    :param offset: The offset in the bytearray which to start reading the track
     data from.
    :return: A MakeCode Arcade `Track`.
    """
    if get8BitNumber(buf, offset + 1) != 0:
        return decodeDrumTrack(buf, offset)
    else:
        return decodeMelodicTrack(buf, offset)


def decodeTrackVelocity(buf: bytearray, tracks: List[Track], offset: int) -> int:
    """
    Decode a MakeCode Arcade track velocity from a bytearray. Ported from
    https://github.com/microsoft/pxt/blob/master/pxtlib/music.ts#L331

    :param buf: A bytearray of an entire song.
    :param tracks: A list of `Track` objects which have already been decoded from the
     bytearray, the one with the matching ID will have the note velocities set.
    :param offset: The offset in the bytearray which to start reading the track velocity
     data from.
    :return: The next offset after the end of this track velocity data.
    """
    trackId = get8BitNumber(buf, offset)
    track = next((t for t in tracks if t.id == trackId), None)
    if track is None:
        raise ValueError(f"Track with {trackId} not found")
    for i in range(len(track.notes)):
        track.notes[i].velocity = get8BitNumber(buf, offset + i + 1)
    return offset + len(track.notes) + 1


def decodeDrumInstrument(buf: bytearray, offset: int) -> DrumInstrument:
    """
    Decode a MakeCode Arcade drum instrument from a bytearray. Ported from
    https://github.com/microsoft/pxt/blob/master/pxtlib/music.ts#L341

    :param buf: A bytearray of an entire song.
    :param offset: The offset in the bytearray which to start reading the drum
     instrument data from.
    :return: A MakeCode Arcade `DrumInstrument`.
    """
    res = DrumInstrument(
        startFrequency=get16BitNumber(buf, offset + 1),
        startVolume=get16BitNumber(buf, offset + 3),
        steps=[],
    )

    for i in range(get8BitNumber(buf, offset)):
        start = offset + 5 + i * 7
        res.steps.append(DrumSoundStep(
            waveform=get8BitNumber(buf, start),
            frequency=get16BitNumber(buf, start + 1),
            volume=get16BitNumber(buf, start + 3),
            duration=get16BitNumber(buf, start + 5)
        ))

    return res


def decodeNoteEvent(buf: bytearray, offset: int, instrumentOctave: int,
                    isDrumTrack: bool) -> NoteEvent:
    """
    Decode a MakeCode Arcade note event from a bytearray. Ported from
    https://github.com/microsoft/pxt/blob/master/pxtlib/music.ts#L361

    :param buf: A bytearray of an entire song.
    :param offset: The offset in the bytearray which to start reading the note event
     data from.
    :param instrumentOctave: The instrument's octave offset used for the note event,
     needed to decode the note value itself.
    :param isDrumTrack: Whether this note event is for a drum track or not, needed to
     decode the note.
    :return: A MakeCode Arcade `NoteEvent`.
    """
    res = NoteEvent(
        startTick=get16BitNumber(buf, offset),
        endTick=get16BitNumber(buf, offset + 2),
        notes=[],
    )

    for i in range(get8BitNumber(buf, offset + 4)):
        res.notes.append(
            decodeNote(
                get8BitNumber(buf, offset + 5 + i),
                instrumentOctave,
                isDrumTrack
            )
        )

    return res


def decodeNote(note: int, instrumentOctave: int, isDrumTrack: bool) -> Note:
    """
    Construct a MakeCode Arcade note from specified parameters. Ported from
    https://github.com/microsoft/pxt/blob/master/pxtlib/music.ts#L374

    :param note: The note number itself, between 0 and 63. For melodic tracks, the
     instrument octave is taken into account.
    :param instrumentOctave: The track's instrument's octave offset.
    :param isDrumTrack: Whether this note is for a drum track or not.
    :return: A MakeCode Arcade `Note`.
    """
    flags = note >> 6
    res = Note(
        note=note if isDrumTrack else ((note & 0x3F) + (instrumentOctave - 2) * 12),
        enharmonicSpelling=EnharmonicSpelling.NORMAL
    )

    if flags == 1:
        res.enharmonicSpelling = EnharmonicSpelling.FLAT
    elif flags == 2:
        res.enharmonicSpelling = EnharmonicSpelling.SHARP

    return res


def decodeMelodicTrack(buf: bytearray, offset: int) -> Tuple[Track, int]:
    """
    Decode a MakeCode Arcade melodic track from a bytearray. Ported from
    https://github.com/microsoft/pxt/blob/master/pxtlib/music.ts#L392

    :param buf: A bytearray of an entire song.
    :param offset: The offset in the bytearray which to start reading the melodic track
     data from.
    :return: A tuple of a MakeCode Arcade `Track` and the next offset after the end of
     this track data.
    """
    res = Track(
        id=get8BitNumber(buf, offset),
        instrument=decodeInstrument(buf, offset + 4),
        notes=[]
    )

    noteStart = offset + 4 + get16BitNumber(buf, offset + 2)
    noteLength = get16BitNumber(buf, noteStart)

    currentOffset = noteStart + 2

    while currentOffset < noteStart + 2 + noteLength:
        res.notes.append(
            decodeNoteEvent(buf, currentOffset, res.instrument.octave, False)
        )
        currentOffset += 5 + len(res.notes[-1].notes)

    return res, currentOffset


def decodeDrumTrack(buf: bytearray, offset: int) -> Tuple[Track, int]:
    """
    Decode a MakeCode Arcade drum track from a bytearray. Ported from
    https://github.com/microsoft/pxt/blob/master/pxtlib/music.ts#L412

    :param buf: A bytearray of an entire song.
    :param offset: The offset in the bytearray which to start reading the drum track
     data from.
    :return: A tuple of a MakeCode Arcade `Track` and the next offset after the end of
     this track data.
    """
    res = Track(
        id=get8BitNumber(buf, offset),
        instrument=Instrument(
            ampEnvelope=Envelope(attack=0, decay=0, sustain=0, release=0, amplitude=0),
            waveform=0, octave=0),
        notes=[],
        drums=[]
    )

    drumByteLength = get16BitNumber(buf, offset + 2)
    currentOffset = offset + 4

    while currentOffset < (offset + 4 + drumByteLength):
        res.drums.append(decodeDrumInstrument(buf, currentOffset))
        currentOffset += 5 + 7 * len(res.drums[-1].steps)

    noteLength = get16BitNumber(buf, currentOffset)
    currentOffset += 2

    while currentOffset < (offset + 4 + drumByteLength + noteLength):
        res.notes.append(decodeNoteEvent(buf, currentOffset, 0, True))
        currentOffset += 5 + len(res.notes[-1].notes)

    return res, currentOffset
