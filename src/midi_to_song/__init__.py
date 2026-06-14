import logging
from copy import deepcopy
from math import ceil
from typing import Dict, List, Optional

from mido import MidiFile

from arcade.music_types import EnharmonicSpelling, Envelope, Instrument, Note, \
    NoteEvent, Song, Track
from midi_to_song.instruments import InstrumentParameterMapping
from midi_to_song.models import AbsoluteCompleteChordWithTick, AbsoluteCompleteNote, \
    AbsoluteCompleteNoteWithTick, AbsoluteTickMessage, AbsoluteTimeMessage, \
    AbsoluteTimeMessageWithInstrument, ChannelState, DrumDeterminationSource, \
    TestingOptionsForMIDIToSong
from midi_to_song.timeline.parser import timeline_build, timeline_find_instrument_data, \
    timeline_group_messages
from midi_to_song.timeline.processor import find_all_drum_chords_used, \
    timeline_fix_gate_lens, timeline_group_by_instrument, \
    timeline_group_into_perfect_chords, \
    timeline_quantize_to_song_ticks, timeline_resolve_overlapping_chords, \
    timeline_split_tracks_for_ranges
from midi_to_song.timeline.validation import timeline_checks
from utils.logger import create_logger

logger = create_logger(name=__name__, level=logging.INFO)


def convert_midi_to_song(midi_song: MidiFile,
                         mapping: InstrumentParameterMapping,
                         testing_opts: Optional[
                             TestingOptionsForMIDIToSong] = None) -> Song:
    """
    Convert a MIDI file into a MakeCode Arcade song.

    :param midi_song: A `MidiFile` object.
    :param mapping: An `InstrumentParameterMapping` object, loaded from
     `load_instrument_params`.
    :param testing_opts: Extra options used for testing, passed from the CLI.
    :return: MakeCode Arcade `Song` object.
    """
    logger.debug("Converting MIDI file into MakeCode Arcade song")

    if testing_opts is None:
        testing_opts = TestingOptionsForMIDIToSong()

    song = Song(
        measures=1,
        beats_per_measure=4,
        beats_per_minute=120,  # beat every 1/2 seconds
        ticks_per_beat=24,  # each tick is 1/48 seconds long
        tracks=[]
    )

    logger.debug("Resolving timeline")

    global_timeline: List[AbsoluteTimeMessage] = timeline_build(midi_song)
    global_timeline: List[
        AbsoluteTimeMessageWithInstrument] = timeline_find_instrument_data(
        global_timeline)
    global_timeline: List[AbsoluteCompleteNote] = timeline_group_messages(
        global_timeline)

    if testing_opts.replace_all_melodics_with is not None:
        logger.debug(f"Testing option enabled to replace all melodic instruments with "
                     f"MIDI instrument {testing_opts.replace_all_melodics_with}")
        for m in global_timeline:
            if not m.is_drum:
                m.instrument = testing_opts.replace_all_melodics_with
    if testing_opts.replace_all_drums_with is not None:
        logger.debug(f"Testing option enabled to replace all drum notes with MIDI drum "
                     f"note {testing_opts.replace_all_drums_with}")
        for m in global_timeline:
            if m.is_drum:
                m.note = testing_opts.replace_all_drums_with

    # MIDI file with C4 (MIDI 60) plays at B5 (MIDI 83)
    # This is because MakeCode Arcade defines C4 as 49 instead of 60
    # And now I have no idea why I need to shift down another octave but then it works
    # Drums don't need this because we already map from MIDI drum notes to an index into
    # a list of drum instruments in a track, which we control
    for note in global_timeline:
        if not note.is_drum:
            note.note -= 11  # MIDI 60 (C4) maps to Arcade's C4 of 49
            note.note -= 12  # another octave down makes it correct

    global_timeline = timeline_fix_gate_lens(global_timeline, song, mapping)
    global_timeline: List[
        AbsoluteCompleteNoteWithTick] = timeline_quantize_to_song_ticks(global_timeline,
                                                                        song)
    global_timeline: List[
        List[AbsoluteCompleteNoteWithTick]] = timeline_group_by_instrument(
        global_timeline)
    global_timeline = timeline_split_tracks_for_ranges(global_timeline)
    global_timeline: List[
        List[AbsoluteCompleteChordWithTick]] = timeline_group_into_perfect_chords(
        global_timeline)

    # Raises exceptions on check failures
    timeline_checks(song, global_timeline, mapping)

    # With all this pitch checks and timing manipulations done to fit MakeCode Arcade's
    # song's constraints, we should be able to basically map 1-1 to the MakeCode Arcade
    # dataclasses
    logger.debug("Timeline resolved, mapping to MakeCode Arcade song")

    next_id = 0
    highest_tick = 0

    for old_track in global_timeline:
        this_track_is_drum = old_track[0].is_drum
        highest_tick = max([highest_tick] + [c.end_tick for c in old_track])

        # for drums
        midi_drum_to_drum_idx: Dict[int, int] = {}
        if this_track_is_drum:
            # shouldn't matter, copied from get_empty_song to satisfy types and song
            # packing
            instrument = Instrument(
                waveform=11,
                octave=4,
                amp_envelope=Envelope(attack=10, decay=100, sustain=500, release=100,
                                      amplitude=1024)
            )
            # actually load the drums in
            # and keep what midi note to what sample index they should go to
            drums = []
            used_drum_notes = find_all_drum_chords_used(old_track)
            for i, drum_note in enumerate(used_drum_notes):
                drums.append(mapping.drum_instruments[drum_note])
                midi_drum_to_drum_idx[drum_note] = i
        else:
            instrument = deepcopy(mapping.melodic_instruments[old_track[0].instrument])
            # determine the optimal octave offset
            highest_note = max(max(chord.notes) for chord in old_track)
            lowest_note = min(min(chord.notes) for chord in old_track)

            def octave_offset_work(octave: int) -> bool:
                return ((((octave - 2) * 12) <= lowest_note) and
                        (highest_note <= ((octave - 2) * 12 + 63)))

            for potential_offset in range(0, 10):  # find the first offset that works
                if octave_offset_work(potential_offset):
                    instrument.octave = potential_offset
                    break
            else:
                raise ValueError(f"Track range too big to fit! (please report)")
            # none for melodic instrument
            drums = None
        new_track = Track(
            id=next_id,
            instrument=instrument,
            drums=drums,
            notes=[],
        )
        for chord in old_track:
            if this_track_is_drum:
                notes = (midi_drum_to_drum_idx[note] for note in chord.notes)
            else:
                notes = chord.notes
            new_track.notes.append(NoteEvent(
                notes=[Note(note=n, enharmonic_spelling=EnharmonicSpelling.NORMAL) for n
                       in notes],
                start_tick=chord.start_tick,
                end_tick=chord.end_tick,
                velocity=chord.velocity
            ))

        song.tracks.append(new_track)
        next_id += 1

    # fix the ending measure count
    ticks_per_measure = song.beats_per_measure * song.ticks_per_beat
    song.measures = ceil(highest_tick / ticks_per_measure)

    time_for_tick = (60 / song.beats_per_minute) / song.ticks_per_beat
    logger.debug(f"Finished mapping to MakeCode Arcade song with {len(song.tracks)} "
                 f"tracks, length of {highest_tick} ticks which is "
                 f"{highest_tick * time_for_tick} seconds")

    return song
