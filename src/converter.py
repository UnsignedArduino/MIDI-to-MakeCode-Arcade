import logging
from enum import Enum
from typing import Optional, Union

from mido import MidiFile

from arcade.music import encodeSong
from midi_to_song import midi_to_song
from utils.logger import create_logger

logger = create_logger(name=__name__, level=logging.DEBUG)


class OutputOptions(Enum):
    MAKECODE_ARCADE_STRING = "makecode_arcade_string"


def convert(input: MidiFile, output: OutputOptions,
            track: Optional[Union[int, str]] = None,
            divisor: Optional[float] = 1,
            char_break: Optional[int] = 0) -> Union[str]:
    """
    Converts a MIDI file to a MakeCode Arcade song.

    :param input: Input MIDI file.
    :param output: An OutputOptions enum value. Currently only supports
     MAKECODE_ARCADE_STRING.
    :param track: The track to use. Can be an index or a string. Defaults to
     the first track.
    :param divisor: A float to divide the number of measures used, to fit big songs
     into the maximum number of measures being 255. Defaults to 1.
    :param char_break: An integer to break the hex string after so many characters.
     Defaults to 0. (No breaking)
    :return: A string which is a MakeCode Arcade song.
    """
    song = midi_to_song(input, int(track) if track.isnumeric() else track,
                        divisor)
    bin_result = encodeSong(song)

    logger.debug(f"Generated {len(bin_result)} bytes, converting to text")

    logger.debug(f"Using character break of {char_break}")

    hex_result = map(lambda v: format(v, "02x"), bin_result)
    result = "hex`"
    for i, hex_num in enumerate(hex_result):
        if char_break != 0 and i % char_break == 0:
            result += "\n    "
        result += hex_num
    if char_break != 0:
        result += "\n"
    result += "`"
    logger.debug(f"Hex string result is {len(result)} characters long")
    
    return result
