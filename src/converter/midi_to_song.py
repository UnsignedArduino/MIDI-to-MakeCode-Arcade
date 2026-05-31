import logging
from collections import defaultdict
from dataclasses import dataclass
from enum import IntEnum
from typing import Dict, List, Tuple

from mido import Message, MidiFile, tick2second

from arcade.music_types import Song
from converter.instruments import InstrumentParameterMapping
from utils.logger import create_logger

logger = create_logger(name=__name__, level=logging.INFO)


@dataclass
class AbsoluteTickMessage:
    tick: int  # absolute MIDI tick
    track: int
    msg: Message
    msg_idx: int


@dataclass
class AbsoluteTimeMessage:
    time: float  # absolute time in seconds
    port: int
    msg: Message  # note_on, note_off, program_change, control_change (where control = 0)


def timeline_build(midi_song: MidiFile) -> List[AbsoluteTimeMessage]:
    """
    Look through all tracks and convert MIDI's delta tick time to absolute time in
    seconds while keeping track of tempo and port changes.

    :param midi_song: `MidiFile` object.
    :return: A list of `AbsoluteTimeMessage` objects, which hold time, their original
     track, and the MIDI message itself. It is sorted by time in ascending order and
     filters out unneeded messages.
    """
    # Gather tracks and convert to absolute time
    # Unfortunately we can't simply just merge all the tracks because if channel 0 on
    # track 0 was a piano and channel 0 on track 1 was a flute, then they collide
    # We must keep them separate and build our own global absolute timeline afterward
    # Additionally, we must keep track of tempo changes and port changes

    logger.debug("Building global absolute timeline of MIDI messages")

    # First gather all messages with their relative ticks, and convert to absolute ticks
    # We need to do this in passes because some messages are globally effective while
    # some others are restricted to a track only
    logger.debug("Convert relative ticks to absolute ticks and sort")
    all_msgs_with_abs_ticks: List[AbsoluteTickMessage] = []
    for i, track in enumerate(midi_song.tracks):
        abs_tick = 0
        for j, msg in enumerate(track):
            abs_tick += msg.time
            all_msgs_with_abs_ticks.append(
                AbsoluteTickMessage(tick=abs_tick, track=i, msg=msg, msg_idx=j))
    # Update the sort, time first, then track, then order within the track
    all_msgs_with_abs_ticks.sort(key=lambda m: (m.tick, m.track, m.msg_idx))

    # Now we can convert absolute ticks to absolute time, but we need to keep track of
    # tempo changes and MIDI port changes as well (they are also chronological)
    logger.debug("Convert absolute ticks to absolute time and track tempo and port "
                 "changes")
    global_timeline: List[AbsoluteTimeMessage] = []
    ticks_per_beat = midi_song.ticks_per_beat
    msgs_skipped = 0

    current_tempo = 500000  # the default
    current_abs_time = 0.0  # in seconds (*_time is seconds)
    last_abs_ticks = 0  # in MIDI ticks (*_ticks is MIDI ticks)

    track_ports = {i: 0 for i in range(len(midi_song.tracks))}
    # Prescan each track for the first meta midi_port message
    # Although technically we shouldn't need to do this, some notation software (notably
    # MuseScore in my testing) seem to skip midi_port for the first batch of CCs and PC
    # in every track, and so CCs and PCs go to port 0 while the note data goes to
    # another port
    # This sets up a default port that usually works
    for i, track in enumerate(midi_song.tracks):
        for msg in track:
            if msg.type == "midi_port":
                track_ports[i] = msg.port
                break

    for item in all_msgs_with_abs_ticks:
        msg = item.msg
        track_idx = item.track
        abs_ticks = item.tick

        delta_ticks = abs_ticks - last_abs_ticks
        if delta_ticks > 0:
            delta_time = tick2second(delta_ticks, ticks_per_beat, current_tempo)
            current_abs_time += delta_time
        last_abs_ticks = abs_ticks

        if msg.type == "set_tempo":
            current_tempo = msg.tempo
        elif msg.type == "midi_port":
            track_ports[track_idx] = msg.port
        # Keep note on/off, program change, sysex, and control change (only if control
        # is 0 or 32 which is the bank select MSB/LSB)
        elif msg.type in ("note_on", "note_off", "program_change", "sysex") or (
                msg.type == "control_change" and msg.control in (0, 32)):
            global_timeline.append(
                AbsoluteTimeMessage(time=current_abs_time, port=track_ports[track_idx],
                                    msg=msg))
        else:
            msgs_skipped += 1
    logger.debug(f"Global timeline has {len(global_timeline)} messages (skipped "
                 f"{msgs_skipped}), total song length of {global_timeline[-1].time}s")

    return global_timeline


class DrumDeterminationSource(IntEnum):
    DEFAULT = 0
    CC = 1  # control change
    SYSEX = 2


@dataclass
class ChannelState:
    program: int  # instrument
    bank_select_msb: int
    bank_select_lsb: int
    is_drum: bool
    drum_determined_by: DrumDeterminationSource


@dataclass
class AbsoluteTimeMessageWithInstrument:
    time: float
    port: int
    instrument: int  # midi instrument
    is_drum: bool
    msg: Message  # note_on and note_off


def timeline_find_instrument_data(timeline: List[AbsoluteTimeMessage]) -> List[
    AbsoluteTimeMessageWithInstrument]:
    """
    Parse the timeline for program_change and control_change (control = 0) messages to
    determine what instrument each message has.

    :param timeline: A list of `AbsoluteTimeMessage` objects.
    :return: A list of `AbsoluteTimeMessageWithInstrument` objects.
    """
    # Read the timeline sequentially and keep track of program_change and
    # control_change (control = 0) to update the current instrument for each channel on
    # a port
    logger.debug("Finding instrument data for each message in the timeline")

    # Initialize channel states, which keep track of the bank_select and program per
    # channel and port
    channel_states = {}
    highest_port = max([m.port for m in timeline], default=0)
    for port in range(highest_port + 1):
        for channel in range(16):
            # By default, channel 10 starts as drum
            channel_states[(port, channel)] = ChannelState(program=0,
                                                           bank_select_msb=0,
                                                           bank_select_lsb=0,
                                                           is_drum=channel == 9,
                                                           drum_determined_by=DrumDeterminationSource.DEFAULT)

    # Now search through the timeline and apply control_change (control = 0) and
    # program_change messages to the channel states
    timeline_with_instrument: List[AbsoluteTimeMessageWithInstrument] = []

    instr_msgs_processed = 0
    sysex_msgs_processed = 0

    for item in timeline:
        msg = item.msg
        port = item.port
        channel = getattr(msg, "channel", -1)

        if msg.type == "control_change":
            if msg.control == 0:
                channel_states[(port, channel)].bank_select_msb = msg.value
            elif msg.control == 32:
                channel_states[(port, channel)].bank_select_lsb = msg.value
            is_drum_bank = (
                    channel_states[(port, channel)].bank_select_msb in (120, 121,
                                                                        126,
                                                                        127) or
                    channel == 9
            )
            # Only override if last drum determination was weaker than CC
            if DrumDeterminationSource.CC >= channel_states[
                (port, channel)].drum_determined_by:
                channel_states[(port, channel)].is_drum = is_drum_bank
                channel_states[
                    (port, channel)].drum_determined_by = DrumDeterminationSource.CC
            instr_msgs_processed += 1
        elif msg.type == "program_change":
            channel_states[(port, channel)].program = msg.program
            instr_msgs_processed += 1
        elif msg.type == "sysex":
            data = msg.data
            # check for Roland GS
            if (len(data) >= 8 and
                    data[0] == 0x41 and  # Roland ID
                    data[1] == 0x10 and  # device ID
                    data[2] == 0x42 and  # GS standard layouts
                    data[3] == 0x12 and
                    data[4] == 0x40 and  # parameter 1
                    0x10 <= data[5] <= 0x1F and  # 0x1n, channel byte, see below
                    data[6] == 0x15 and  # part address
                    data[7] in (0, 1, 2)):  # map byte
                # 0x1n is the channel byte, where:
                #   n=0 is channel 9 (midi channel 10)
                #   n=1 through 9 is channels 0 through 8
                #   n=A through F is channels 10 through 15
                def gs_byte_to_channel(val: int) -> int:
                    n = val & 0x0F
                    if n == 0:
                        return 9
                    if n < 10:
                        return n - 1
                    return n

                channel = gs_byte_to_channel(data[5])
                is_drum = data[7] in (1, 2)
                # Only override if last drum determination was weaker than sysex
                if DrumDeterminationSource.SYSEX >= channel_states[
                    (port, channel)].drum_determined_by:
                    channel_states[(port, channel)].is_drum = is_drum
                    channel_states[
                        (port,
                         channel)].drum_determined_by = DrumDeterminationSource.SYSEX
                # print(f"Roland GS channel {channel} drum: {is_drum}")
                instr_msgs_processed += 1
                sysex_msgs_processed += 1
            # check for Yamaha XG
            elif (len(data) >= 7 and
                  data[0] == 0x43 and  # Yamaha ID
                  data[1] == 0x10 and  # device ID
                  data[2] == 0x4C and  # XG model ID
                  data[3] == 0x08 and  # multi part params
                  0x00 <= data[4] <= 0x0F and  # channel, direct mapping
                  data[5] == 0x07 and  # part address
                  data[6] >= 0):  # map byte, technically redundant but
                channel = data[4]
                is_drum = data[6] > 0
                # Only override if last drum determination was weaker than sysex
                if DrumDeterminationSource.SYSEX >= channel_states[
                    (port, channel)].drum_determined_by:
                    channel_states[(port, channel)].is_drum = is_drum
                    channel_states[
                        (port,
                         channel)].drum_determined_by = DrumDeterminationSource.SYSEX
                # print(f"Yamaha XG channel {channel} drum: {is_drum}")
                instr_msgs_processed += 1
                sysex_msgs_processed += 1
        elif msg.type in ("note_on", "note_off"):
            timeline_with_instrument.append(AbsoluteTimeMessageWithInstrument(
                time=item.time,
                port=item.port,
                instrument=channel_states[(port, channel)].program,
                is_drum=channel_states[(port, channel)].is_drum,
                msg=msg
            ))
    logger.debug(f"Global timeline has {len(timeline_with_instrument)} note messages ("
                 f"processed {instr_msgs_processed} instrument messages, "
                 f"{sysex_msgs_processed} of which were recognized SysEx messages)")

    return timeline_with_instrument


@dataclass
class AbsoluteCompleteNote:
    start_time: float
    end_time: float

    note: int
    velocity: int

    instrument: int
    is_drum: bool


def timeline_group_messages(timeline: List[AbsoluteTimeMessageWithInstrument]) -> List[
    AbsoluteCompleteNote]:
    """
    Parse the timeline for note_on and note_off messages to determine the start and end
    times of each note.

    :param timeline: A list of `AbsoluteTimeMessageWithInstrument` objects.
    :return: A list of `AbsoluteCompleteNote` objects.
    """
    # Find all note_on and note_on (velocity = 0) and note_off messages, and pair them
    # up
    logger.debug("Grouping messages into complete notes in the timeline")

    timeline_with_complete_notes = []
    highest_port = max([0] + [m.port for m in timeline])
    active_notes: Dict[Tuple[int, int], List[AbsoluteCompleteNote]] = {
        (port, channel): []
        for port in range(highest_port + 1)
        for channel in range(16)
    }
    last_time = 0

    current_poly = 0
    max_poly = 0

    for item in timeline:
        msg = item.msg
        port_and_channel = (item.port, msg.channel)
        last_time = max(last_time, item.time)

        if msg.type == "note_on" and msg.velocity > 0:
            # add to the list of playing notes
            active_notes[port_and_channel].append(
                AbsoluteCompleteNote(
                    start_time=item.time,
                    end_time=item.time,  # will be updated when note_off found
                    note=msg.note,
                    velocity=msg.velocity,
                    instrument=item.instrument,
                    is_drum=item.is_drum,
                )
            )
            current_poly += 1
        elif msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0):
            # find the playing note and finish it
            for playing_note in active_notes[port_and_channel]:
                if playing_note.note == msg.note:
                    playing_note.end_time = item.time
                    timeline_with_complete_notes.append(playing_note)
                    active_notes[port_and_channel].remove(playing_note)
                    current_poly -= 1
                    break
            else:
                logger.warning(f"Could not find start of note for {item}")
        max_poly = max(max_poly, current_poly)

    # handle hanging notes
    hanging_count = 0
    for group_key in active_notes:
        for playing_note in active_notes[group_key]:
            playing_note.end_time = last_time
            timeline_with_complete_notes.append(playing_note)
            hanging_count += 1
            # no need to remove we're cleaning up

    # sort by start instead of when they ended
    timeline_with_complete_notes.sort(key=lambda m: m.start_time)

    logger.debug(f"Global timeline has {len(timeline_with_complete_notes)} note events "
                 f"(maximum polyphony across all channels and ports was {max_poly} "
                 f"notes and had to clean up {hanging_count} hanging notes)")

    return timeline_with_complete_notes


@dataclass
class AbsoluteCompleteNoteWithTick:
    start_tick: int
    end_tick: int

    note: int
    velocity: int

    instrument: int
    is_drum: bool


def timeline_quantize_to_song_ticks(timeline: List[AbsoluteCompleteNote],
                                    song: Song) -> List[AbsoluteCompleteNoteWithTick]:
    """
    Given the song's BPM and TPB, quantize the timeline's start and end times to ticks.

    :param timeline: A list of `AbsoluteCompleteNote` objects.
    :param song: The `Song` object to use.
    :return: A list of `AbsoluteCompleteNoteWithTick` objects.
    """
    tick_time = (60 / song.beats_per_minute) / song.ticks_per_beat  # in secs
    logger.debug(f"Quantizing note times to ticks based of song BPM of "
                 f"{song.beats_per_minute} and TPB of {song.ticks_per_beat} - one tick "
                 f"is 1/{1 / tick_time} ({tick_time}) seconds long")

    res = []

    for old_note in timeline:
        new_start_tick = round(old_note.start_time / tick_time)
        # Ensure all notes last for one tick
        new_end_tick = max(round(old_note.end_time / tick_time), new_start_tick + 1)
        res.append(AbsoluteCompleteNoteWithTick(
            start_tick=new_start_tick,
            end_tick=new_end_tick,
            note=old_note.note,
            velocity=old_note.velocity,
            instrument=old_note.instrument,
            is_drum=old_note.is_drum
        ))

    return res


def find_all_melodic_instruments(timeline: List[AbsoluteCompleteNoteWithTick]) -> List[
    int]:
    """
    Search the timeline for all unique melodic instruments.

    :param timeline: A list of `AbsoluteTimeMessage` objects.
    :return: A list of ints, representing what general MIDI instruments are in the song.
    """
    logger.debug("Finding all melodic instruments in the timeline")

    return list(sorted(set([m.instrument for m in timeline if not m.is_drum])))


def find_all_drum_notes_used(timeline: List[AbsoluteCompleteNoteWithTick]) -> List[int]:
    """
    Search the timeline for all unique drum notes.

    :param timeline: A list of `AbsoluteTimeMessage` objects.
    :return: A list of ints, representing what general MIDI drum notes are in the song.
    """
    logger.debug("Finding all drum notes in the timeline")

    return list(sorted(set([m.note for m in timeline if m.is_drum])))


def timeline_group_by_instrument(timeline: List[AbsoluteCompleteNoteWithTick]) -> List[
    List[AbsoluteCompleteNoteWithTick]]:
    """
    Split up the timeline by instrument.

    :param timeline: A list of `AbsoluteCompleteNote` objects.
    :return: A list of lists of `AbsoluteCompleteNoteWithTick` objects. (Each list of
     notes within the list have the same instrument)
    """
    logger.debug("Splitting up the timeline by instruments")

    used_melodics = find_all_melodic_instruments(timeline)
    used_drums = find_all_drum_notes_used(timeline)
    logger.debug(f"Song used {len(used_melodics)} melodic instruments and "
                 f"{len(used_drums)} unique drum notes")

    tracks = []

    for melodic in used_melodics:
        tracks.append([note for note in timeline if
                       note.instrument == melodic and not note.is_drum])

    if len(used_drums) > 0:
        tracks.append([note for note in timeline if note.is_drum])

    logger.debug(f"Split up global timeline into {len(tracks)} tracks")

    return tracks


def timeline_split_into_two_tracks_if_needed(
        timeline: List[List[AbsoluteCompleteNoteWithTick]]) -> List[
    List[AbsoluteCompleteNoteWithTick]]:
    """
    Go through the melodic tracks in the timeline and check the highest and lowest note
    in each track. If it can't fit into one track (which has a range limit of 64 notes
    from an octave offset) then we use two tracks and move notes as necessary.

    :param timeline: A list of lists of `AbsoluteCompleteNote` objects.
    :return: A list of lists of `AbsoluteCompleteNoteWithTick` objects.
    """
    logger.debug("Checking necessity to split track into two tracks for range")

    new_tracks: List[List[AbsoluteCompleteNoteWithTick]] = []

    tracks_that_fit = 0
    tracks_that_split = 0

    for old_track in timeline:
        # drum tracks don't use octave offsets, only 61 samples max as well
        if old_track[0].is_drum:
            new_tracks.append(old_track)
            tracks_that_fit += 1
            continue

        all_notes = [n.note for n in old_track]
        highest_note = max(all_notes)
        lowest_note = min(all_notes)

        def octave_offset_work(octave: int) -> bool:
            return ((((octave - 2) * 12) <= lowest_note) and
                    (highest_note <= ((octave - 2) * 12 + 63)))

        # does ANY octave offset from [2, 7] work?
        if any([octave_offset_work(o) for o in range(2, 8)]):
            # we don't need to modify, when constructing the MakeCode Arcade Tracks,
            # we'll find the correct octave offset again
            new_tracks.append(old_track)
            tracks_that_fit += 1
        else:
            # split into two tracks, using octave offsets 2 and 7 guarantee covering the
            # full MIDI range
            low_track = [n for n in old_track if n.note < 64]
            high_track = [n for n in old_track if n.note >= 64]
            new_tracks.append(low_track)
            new_tracks.append(high_track)
            tracks_that_split += 1

    logger.debug(f"{tracks_that_fit} tracks fit within one track's range, "
                 f"{tracks_that_split} tracks had to split, total of {len(new_tracks)} "
                 f"tracks in timeline")

    return new_tracks


@dataclass
class AbsoluteCompleteChordWithTick:
    start_tick: int
    end_tick: int

    notes: List[int]
    velocity: int

    instrument: int
    is_drum: bool


def timeline_group_into_perfect_chords(
        timeline: List[List[AbsoluteCompleteNoteWithTick]]) -> List[
    List[AbsoluteCompleteChordWithTick]]:
    """
    Go through the tracks in the timeline and group up notes that share the same start
    and end tick and velocity and instruments to create "perfect" chords.

    :param timeline: A list of lists of `AbsoluteCompleteNoteWithTick` objects.
    :return: A list of lists of `AbsoluteCompleteChordWithTick` objects.
    """
    logger.debug("Grouping notes in timeline into perfect chords")

    new_tracks: List[List[AbsoluteCompleteChordWithTick]] = []

    old_note_count = sum(len(track) for track in timeline)

    for old_track in timeline:
        # use dictionaries to quickly find existing chords
        # key is a tuple of (start_tick, end_tick, velocity, instrument, is_drum)
        # values are notes in the chord
        chords: Dict[Tuple[int, int, int, int, bool], List[int]] = defaultdict(list)

        for note in old_track:
            key = (note.start_tick, note.end_tick, note.velocity, note.instrument,
                   note.is_drum)
            # if a note has the same start and end tick and velocity and instrument
            # they can be played as a chord
            chords[key].append(note.note)

        new_track: List[AbsoluteCompleteChordWithTick] = []
        for (start_tick, end_tick, velocity, instrument,
             is_drum), notes in chords.items():
            new_track.append(AbsoluteCompleteChordWithTick(
                start_tick=start_tick, end_tick=end_tick,
                notes=notes,
                velocity=velocity,
                instrument=instrument, is_drum=is_drum
            ))
        new_track.sort(key=lambda c: (c.start_tick, c.end_tick))
        new_tracks.append(new_track)

    new_chord_count = sum(len(track) for track in new_tracks)

    logger.debug(f"Created "
                 f"{sum([sum([1 if len(n.notes) > 1 else 0 for n in t]) for t in new_tracks])}"
                 f" multi-note chords (dropped from {old_note_count} notes to "
                 f"{new_chord_count} chords)")

    return new_tracks


def convert_midi_to_song(midi_song: MidiFile,
                         mapping: InstrumentParameterMapping) -> Song:
    """
    Convert a MIDI file into a MakeCode Arcade song.

    :param midi_song: A `MidiFile` object.
    :param mapping: An `InstrumentParameterMapping` object, loaded from
     `load_instrument_params`.
    :return: MakeCode Arcade `Song` object.
    """
    logger.debug("Converting MIDI file into MakeCode Arcade song")

    global_timeline: List[AbsoluteTimeMessage] = timeline_build(midi_song)
    global_timeline: List[
        AbsoluteTimeMessageWithInstrument] = timeline_find_instrument_data(
        global_timeline)
    global_timeline: List[AbsoluteCompleteNote] = timeline_group_messages(
        global_timeline)

    song = Song(
        measures=1,
        beats_per_measure=4,
        beats_per_minute=120,  # beat every 1/2 seconds
        ticks_per_beat=24,  # each tick is 1/48 seconds long
        tracks=[]
    )

    global_timeline: List[
        AbsoluteCompleteNoteWithTick] = timeline_quantize_to_song_ticks(global_timeline,
                                                                        song)
    global_timeline: List[
        List[AbsoluteCompleteNoteWithTick]] = timeline_group_by_instrument(
        global_timeline)
    global_timeline = timeline_split_into_two_tracks_if_needed(global_timeline)
    global_timeline: List[
        List[AbsoluteCompleteChordWithTick]] = timeline_group_into_perfect_chords(
        global_timeline)

    return song
