import logging
from argparse import ArgumentParser
from pathlib import Path

from mido import MidiFile

from arcade.tracks import get_available_tracks
from converter import OutputOptions, convert
from utils.logger import create_logger, set_all_stdout_logger_levels

tracks = get_available_tracks()
track_names = [t.name.lower() for t in tracks]
track_ids = [str(t.id) for t in tracks]

parser = ArgumentParser(prog="ArcadeMIDItoSong",
                        description="A program to convert MIDI files to the "
                                    "Arcade song format. ")
parser.add_argument("--input", "-i", required=True, type=Path,
                    help="Input MIDI file")
parser.add_argument("--output", "-o", type=Path,
                    help="Output text file path, otherwise we will output to "
                         "standard output.")
parser.add_argument("--track", "-t", metavar="TRACK",
                    choices=track_ids + track_names,
                    default=track_names[0],
                    help=f"A track to use, which changes the instrument. "
                         f"Available tracks include {track_names}. (You can "
                         f"also use indices 0-{len(track_ids) - 1}) Defaults "
                         f"to '{track_names[0]}'.")
parser.add_argument("--divisor", "-d", type=float,
                    default=1,
                    help="A divisor to reduce (or increase!) the number of "
                         "measures used. A higher float means a longer song "
                         "can fit in the maximum of 255 measures of a song, "
                         "but with less precision. Must be greater than 0, "
                         "defaults to 1 for no division.")
parser.add_argument("--break", "-b", type=int, dest="char_break",
                    default=0,
                    help="Break the hex string after so many characters. "
                         "Defaults to 0 for no breaking.")
parser.add_argument("--debug", action="store_const",
                    const=logging.DEBUG, default=logging.INFO,
                    help="Include debug messages. Defaults to info and "
                         "greater severity messages only.")
args = parser.parse_args()
logger = create_logger(name=__name__, level=logging.INFO)
set_all_stdout_logger_levels(args.debug)
logger.debug(f"Received arguments: {args}")

input_path = Path(args.input)
logger.debug(f"Input path is {input_path}")

midi = MidiFile(input_path)
logger.debug(f"MIDI is {midi.length}s long")

divisor = float(args.divisor)
if not divisor > 0:
    raise ValueError(f"divisor must be a float greater than 0, "
                     f"not {divisor}!")
logger.debug(f"Using divisor of {divisor}")

char_break = int(args.char_break)
if char_break < 0:
    raise ValueError(f"break must be an integer greater than or equal to 0, "
                     f"not {char_break}!")

result = convert(midi, OutputOptions.MAKECODE_ARCADE_STRING, args.track, divisor,
                 char_break)

output_path = args.output
if output_path is None:
    logger.debug("No output path provided, printing to standard output")
    print(result)
else:
    logger.debug(f"Writing to {output_path}")
    Path(output_path).write_text(result)
