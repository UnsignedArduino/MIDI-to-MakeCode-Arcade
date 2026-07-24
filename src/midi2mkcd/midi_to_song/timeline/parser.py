import logging
from typing import Dict, List, Tuple

from mido import MidiFile, tick2second

from midi2mkcd.midi_to_song.models import AbsoluteCompleteNote, \
    AbsoluteTickMessage, AbsoluteTimeMessage, \
    AbsoluteTimeMessageWithInstrument, ChannelState, DrumDeterminationSource
from midi2mkcd.utils.logger import create_logger

logger = create_logger(name=__name__, level=logging.INFO)


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
