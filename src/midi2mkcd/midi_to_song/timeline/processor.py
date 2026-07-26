import logging
from collections import defaultdict
from collections.abc import Callable
from copy import deepcopy
from math import ceil

from midi2mkcd.arcade.music_types import Song
from midi2mkcd.midi_to_song import InstrumentParameterMapping
from midi2mkcd.midi_to_song.models import (
    AbsoluteCompleteChordWithTick,
    AbsoluteCompleteLyricWithTick,
    AbsoluteCompleteNote,
    AbsoluteCompleteNoteWithTick,
    AbsoluteTimeLyric,
)
from midi2mkcd.utils.logger import create_logger
from midi2mkcd.utils.strings import decode_lyric_text

logger = create_logger(name=__name__, level=logging.INFO)


def timeline_apply_pitch_compensation(
    timeline: list[AbsoluteCompleteNote], k: float
) -> list[AbsoluteCompleteNote]:
    """
    If you tuned all melodic instruments with the same song, then you could be
    ex. tuning a bass with higher-than-typical notes, and a flute with lower-
    than-normal notes. Thus, a flute playing at in its regular range will
    overpower the bass, due to Fletcher-Munson curves. But here, we just use a
    compensation factor, so lower pitches relative to MIDI note 60 are boosted,
    and higher pitches are attenuated.

    With k=0.35, one octave up is at 79% amplitude, two octaves up is at 62%
    amplitude, and one octave down is at 127% amplitude. Currently, this k factor
    is global. Set k=0 or remove it to disable if you tuned instruments with
    their correct ranges.
    :param timeline: A list of `AbsoluteCompleteNote` objects.
    :param k: The ptich compensation factor. Higher k means higher notes are attenuated
     more and lower notes are boosted more. Set 0 to disable.
    :return: A list of `AbsoluteCompleteNote` objects.
    """
    logger.debug(f"Applying pitch compensation factor of {k=} to timeline")
    res = []
    ref_note = 60 - 11 - 12  # notes were adjusted
    scaler: Callable[[int], float] = lambda n: 2 ** (-(n - ref_note) / 12 * k)
    logger.debug(f"Reference note is MIDI note {ref_note}")
    logger.debug(
        f"Scaler value examples for octave -2, -1, 0, 1, and 2: "
        f"{[round(scaler(ref_note + 12 * o) * 100) / 100 for o in range(-2, 3)]}"
    )
    notes_boosted = 0
    notes_attenuated = 0
    for old_note in timeline:
        if not old_note.is_drum:
            new_note = deepcopy(old_note)
            scale = scaler(new_note.note)
            new_note.velocity = round(min(max(new_note.velocity * scale, 1), 127))
            if scale > 1:
                notes_boosted += 1
            elif scale < 1:
                notes_attenuated += 1
        else:
            new_note = old_note
        res.append(new_note)
    logger.debug(
        f"Attenuated {notes_attenuated} notes and boosted {notes_boosted} notes"
    )
    return res


def timeline_fix_gate_lens(
    timeline: list[AbsoluteCompleteNote],
    song: Song,
    mapping: InstrumentParameterMapping,
) -> list[AbsoluteCompleteNote]:
    """
    Increase all the durations of the notes to ensure that attack < gate len + release,
    so a playback bug is avoided. See https://github.com/microsoft/pxt/pull/11352.

    :param timeline: A list of `AbsoluteCompleteNote` objects.
    :param song: The `Song` object to reference the TPM and BPM.
    :param mapping: An `InstrumentParameterMapping` object, loaded from
     `load_instrument_params`.
    :return: A list of `AbsoluteCompleteNote` objects.
    """
    logger.debug("Fixing gate lengths of notes in the timeline to avoid playback bug")

    seconds_per_tick = (60 / song.beats_per_measure) / song.ticks_per_beat

    res = []

    durations_extended = 0

    for old_note in timeline:
        if old_note.is_drum:
            # drum instruments don't have gate lengths, so we can skip
            res.append(old_note)
            continue
        instrument_params = mapping.melodic_instruments[old_note.instrument]
        attack = instrument_params.amp_envelope.attack / 1000
        release = instrument_params.amp_envelope.release / 1000
        old_duration = old_note.end_time - old_note.start_time
        min_duration = attack - release
        if old_duration <= min_duration:
            min_ticks = min_duration / seconds_per_tick
            required_ticks = ceil(min_ticks)
            if min_ticks == required_ticks:
                required_ticks += 1
            new_note = deepcopy(old_note)
            new_duration = required_ticks * seconds_per_tick
            new_note.end_time = new_note.start_time + new_duration
            durations_extended += 1
        else:
            new_note = old_note
        res.append(new_note)

    logger.debug(
        f"Extended {durations_extended} note durations to ensure duration > "
        f"attack - release"
    )

    return res


def timeline_quantize_to_song_ticks(
    timeline: list[AbsoluteCompleteNote], song: Song
) -> list[AbsoluteCompleteNoteWithTick]:
    """
    Given the song's BPM and TPB, quantize the timeline's start and end times to ticks.

    :param timeline: A list of `AbsoluteCompleteNote` objects.
    :param song: The `Song` object to use.
    :return: A list of `AbsoluteCompleteNoteWithTick` objects.
    """
    tick_time = (60 / song.beats_per_minute) / song.ticks_per_beat  # in secs
    logger.debug(
        f"Quantizing note times to ticks based of song BPM of "
        f"{song.beats_per_minute} and TPB of {song.ticks_per_beat} - one tick "
        f"is 1/{1 / tick_time} ({tick_time}) seconds long"
    )

    res = []

    for old_note in timeline:
        new_start_tick = round(old_note.start_time / tick_time)
        # Ensure all notes last for one tick
        new_end_tick = max(round(old_note.end_time / tick_time), new_start_tick + 1)
        res.append(
            AbsoluteCompleteNoteWithTick(
                start_tick=new_start_tick,
                end_tick=new_end_tick,
                note=old_note.note,
                velocity=old_note.velocity,
                instrument=old_note.instrument,
                is_drum=old_note.is_drum,
            )
        )

    return res


def timeline_lyrics_quantize_to_song_ticks(
    timeline: list[AbsoluteTimeLyric], song: Song
) -> list[AbsoluteCompleteLyricWithTick]:
    """
    Given the song's BPM and TPB, quantize the timeline lyric's start times to ticks.

    :param timeline: A list of `AbsoluteTimeLyric` objects.
    :param song: The `Song` object to use.
    :return: A list of `AbsoluteCompleteLyricWithTick` objects.
    """
    tick_time = (60 / song.beats_per_minute) / song.ticks_per_beat  # in secs
    logger.debug(
        f"Quantizing lyric times to ticks based of song BPM of "
        f"{song.beats_per_minute} and TPB of {song.ticks_per_beat} - one tick "
        f"is 1/{1 / tick_time} ({tick_time}) seconds long"
    )

    res = []

    for old_lyric in timeline:
        new_start_tick = round(old_lyric.time / tick_time)
        res.append(
            AbsoluteCompleteLyricWithTick(
                tick=new_start_tick, lyric=decode_lyric_text(old_lyric.msg.text)
            )
        )

    return res


def find_all_melodic_instruments(
    timeline: list[AbsoluteCompleteNoteWithTick],
) -> list[int]:
    """
    Search the timeline for all unique melodic instruments.

    :param timeline: A list of `AbsoluteTimeMessage` objects.
    :return: A list of ints, representing what general MIDI instruments are in the song.
    """
    logger.debug("Finding all melodic instruments in the timeline")

    return sorted({m.instrument for m in timeline if not m.is_drum})


def find_all_drum_notes_used(timeline: list[AbsoluteCompleteNoteWithTick]) -> list[int]:
    """
    Search the timeline for all unique drum notes.

    :param timeline: A list of `AbsoluteTimeMessage` objects.
    :return: A list of ints, representing what general MIDI drum notes are in the song.
    """
    logger.debug("Finding all drum notes in the timeline")

    return sorted({m.note for m in timeline if m.is_drum})


def find_all_drum_chords_used(
    timeline: list[AbsoluteCompleteChordWithTick],
) -> list[int]:
    """
    Search the timeline for all unique drum notes, for a list of chords.

    :param timeline: A list of `AbsoluteCompleteChordWithTick` objects.
    :return: A list of ints, representing what general MIDI drum notes are in the song.
    """
    logger.debug("Finding all drum chords in the timeline")

    return sorted({note for chord in timeline if chord.is_drum for note in chord.notes})


def timeline_group_by_instrument(
    timeline: list[AbsoluteCompleteNoteWithTick],
) -> list[list[AbsoluteCompleteNoteWithTick]]:
    """
    Split up the timeline by instrument.

    :param timeline: A list of `AbsoluteCompleteNote` objects.
    :return: A list of lists of `AbsoluteCompleteNoteWithTick` objects. (Each list of
     notes within the list have the same instrument)
    """
    logger.debug("Splitting up the timeline by instruments")

    used_melodics = find_all_melodic_instruments(timeline)
    used_drums = find_all_drum_notes_used(timeline)
    logger.debug(
        f"Song used {len(used_melodics)} melodic instruments and "
        f"{len(used_drums)} unique drum notes"
    )
    logger.debug(f"Melodics used: {used_melodics}")
    logger.debug(f"Drums used: {used_drums}")

    tracks = []

    for melodic in used_melodics:
        tracks.append(
            [
                note
                for note in timeline
                if note.instrument == melodic and not note.is_drum
            ]
        )

    if len(used_drums) > 0:
        tracks.append([note for note in timeline if note.is_drum])

    logger.debug(f"Split up global timeline into {len(tracks)} tracks")

    return tracks


def timeline_split_tracks_for_ranges(
    timeline: list[list[AbsoluteCompleteNoteWithTick]],
) -> list[list[AbsoluteCompleteNoteWithTick]]:
    """
    Go through the melodic tracks in the timeline and check the highest and lowest note
    in each track. If it can't fit into one track (which has a range limit of 64 notes
    from an octave offset) then we must split into more tracks to handle all the
    offsets.

    :param timeline: A list of lists of `AbsoluteCompleteNote` objects.
    :return: A list of lists of `AbsoluteCompleteNoteWithTick` objects.
    """
    logger.debug("Checking necessity to split track into two tracks for range")

    new_tracks: list[list[AbsoluteCompleteNoteWithTick]] = []

    tracks_that_fit = 0
    tracks_that_split = 0

    for old_track in timeline:
        # drum tracks don't use octave offsets, only 61 samples max as well
        if old_track[0].is_drum:
            new_tracks.append(old_track)
            tracks_that_fit += 1
            continue

        highest_note = max(n.note for n in old_track)
        lowest_note = min(n.note for n in old_track)

        def octave_offset_work(
            octave: int, lowest: int = lowest_note, highest: int = highest_note
        ) -> bool:
            return (((octave - 2) * 12) <= lowest) and (
                highest <= ((octave - 2) * 12 + 63)
            )

        # does ANY octave offset from [0, 9] work?
        if any(octave_offset_work(o) for o in range(10)):
            # we don't need to modify, when constructing the MakeCode Arcade Tracks,
            # we'll find the correct octave offset again
            new_tracks.append(old_track)
            tracks_that_fit += 1
        else:
            # split into three tracks to guarantee covering [0, 127]
            # TODO: figure out if we really need 3 tracks all the time (probably not?)
            #  maybe we can test a version where we start with MIDI notes [12, 75] and
            #  [72, 135] or similar
            low_track = [n for n in old_track if 0 <= n.note < 64]
            med_track = [n for n in old_track if 60 <= n.note < 124]
            high_track = [n for n in old_track if 124 <= n.note < 128]
            if len(low_track) > 0:
                new_tracks.append(low_track)
            if len(med_track) > 0:
                new_tracks.append(med_track)
            if len(high_track) > 0:
                new_tracks.append(high_track)
            tracks_that_split += 1

    logger.debug(
        f"{tracks_that_fit} tracks fit within one track's range, "
        f"{tracks_that_split} tracks had to split, total of {len(new_tracks)} "
        f"tracks in timeline"
    )

    return new_tracks


def timeline_group_into_perfect_chords(
    timeline: list[list[AbsoluteCompleteNoteWithTick]],
) -> list[list[AbsoluteCompleteChordWithTick]]:
    """
    Go through the tracks in the timeline and group up notes that share the same start
    and end tick and velocity and instruments to create "perfect" chords.

    :param timeline: A list of lists of `AbsoluteCompleteNoteWithTick` objects.
    :return: A list of lists of `AbsoluteCompleteChordWithTick` objects.
    """
    logger.debug("Grouping notes in timeline into perfect chords")

    new_tracks: list[list[AbsoluteCompleteChordWithTick]] = []

    old_note_count = sum(len(track) for track in timeline)

    for old_track in timeline:
        # use dictionaries to quickly find existing chords
        # key is a tuple of (start_tick, end_tick, velocity, instrument, is_drum)
        # values are notes in the chord
        chords: dict[tuple[int, int, int, int, bool], list[int]] = defaultdict(list)

        for note in old_track:
            key = (
                note.start_tick,
                note.end_tick,
                note.velocity,
                note.instrument,
                note.is_drum,
            )
            # if a note has the same start and end tick and velocity and instrument
            # they can be played as a chord
            chords[key].append(note.note)

        new_track: list[AbsoluteCompleteChordWithTick] = []
        for (
            start_tick,
            end_tick,
            velocity,
            instrument,
            is_drum,
        ), notes in chords.items():
            new_track.append(
                AbsoluteCompleteChordWithTick(
                    start_tick=start_tick,
                    end_tick=end_tick,
                    notes=notes,
                    velocity=velocity,
                    instrument=instrument,
                    is_drum=is_drum,
                )
            )
        new_track.sort(key=lambda c: (c.start_tick, c.end_tick))
        new_tracks.append(new_track)

    new_chord_count = sum(len(track) for track in new_tracks)

    logger.debug(
        f"Created "
        f"{sum([sum([1 if len(n.notes) > 1 else 0 for n in t]) for t in new_tracks])}"
        f" multi-note chords (dropped from {old_note_count} notes to "
        f"{new_chord_count} chords)"
    )

    return new_tracks


def timeline_resolve_overlapping_chords(
    timeline: list[list[AbsoluteCompleteChordWithTick]],
) -> list[list[AbsoluteCompleteChordWithTick]]:
    """
    Go through the melodic tracks in the timeline and check for chords overlapping in a
    track. If they are, move them to an extra track. If there are no free tracks, create
    one. This minimizes the number of extra tracks required while keeping the desired
    polyphony of MIDI.

    :param timeline: A list of lists of `AbsoluteCompleteChordWithTick` objects.
    :return: A list of lists of `AbsoluteCompleteChordWithTick` objects.
    """
    logger.debug("Resolving overlapping chords")

    new_tracks: list[list[AbsoluteCompleteChordWithTick]] = []
    old_track_count = len(timeline)

    for old_track in timeline:
        new_sub_tracks: list[list[AbsoluteCompleteChordWithTick]] = [[]]

        # don't have to worry about instrument matching because chords in old_track
        # should all have the same instrument
        for chord in old_track:
            # try to place into a sub track
            for sub_track in new_sub_tracks:
                # if the last note in the subtrack has ended (or it's empty)
                # TODO: Test if this needs to be < or <= works
                #  theoretically it should work fine with <= (and this will save tracks)
                #  but < will guarantee a "rest"
                if len(sub_track) == 0 or sub_track[-1].end_tick < chord.start_tick:
                    sub_track.append(chord)
                    break
            else:
                # no free sub tracks, create
                new_sub_tracks.append([chord])

        # dump all generated sub tracks directly into the new timeline
        new_tracks.extend(new_sub_tracks)

    new_track_count = len(new_tracks)
    logger.debug(
        f"Created {new_track_count - old_track_count} extra tracks to handle "
        f"overlapping chords (from {old_track_count} to {new_track_count} "
        f"tracks)"
    )

    return new_tracks
