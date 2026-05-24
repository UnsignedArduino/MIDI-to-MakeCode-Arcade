import logging
from argparse import ArgumentParser
from pathlib import Path

from utils.logger import create_logger, set_all_stdout_logger_levels

parser = ArgumentParser(description="Convert a MIDI file to a MakeCode Arcade song.")
parser.add_argument("--input", "-i", required=True, type=Path,
                    help="Input MIDI file")
parser.add_argument("--output", "-o", type=Path,
                    help="Output text file path, otherwise we will output to "
                         "standard output.")
parser.add_argument("--debug", action="store_const",
                    const=logging.DEBUG, default=logging.INFO,
                    help="Include debug messages. Defaults to info and "
                         "greater severity messages only.")
args = parser.parse_args()
logger = create_logger(name=__name__, level=logging.INFO)
set_all_stdout_logger_levels(args.debug)
logger.debug(f"Received arguments: {args}")
