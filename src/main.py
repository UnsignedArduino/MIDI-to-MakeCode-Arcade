import logging
from argparse import ArgumentParser
from pathlib import Path

from mido import MidiFile

from arcade.music import encode_song_to_hex
from converter.instruments import load_instrument_params
from converter.midi_to_song import convert_midi_to_song
from utils.logger import create_logger, set_all_stdout_logger_levels

parser = ArgumentParser(description="Convert a MIDI file to a MakeCode Arcade song.")
parser.add_argument("--input", "-i", type=Path,
                    help="Input MIDI file.")
parser.add_argument("--input-instrument-params", "-p", type=Path,
                    help="Input instrument parameter mapping file.")
# parser.add_argument("--output", "-o", type=Path,
#                     help="Output TypeScript file path, otherwise will write to stdout.")
parser.add_argument("--debug", action="store_const",
                    const=logging.DEBUG, default=logging.INFO,
                    help="Include debug messages. Defaults to info and "
                         "greater severity messages only.")
args = parser.parse_args()
logger = create_logger(name=__name__, level=logging.INFO)
set_all_stdout_logger_levels(args.debug)
logger.debug(f"Received arguments: {args}")

input_path = Path(args.input)
logger.debug(f"Reading MIDI file from {input_path}")

mid = MidiFile(input_path)
logger.debug(f"Found {len(mid.tracks)} tracks, length of {mid.length}s")

input_instrument_param_path = Path(args.input_instrument_params)
logger.debug(f"Reading instrument parameter mapping from {input_instrument_param_path}")

mapping = load_instrument_params(input_instrument_param_path.read_text())
logger.debug(f"Mapped {len(mapping.melodic_instruments)} melodic instruments and "
             f"{len(mapping.drum_instruments)} drum instruments")

song = convert_midi_to_song(mid, mapping)
h = encode_song_to_hex(song)
logger.info("Finished converting MIDI file")
print(f"hex`{h}`")
