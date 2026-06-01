import logging
from typing import List

from midi_to_song.models import AbsoluteCompleteChordWithTick
from utils.logger import create_logger

logger = create_logger(name=__name__, level=logging.INFO)


def timeline_checks(timeline: List[List[AbsoluteCompleteChordWithTick]]):
    """
    Run some basic checks on the timeline to verify assumptions before mapping to the
    MakeCode Arcade dataclasses.

    :param timeline: A list of lists of `AbsoluteCompleteChordWithTick` objects.
    :raises ValueError: If any violations are detected.
    """
    logger.debug("Running checks on the timeline")
    # Track count must be less than 256
    if len(timeline) > 255:
        raise ValueError(f"Too many tracks in the timeline! (max of 255, found "
                         f"{len(timeline)}) Reduce polyphony or unique instruments.")
    for track in timeline:
        if len(track) == 0:
            raise Warning("Empty track in the timeline!")
        # Sort by start tick as required, just in case
        track.sort(key=lambda c: c.start_tick)
        # The start and end ticks must be less than 65536
        highest_tick = max(
            [chord.start_tick for chord in track] + [chord.end_tick for chord in track])
        if highest_tick > 65535:
            raise ValueError(f"Chord start or end tick is too high! (max of 65535, "
                             f"found {highest_tick}) Decrease song length or reduce "
                             f"BPM/TPB at the cost of worse timing.")
        # The highest and lowest notes must fit within 64 notes of an integer octave
        # offset for melodic instruments
        if not track[0].is_drum:
            highest_note = max([max(chord.notes) for chord in track])
            lowest_note = min([min(chord.notes) for chord in track])

            def octave_offset_work(octave: int) -> bool:
                return ((((octave - 2) * 12) <= lowest_note) and
                        (highest_note <= ((octave - 2) * 12 + 63)))

            if not any([octave_offset_work(o) for o in range(0, 10)]):
                raise ValueError(f"Track range too big to fit! (please report)")
        # Chord must have less than 256 notes
        max_chord_polyphony = max([len(chord.notes) for chord in track])
        if max_chord_polyphony > 255:
            raise ValueError(f"Chord has too many notes! (max of 255, found "
                             f"{max_chord_polyphony})")
        # Chords must last at least 1 tick
        min_chord_duration = min([chord.end_tick - chord.start_tick for chord in track])
        if min_chord_duration < 1:
            raise ValueError(f"Chord violated minimum duration time! (min of 1 tick, "
                             f"found {min_chord_duration} ticks, please report)")
        # All chords must have the same instrument if melodic or all chords must be
        # marked as drums in a drum track
        if track[0].is_drum:
            if any([not chord.is_drum for chord in track]):
                raise ValueError(f"Found non drum chord in drum track! (please report")
        else:
            instruments = set([chord.instrument for chord in track])
            if len(instruments) > 1:
                raise ValueError(f"Track has more than one instrument! (found "
                                 f"{len(instruments)}, please report)")
        # No chords overlap, i.e. start_tick >= end_tick of prev chord
        for i in range(1, len(track)):
            prev_chord = track[i - 1]
            this_chord = track[i]
            if prev_chord.end_tick > this_chord.start_tick:
                raise ValueError(f"Chord overlapped! (please report)")

    logger.debug("Timeline passes all checks")
