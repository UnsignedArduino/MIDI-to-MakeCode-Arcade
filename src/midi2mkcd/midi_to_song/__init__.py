import logging
from copy import deepcopy
from dataclasses import dataclass
from math import ceil

from mido import MidiFile

from midi2mkcd.arcade.music_types import (
    EnharmonicSpelling,
    Envelope,
    Instrument,
    Note,
    NoteEvent,
    Song,
    Track,
)
from midi2mkcd.midi_to_song.instruments import (
    InstrumentParameterMapping as InstrumentParameterMapping,
)
from midi2mkcd.midi_to_song.models import (
    AbsoluteCompleteChordWithTick,
    AbsoluteCompleteNote,
    AbsoluteCompleteNoteWithTick,
    AbsoluteTimeMessage,
    AbsoluteTimeMessageWithInstrument,
    TestingOptionsForMIDIToSong,
)
from midi2mkcd.midi_to_song.models import (
    AbsoluteCompleteLyricWithTick as AbsoluteCompleteLyricWithTick,
)
from midi2mkcd.midi_to_song.models import AbsoluteTimeLyric as AbsoluteTimeLyric
from midi2mkcd.midi_to_song.timeline.parser import (
    timeline_build,
    timeline_find_instrument_data,
    timeline_find_lyrics,
    timeline_group_messages,
)
from midi2mkcd.midi_to_song.timeline.processor import (
    find_all_drum_chords_used,
    timeline_apply_pitch_compensation,
    timeline_fix_gate_lens,
    timeline_group_by_instrument,
    timeline_group_into_perfect_chords,
    timeline_lyrics_quantize_to_song_ticks,
    timeline_quantize_to_song_ticks,
    timeline_split_tracks_for_note_byte_lengths,
    timeline_split_tracks_for_ranges,
)
from midi2mkcd.midi_to_song.timeline.validation import (
    timeline_checks,
    timeline_lyrics_checks,
)
from midi2mkcd.utils.logger import create_logger

logger = create_logger(name=__name__, level=logging.INFO)


@dataclass
class ConvertMIDIToSongResult:
    song: Song
    # a list of ints, where the index maps to the correct MIDI note. (so in a drum
    # track, drum index 0 maps to the MIDI drum note at index 0 in the list, etc.)
    drum_idx_to_midi_drum: list[int]
    # a list of ints, where the index maps to the correct MIDI instrument, where -1 is
    # the standard drum kit
    track_idx_to_midi_instrument: list[int]
    # a list of ints, which is the starting tick of the lyric
    lyric_ticks: list[int]
    # a list of strs, which is the lyrics themselves
    lyric_texts: list[str]


def convert_midi_to_song(
    midi_song: MidiFile,
    mapping: InstrumentParameterMapping,
    testing_opts: TestingOptionsForMIDIToSong | None = None,
) -> ConvertMIDIToSongResult:
    """
    Convert a MIDI file into a MakeCode Arcade song.

    :param midi_song: A `MidiFile` object.
    :param mapping: An `InstrumentParameterMapping` object, loaded from
     `load_instrument_params`.
    :param testing_opts: Extra options used for testing, passed from the CLI.
    :return: A convertMIDIToSongResult data class.
    """
    logger.debug("Converting MIDI file into MakeCode Arcade song")

    if testing_opts is None:
        testing_opts = TestingOptionsForMIDIToSong()

    song = Song(
        measures=1,
        beats_per_measure=4,
        beats_per_minute=120,  # beat every 1/2 seconds
        ticks_per_beat=24,  # each tick is 1/48 seconds long
        tracks=[],
    )

    logger.debug("Resolving timeline")

    global_timeline: list[AbsoluteTimeMessage] = timeline_build(midi_song)
    global_timeline_0: list[AbsoluteTimeMessageWithInstrument] = (
        timeline_find_instrument_data(global_timeline)
    )
    global_timeline_1: list[AbsoluteCompleteNote] = timeline_group_messages(
        global_timeline_0
    )

    if testing_opts.replace_all_melodics_with is not None:
        logger.debug(
            f"Testing option enabled to replace all melodic instruments with "
            f"MIDI instrument {testing_opts.replace_all_melodics_with}"
        )
        for m in global_timeline_1:
            if not m.is_drum:
                m.instrument = testing_opts.replace_all_melodics_with
    if testing_opts.replace_all_drums_with is not None:
        logger.debug(
            f"Testing option enabled to replace all drum notes with MIDI drum "
            f"note {testing_opts.replace_all_drums_with}"
        )
        for m in global_timeline_1:
            if m.is_drum:
                m.note = testing_opts.replace_all_drums_with

    # MIDI file with C4 (MIDI 60) plays at B5 (MIDI 83)
    # This is because MakeCode Arcade defines C4 as 49 instead of 60
    # And now I have no idea why I need to shift down another octave but then it works
    # Drums don't need this because we already map from MIDI drum notes to an index into
    # a list of drum instruments in a track, which we control
    for note in global_timeline_1:
        if not note.is_drum:
            note.note -= 11  # MIDI 60 (C4) maps to Arcade's C4 of 49
            note.note -= 12  # another octave down makes it correct

    global_timeline_2 = timeline_apply_pitch_compensation(
        global_timeline_1, mapping.melodic_pitch_comp_k
    )
    global_timeline_3 = timeline_fix_gate_lens(global_timeline_2, song, mapping)
    global_timeline_4: list[AbsoluteCompleteNoteWithTick] = (
        timeline_quantize_to_song_ticks(global_timeline_3, song)
    )
    global_timeline_5: list[list[AbsoluteCompleteNoteWithTick]] = (
        timeline_group_by_instrument(global_timeline_4)
    )
    global_timeline_6 = timeline_split_tracks_for_ranges(global_timeline_5)
    global_timeline_7: list[list[AbsoluteCompleteChordWithTick]] = (
        timeline_group_into_perfect_chords(global_timeline_6)
    )
    global_timeline_8: list[list[AbsoluteCompleteChordWithTick]] = (
        timeline_split_tracks_for_note_byte_lengths(global_timeline_7)
    )

    # Raises exceptions on check failures
    timeline_checks(song, global_timeline_8, mapping)

    # Now let's do lyrics
    global_timeline_lyrics_0: list[AbsoluteTimeLyric] = timeline_find_lyrics(
        global_timeline
    )
    global_timeline_lyrics_1: list[AbsoluteCompleteLyricWithTick] = (
        timeline_lyrics_quantize_to_song_ticks(global_timeline_lyrics_0, song)
    )

    # Raises exceptions on check failures
    # Currently none but we'll add this right now
    timeline_lyrics_checks(song, global_timeline_7, global_timeline_lyrics_1)

    # With all this pitch checks and timing manipulations done to fit MakeCode Arcade's
    # song's constraints, we should be able to basically map 1-1 to the MakeCode Arcade
    # dataclasses
    logger.debug("Timeline resolved, mapping to MakeCode Arcade song")

    next_id = 0
    highest_tick = 0

    midi_drum_to_drum_idx: dict[int, int] = {}
    track_idx_to_midi_instrument = []
    for old_track in global_timeline_8:
        this_track_is_drum = old_track[0].is_drum
        highest_tick = max([highest_tick] + [c.end_tick for c in old_track])

        if this_track_is_drum:
            # shouldn't matter, copied from get_empty_song to satisfy types and song
            # packing
            instrument = Instrument(
                waveform=11,
                octave=4,
                amp_envelope=Envelope(
                    attack=10, decay=100, sustain=500, release=100, amplitude=1024
                ),
            )
            # actually load the drums in
            # and keep what midi note to what sample index they should go to
            drums = []
            used_drum_notes = find_all_drum_chords_used(old_track)
            for i, drum_note in enumerate(used_drum_notes):
                drums.append(mapping.drum_instruments[drum_note])
                midi_drum_to_drum_idx[drum_note] = i
            # Record the MIDI instrument
            # We have the standard kit as -1
            track_idx_to_midi_instrument.append(-1)
        else:
            instrument = deepcopy(mapping.melodic_instruments[old_track[0].instrument])
            # determine the optimal octave offset
            highest_note = max(max(chord.notes) for chord in old_track)
            lowest_note = min(min(chord.notes) for chord in old_track)

            def octave_offset_work(
                octave: int, lowest: int = lowest_note, highest: int = highest_note
            ) -> bool:
                return (((octave - 2) * 12) <= lowest) and (
                    highest <= ((octave - 2) * 12 + 63)
                )

            for potential_offset in range(10):  # find the first offset that works
                if octave_offset_work(potential_offset):
                    instrument.octave = potential_offset
                    break
            else:
                raise ValueError("Track range too big to fit! (please report)")
            # none for melodic instrument
            drums = None
            # Record the MIDI instrument
            track_idx_to_midi_instrument.append(old_track[0].instrument)
        new_track = Track(
            id=next_id,
            instrument=instrument,
            drums=drums,
            notes=[],
        )
        for chord in old_track:
            if this_track_is_drum:
                notes = [midi_drum_to_drum_idx[note] for note in chord.notes]
            else:
                notes = chord.notes
            new_track.notes.append(
                NoteEvent(
                    notes=[
                        Note(note=n, enharmonic_spelling=EnharmonicSpelling.NORMAL)
                        for n in notes
                    ],
                    start_tick=chord.start_tick,
                    end_tick=chord.end_tick,
                    velocity=chord.velocity,
                )
            )

        song.tracks.append(new_track)
        next_id += 1  # noqa: SIM113

    # fix the ending measure count
    ticks_per_measure = song.beats_per_measure * song.ticks_per_beat
    song.measures = ceil(highest_tick / ticks_per_measure)

    # create flat parallel arrays for lyrics
    lyric_ticks = []
    lyric_texts = []

    for lyric in global_timeline_lyrics_1:
        lyric_ticks.append(lyric.tick)
        lyric_texts.append(lyric.lyric)

    time_for_tick = (60 / song.beats_per_minute) / song.ticks_per_beat
    logger.debug(
        f"Finished mapping to MakeCode Arcade song with {len(song.tracks)} "
        f"tracks, length of {highest_tick} ticks which is "
        f"{highest_tick * time_for_tick} seconds"
    )

    if len(midi_drum_to_drum_idx) > 0:
        drum_idx_to_midi_drum = list(midi_drum_to_drum_idx.keys())
    else:
        drum_idx_to_midi_drum = []
    logger.debug(f"Drum indices to MIDI drum notes: {drum_idx_to_midi_drum}")

    logger.debug(f"Track indices to MIDI instruments: {track_idx_to_midi_instrument}")

    return ConvertMIDIToSongResult(
        song=song,
        drum_idx_to_midi_drum=drum_idx_to_midi_drum,
        track_idx_to_midi_instrument=track_idx_to_midi_instrument,
        lyric_ticks=lyric_ticks,
        lyric_texts=lyric_texts,
    )
