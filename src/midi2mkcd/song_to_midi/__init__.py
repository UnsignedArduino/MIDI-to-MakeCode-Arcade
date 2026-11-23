"""
Recover a MIDI file from a converted MakeCode Arcade song. Lossy, and not meant for
round trips: it is an escape hatch for when the original MIDI file is lost.

Programs and drum notes come from a `code_extra` file's constants when present, else
from matching each track's instrument against the params file. Pitch, timing and
velocity come from the song itself, with pitch compensation undone using the params
file's `melodic_pitch_comp_k`. Lyrics only survive in `code_extra` files.
"""

import io
import logging
from collections.abc import Sequence
from dataclasses import dataclass

from mido import Message, MetaMessage, MidiFile, MidiTrack, bpm2tempo

from midi2mkcd.api import ParamsInput, _resolve_params
from midi2mkcd.arcade.music import encode_drum_instrument, encode_instrument
from midi2mkcd.arcade.music_types import Track
from midi2mkcd.midi_to_song.instruments import InstrumentParameterMapping
from midi2mkcd.midi_to_song.timeline.processor import pitch_compensation_scale
from midi2mkcd.utils.gm_names import melodic_instrument_name
from midi2mkcd.utils.song_text import SongText, decode_song_hex, parse_song_text

__all__ = ["MIDI_TICKS_PER_BEAT", "song_to_midi"]

logger = logging.getLogger(__name__)

MIDI_TICKS_PER_BEAT = 480
# Arcade note index 0 is MIDI 23 (B0), the first entry of the simulator's table
ARCADE_NOTE_TO_MIDI = 23
DRUM_CHANNEL = 9
# Every channel but 10 (index 9), which is drums on every port
MELODIC_CHANNELS = [c for c in range(16) if c != DRUM_CHANNEL]
# The melodic instrument's octave byte, which the params entries don't set
INSTRUMENT_BYTES_WITHOUT_OCTAVE = 27


@dataclass(frozen=True)
class _RecoveredNote:
    start_tick: int  # song ticks
    end_tick: int
    note: int  # MIDI note
    velocity: int  # 1-127


def song_to_midi(text: str, params: ParamsInput = None) -> bytes:
    """
    Turn generated TypeScript holding one song back into a MIDI file.

    :param text: Text with exactly one ``hex`...` `` literal: `convert` output in any
     format. A `code_extra` file's constants give exact programs, drum notes and
     lyrics.
    :param params: The instrument params the song was converted with, as YAML text,
     a loaded mapping, or None for the bundled defaults. Used to match instruments
     (unless the text is `code_extra`) and for `melodic_pitch_comp_k`.
    :return: A type 1 MIDI file at 120 BPM.
    :raises InvalidSongError: If the text doesn't hold exactly one valid song.
    :raises InstrumentParamsError: If the params are invalid.
    """
    song_text = parse_song_text(text)
    song = decode_song_hex(song_text.hex)
    if params is None:
        logger.info(
            "Using the bundled default params to match instruments and undo pitch "
            "compensation"
        )
    mapping = _resolve_params(params)
    k = mapping.melodic_pitch_comp_k
    logger.debug(f"Undoing pitch compensation with {k=}")

    tracks = [track for track in song.tracks if track.notes]
    programs = _track_programs(tracks, song_text, mapping)

    melodic: dict[int, list[_RecoveredNote]] = {}
    drums: list[_RecoveredNote] = []
    drum_map = song_text.midi_drum_note_map if song_text.is_code_extra else None
    for track, program in zip(tracks, programs, strict=True):
        if track.drums is not None:
            drums.extend(_drum_notes(track, drum_map, mapping))
        else:
            melodic.setdefault(program, []).extend(_melodic_notes(track, k))

    melodic_slots = _assign_slots(
        [
            _Part(program, lane)
            for program, notes in melodic.items()
            for lane in _split_lanes(notes)
        ],
        MELODIC_CHANNELS,
    )
    drum_slots = _assign_slots(
        [_Part(None, lane) for lane in _split_lanes(drums)], [DRUM_CHANNEL]
    )
    slots = melodic_slots + drum_slots
    port_count = 1 + max((slot.port for slot in slots), default=0)
    if port_count > 1:
        logger.info(
            f"The recovered song uses {len(melodic_slots)} melodic and {len(drum_slots)} "
            f"drum channels, so the MIDI file uses {port_count} ports"
        )

    midi = MidiFile(type=1, ticks_per_beat=MIDI_TICKS_PER_BEAT)
    midi.tracks.append(_conductor(song_text))
    for slot in slots:
        # Every track names its port once there are several: players such as
        # SpessaSynth put tracks without one on the lowest port that is named
        midi.tracks.append(_slot_track(slot, write_port=port_count > 1))

    buf = io.BytesIO()
    midi.save(file=buf)
    logger.debug(
        f"Recovered {len(melodic)} melodic programs and {len(drums)} drum notes"
    )
    return buf.getvalue()


def _track_programs(
    tracks: Sequence[Track], song_text: SongText, mapping: InstrumentParameterMapping
) -> list[int]:
    """The MIDI program of each track, -1 for drum tracks."""
    if song_text.is_code_extra:
        programs = song_text.track_instrument_map
        if len(programs) == len(tracks):
            for track, program in zip(tracks, programs, strict=True):
                if (program == -1) != (track.drums is not None):
                    break
            else:
                logger.debug("Using the programs from trackInstrumentMap")
                return list(programs)
        logger.warning(
            "trackInstrumentMap doesn't match the song's tracks; matching "
            "instruments against the params instead"
        )
    return [
        -1 if track.drums is not None else _match_program(track, mapping)
        for track in tracks
    ]


def _match_program(track: Track, mapping: InstrumentParameterMapping) -> int:
    """Find the params entry whose instrument the track plays (octave ignored)."""
    wanted = _instrument_key(track)
    matches = sorted(
        program
        for program, instrument in mapping.melodic_instruments.items()
        if encode_instrument(instrument)[:INSTRUMENT_BYTES_WITHOUT_OCTAVE] == wanted
    )
    if not matches:
        logger.warning(
            f"Track {track.id}'s instrument matches no params entry; using program 0"
        )
        return 0
    if len(matches) > 1:
        logger.info(
            f"Track {track.id}'s instrument matches programs {matches}; using "
            f"{matches[0]}"
        )
    return matches[0]


def _instrument_key(track: Track) -> bytes:
    return bytes(encode_instrument(track.instrument)[:INSTRUMENT_BYTES_WITHOUT_OCTAVE])


def _melodic_notes(track: Track, k: float) -> list[_RecoveredNote]:
    notes = []
    out_of_range = 0
    for event in track.notes:
        for note in event.notes:
            midi_note = note.note + ARCADE_NOTE_TO_MIDI
            if not 0 <= midi_note <= 127:
                out_of_range += 1
                continue
            velocity = _velocity(event.velocity)
            velocity = round(velocity / pitch_compensation_scale(note.note, k))
            notes.append(
                _RecoveredNote(
                    start_tick=event.start_tick,
                    end_tick=event.end_tick,
                    note=midi_note,
                    velocity=min(max(velocity, 1), 127),
                )
            )
    if out_of_range:
        logger.warning(
            f"Dropped {out_of_range} notes in track {track.id} outside MIDI 0-127"
        )
    return notes


def _drum_notes(
    track: Track, drum_map: list[int] | None, mapping: InstrumentParameterMapping
) -> list[_RecoveredNote]:
    assert track.drums is not None
    used = {note.note for event in track.notes for note in event.notes}
    if drum_map is not None and all(i < len(drum_map) for i in used):
        index_to_note: dict[int, int | None] = {i: drum_map[i] for i in used}
    else:
        if drum_map is not None:
            logger.info(
                f"midiDrumNoteMap is too short for drum track {track.id} (made by an "
                f"older converter?); matching its drums against the params instead"
            )
        index_to_note = {
            i: _match_drum(track, i, mapping) if i < len(track.drums) else None
            for i in used
        }
    notes = []
    dropped = 0
    for event in track.notes:
        for note in event.notes:
            midi_note = index_to_note[note.note]
            if midi_note is None:
                dropped += 1
                continue
            notes.append(
                _RecoveredNote(
                    start_tick=event.start_tick,
                    end_tick=event.end_tick,
                    note=midi_note,
                    velocity=_velocity(event.velocity),
                )
            )
    if dropped:
        logger.warning(
            f"Dropped {dropped} hits in drum track {track.id} whose drum matches no "
            f"params entry"
        )
    return notes


def _match_drum(
    track: Track, index: int, mapping: InstrumentParameterMapping
) -> int | None:
    """Find the params drum note whose sound is drum `index` of the track."""
    assert track.drums is not None
    wanted = encode_drum_instrument(track.drums[index])
    matches = sorted(
        note
        for note, drum in mapping.drum_instruments.items()
        if encode_drum_instrument(drum) == wanted
    )
    if not matches:
        logger.warning(f"Drum {index} of track {track.id} matches no params entry")
        return None
    if len(matches) > 1:
        logger.info(
            f"Drum {index} of track {track.id} matches drum notes {matches}; using "
            f"{matches[0]}"
        )
    return matches[0]


def _velocity(velocity: int | None) -> int:
    """A note event's velocity as MIDI velocity (missing means Arcade's 128)."""
    return min(max(128 if velocity is None else velocity, 1), 127)


def _midi_tick(song_tick: int) -> int:
    return song_tick * MIDI_TICKS_PER_BEAT // 24


def _conductor(song_text: SongText) -> MidiTrack:
    events: list[tuple[int, MetaMessage]] = [
        (0, MetaMessage("set_tempo", tempo=bpm2tempo(120))),
        (0, MetaMessage("time_signature", numerator=4, denominator=4)),
    ]
    for tick, lyric in zip(song_text.lyric_ticks, song_text.lyric_texts, strict=False):
        # mido writes text as latin-1, and the converter reads lyrics back as UTF-8
        # when they decode as it
        text = lyric.encode("utf-8").decode("latin-1")
        events.append((_midi_tick(tick), MetaMessage("lyrics", text=text)))
    if len(song_text.lyric_ticks) != len(song_text.lyric_texts):
        logger.warning("lyricTicks and lyricTexts have different lengths")
    return _track_from_events("Conductor", events)


@dataclass
class _Part:
    """Notes of one program (None for drums) that can share one MIDI channel."""

    program: int | None
    notes: list[_RecoveredNote]

    @property
    def start(self) -> int:
        return self.notes[0].start_tick

    @property
    def end(self) -> int:
        return max(n.end_tick for n in self.notes)


@dataclass
class _Slot:
    """One MIDI channel on one port, holding parts that don't overlap in time."""

    port: int
    channel: int
    parts: list[_Part]


def _split_lanes(notes: list[_RecoveredNote]) -> list[list[_RecoveredNote]]:
    """
    Split notes into lanes that each pair up correctly on one channel.

    The converter (like most players) ends the earliest-started sounding note of a
    pitch at each note off, so on one channel a same-pitch note must not end before
    one that started earlier. A note that would (e.g. a short hit inside a long one)
    goes to the next lane, which needs its own channel. Duplicates are kept: the
    song plays them, so converting again gives the same song.
    """
    lanes: list[list[_RecoveredNote]] = []
    latest_end: list[dict[int, int]] = []
    for note in sorted(notes, key=lambda n: (n.start_tick, n.end_tick, n.note)):
        for lane, ends in zip(lanes, latest_end, strict=True):
            if ends.get(note.note, -1) <= note.end_tick:
                break
        else:
            lane, ends = [], {}
            lanes.append(lane)
            latest_end.append(ends)
        lane.append(note)
        ends[note.note] = note.end_tick
    return lanes


def _assign_slots(parts: list[_Part], channels: list[int]) -> list[_Slot]:
    """
    Give each part a channel. Each part gets its own channel while port 0 has free
    ones; after that, parts share a channel with parts that ended before they start
    (with a program change in between), and only then spill onto more ports.
    """
    slots: list[_Slot] = []
    busy_until: list[int] = []
    for part in sorted(parts, key=lambda p: p.start):
        if len(slots) < len(channels):
            index = None
        else:
            index = next(
                (i for i, end in enumerate(busy_until) if end <= part.start), None
            )
        if index is None:
            port, i = divmod(len(slots), len(channels))
            slots.append(_Slot(port=port, channel=channels[i], parts=[]))
            busy_until.append(0)
            index = len(slots) - 1
        slots[index].parts.append(part)
        busy_until[index] = part.end
    return slots


def _slot_track(slot: _Slot, *, write_port: bool) -> MidiTrack:
    """The MIDI track for one channel: its parts' program changes and notes."""
    channel = slot.channel
    events: list[tuple[int, Message | MetaMessage]] = []
    if write_port:
        events.append((0, MetaMessage("midi_port", port=slot.port)))
    names: list[str] = []
    for part in slot.parts:
        if part.program is None:
            names.append("Drums")
        else:
            names.append(melodic_instrument_name(part.program))
            tick = 0 if part is slot.parts[0] else _midi_tick(part.start)
            events.append(
                (tick, Message("program_change", channel=channel, program=part.program))
            )
        # Note offs sort before note ons at the same tick, so a note can restart
        for n in part.notes:
            events.append(
                (
                    _midi_tick(n.start_tick),
                    Message(
                        "note_on", channel=channel, note=n.note, velocity=n.velocity
                    ),
                )
            )
            events.append(
                (
                    _midi_tick(n.end_tick),
                    Message("note_off", channel=channel, note=n.note),
                )
            )
    return _track_from_events(" / ".join(dict.fromkeys(names)), events)


def _event_order(event: tuple[int, Message | MetaMessage]) -> tuple[int, int]:
    tick, msg = event
    if msg.is_meta or msg.type == "program_change":
        return tick, 0
    return tick, 1 if msg.type == "note_off" else 2


def _track_from_events(
    name: str, events: list[tuple[int, Message | MetaMessage]]
) -> MidiTrack:
    track = MidiTrack()
    track.append(MetaMessage("track_name", name=name, time=0))
    now = 0
    for tick, msg in sorted(events, key=_event_order):
        track.append(msg.copy(time=tick - now))
        now = tick
    track.append(MetaMessage("end_of_track", time=0))
    return track
