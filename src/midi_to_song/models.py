import logging
from dataclasses import dataclass
from enum import IntEnum
from typing import List, Optional

from mido import Message

from utils.logger import create_logger

logger = create_logger(name=__name__, level=logging.INFO)


# All intermediate representations used during conversion


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


@dataclass
class ChannelState:
    program: int  # instrument
    bank_select_msb: int
    bank_select_lsb: int
    is_drum: bool
    drum_determined_by: DrumDeterminationSource


class DrumDeterminationSource(IntEnum):
    DEFAULT = 0
    CC = 1  # control change
    SYSEX = 2


@dataclass
class AbsoluteTimeMessageWithInstrument:
    time: float
    port: int
    instrument: int  # midi instrument
    is_drum: bool
    msg: Message  # note_on and note_off


@dataclass
class AbsoluteCompleteNote:
    start_time: float
    end_time: float

    note: int
    velocity: int

    instrument: int
    is_drum: bool


@dataclass
class AbsoluteCompleteNoteWithTick:
    start_tick: int
    end_tick: int

    note: int
    velocity: int

    instrument: int
    is_drum: bool


@dataclass
class AbsoluteCompleteChordWithTick:
    start_tick: int
    end_tick: int

    notes: List[int]
    velocity: int

    instrument: int
    is_drum: bool


@dataclass
class TestingOptionsForLoadInstrumentParams:
    force_load: Optional[bool] = False


@dataclass
class TestingOptionsForMIDIToSong:
    replace_all_melodics_with: Optional[int] = None
    generate_code: Optional[bool] = False
