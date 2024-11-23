import logging
from collections import namedtuple
from math import ceil
from sys import stdout
from typing import Union

from mido import Message, MidiFile
from tqdm import tqdm

from arcade.music import EnharmonicSpelling, Note, NoteEvent, Song, Track, \
    getEmptySong
from arcade.tracks import get_available_tracks
from utils.logger import create_logger

logger = create_logger(name=__name__, level=logging.INFO)


def midi_to_song(midi: MidiFile, track_id: Union[str, int],
                 divisor: float) -> Song:
    def get_track_from_name_or_id(name_or_id: Union[int, str]) -> Track:
        logger.debug(f"Finding track {name_or_id}")
        for track in get_available_tracks():
            if name_or_id == track.name.lower() or name_or_id == track.id:
                selected_track = track
                break
        else:
            raise ValueError(f"Unknown track ID or name {name_or_id}!")
        logger.debug(f"Found track '{selected_track.name}' ({selected_track})")
        return selected_track

    def find_note_time(start_index: int, note: int,
                       msgs: list[Message]) -> float:
        time = 0
        for i in range(start_index, len(msgs)):
            msg = msgs[i]
            if msg.type not in ("note_on", "note_off"):
                continue
            time += msg.time
            if ((msg.type == "note_on" and msg.velocity == 0) or
                msg.type == "note_off") and msg.note == note:
                break
        return time

    NoteInfo = namedtuple("NoteInfo",
                          "note_value note_time start_tick end_tick")

    def gather_note_info(index: int, msgs: list[Message],
                         current_time: int) -> NoteInfo:
        msg = msgs[index]
        if msg.type != "note_on":
            return NoteInfo(
                note_value=-1,
                note_time=-1,
                start_tick=-1,
                end_tick=-1
            )
        note_time = round(find_note_time(index + 1, msg.note, msgs) * 1000)
        start_tick = round(current_time / 10)
        end_tick = start_tick + round(note_time / 10)
        return NoteInfo(
            note_value=msg.note,
            note_time=note_time,
            start_tick=start_tick,
            end_tick=end_tick
        )

    NoteSimpleEvent = namedtuple("NoteSimpleEvent",
                                 "note start_tick end_tick")
    ChordSimpleEvent = namedtuple("ChordSimpleEvent",
                                  "notes start_tick end_tick")

    def find_chord_with_start_tick(chords: list[ChordSimpleEvent],
                                   start_tick: int) -> int:
        for i, chord in enumerate(chords):
            if chord.start_tick == start_tick:
                return i
        return -1

    def add_tracks_for_piano(song: Song, track_id: Union[int, str]):
        selected_track = get_track_from_name_or_id(track_id)
        selected_higher_track = get_track_from_name_or_id(track_id)
        song.tracks.append(selected_track)
        song.tracks[-1].instrument.octave = 2
        song.tracks.append(selected_higher_track)
        song.tracks[-1].instrument.octave = 7
        logger.debug(f"Added 2 piano tracks")

    msgs = list(midi)
    simple_notes = []

    curr_time = 0
    ending_tick = 0
    for i, msg in tqdm(enumerate(msgs), desc="Processing (stage 1/3)",
                       file=stdout):
        curr_time += round(msg.time * 1000)
        if msg.type not in ("note_on", "note_off"):
            continue
        # logger.debug(f"{i}: {msg} (current time: {curr_time})")
        if msg.type == "note_off" or (
                msg.type == "note_on" and msg.velocity == 0):
            pass
        else:
            note_info = gather_note_info(i, msgs, curr_time)
            note_simple_event = NoteSimpleEvent(note_info.note_value,
                                                note_info.start_tick,
                                                note_info.end_tick)
            ending_tick = max(ending_tick, note_info.end_tick)
            # duration = (note_simple_event.end_tick -
            #             note_simple_event.start_tick)
            # logger.debug(f"{i}: * {note_simple_event} "
            #              f"(duration: {duration})")
            simple_notes.append(note_simple_event)

    logger.debug(f"Last tick is {ending_tick} ({round(ending_tick / divisor)} "
                 f"after divisor)")

    ending_tick = round(ending_tick / divisor)

    simple_chords = []
    for note in tqdm(simple_notes, desc="Processing (stage 2/3)", file=stdout):
        chord_index = find_chord_with_start_tick(simple_chords,
                                                 note.start_tick)
        if chord_index == -1:
            simple_chords.append(
                ChordSimpleEvent([note.note], note.start_tick, note.end_tick)
            )
        else:
            simple_chords[chord_index].notes.append(note.note)

    ticks_per_beat = 100
    beats_per_measure = 10
    beats_per_minute = round(60 / divisor)
    measure_count = ceil(ending_tick / ticks_per_beat / beats_per_measure)

    logger.debug(
        f"{measure_count=} {ticks_per_beat=} {beats_per_measure=} {beats_per_minute=}")
    logger.debug(f"Maximum number of ticks is "
                 f"{measure_count * ticks_per_beat * beats_per_measure} ticks")

    song = getEmptySong(measure_count)
    song.ticksPerBeat = ticks_per_beat
    song.beatsPerMeasure = beats_per_measure
    song.beatsPerMinute = beats_per_minute
    song.tracks.clear()
    add_tracks_for_piano(song, track_id)

    for i, chord in tqdm(enumerate(simple_chords), desc="Processing (stage 3/3)",
                         file=stdout):
        # logger.debug(f"Chord {i}: {chord}")
        all_notes = [Note(note=n, enharmonicSpelling=EnharmonicSpelling.NORMAL)
                     for n in chord.notes]
        notes = []
        higher_notes = []
        for note in all_notes:
            instrumentOctave = song.tracks[-2].instrument.octave
            note_val = (note.note - (instrumentOctave - 2) * 12)
            note_val += 1 - 12
            if note_val > 63:
                higher_notes.append(note)
            else:
                notes.append(note)
        event = NoteEvent(
            notes=notes,
            startTick=round(chord.start_tick / divisor),
            endTick=round(chord.end_tick / divisor)
        )
        higher_event = NoteEvent(
            notes=higher_notes,
            startTick=round(chord.start_tick / divisor),
            endTick=round(chord.end_tick / divisor)
        )
        # logger.debug(f"Note event {i}: {event}")
        if len(event.notes) > 0:
            song.tracks[-2].notes.append(event)
        if len(higher_event.notes) > 0:
            song.tracks[-1].notes.append(higher_event)
        ending_tick = chord.end_tick

    for i, track in enumerate(song.tracks):
        logger.debug(
            f"Created {len(song.tracks[i].notes)} note events in track {i}")
    logger.debug(
        f"Total of {sum([len(t.notes) for t in song.tracks])} note events")
    logger.debug(f"Last tick is {ending_tick}")

    return song
