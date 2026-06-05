import logging
from argparse import ArgumentParser
from pathlib import Path

from mido import MidiFile

from arcade.music import encode_song_to_hex
from midi_to_song import TestingOptionsForMIDIToSong, convert_midi_to_song
from midi_to_song.instruments import load_instrument_params
from midi_to_song.models import TestingOptionsForLoadInstrumentParams
from utils.logger import create_logger, set_all_stdout_logger_levels
from utils.strings import parse_range

parser = ArgumentParser(description="Convert a MIDI file to a MakeCode Arcade song.")
parser.add_argument("--input", "-i", type=Path, required=True,
                    help="Input MIDI file.")
parser.add_argument("--input-instrument-params", "-p", type=Path,
                    required=True, help="Input instrument parameter mapping file.")
parser.add_argument("--output", "-o", type=Path,
                    help="Output TypeScript file path, otherwise will write to stdout. "
                         "If specified, this will overwrite the file if it already "
                         "exists, and the parent directories must exist.")
parser.add_argument("--debug", action="store_const",
                    const=logging.DEBUG, default=logging.INFO,
                    help="Include debug messages. Defaults to info and "
                         "greater severity messages only.")

testing_group = parser.add_argument_group("Testing options")
testing_group.add_argument("--test-replace-all-melodics-with", type=int,
                           default=None, help="Replace all melodic tracks in a song "
                                              "with the specific MIDI instrument.")
testing_group.add_argument("--test-ask-to-replace-all-melodics-with",
                           action="store_true", help="Prompt the user to replace all "
                                                     "melodic tracks in a song with a "
                                                     "specific MIDI instrument.")
testing_group.add_argument("--test-generate-code", action="store_true",
                           help="Generate the MakeCode Arcade code to play the song.")
testing_group.add_argument("--test-force-instrument-param-load", action="store_true",
                           help="Forcibly load the instrument parameter mapping file, "
                                "even if it would normally cause errors.")
testing_group.add_argument("--test-sample-melodic-instruments", type=parse_range,
                           help="Pass in a range of MIDI instruments to sample, such "
                                "as \"0,2,4-10\" to replicate the song several times "
                                "and replace all melodic tracks in the song with the "
                                "specific MIDI instrument. Basically does what "
                                "`--test-replace-all-melodics-with` and "
                                "`--test-generate-code` but with a bunch of specified "
                                "instruments.")

args = parser.parse_args()
logger = create_logger(name=__name__, level=logging.INFO)
set_all_stdout_logger_levels(args.debug)
logger.debug(f"Received arguments: {args}")

testing_opts_midi_to_song = TestingOptionsForMIDIToSong()
testing_opts_load_instrument_params = TestingOptionsForLoadInstrumentParams()

testing_opts_midi_to_song.replace_all_melodics_with = args.test_replace_all_melodics_with
testing_opts_load_instrument_params.force_load = args.test_force_instrument_param_load
if args.test_ask_to_replace_all_melodics_with:
    testing_opts_midi_to_song.replace_all_melodics_with = int(
        input("Replace all melodics with MIDI "
              "instrument: "))
testing_opts_midi_to_song.generate_code = args.test_generate_code
if testing_opts_midi_to_song.replace_all_melodics_with is not None:
    logger.info(f"Replacing all melodic instruments with MIDI instrument "
                f"{testing_opts_midi_to_song.replace_all_melodics_with} in final output")
if testing_opts_midi_to_song.generate_code:
    logger.info(f"Final result will be valid MakeCode Arcade TypeScript code to play "
                f"the song")

input_path = Path(args.input)
logger.info(f"Reading MIDI file from {input_path}")

mid = MidiFile(input_path)
logger.debug(f"Found {len(mid.tracks)} tracks, length of {mid.length}s")

input_instrument_param_path = Path(args.input_instrument_params)
if testing_opts_load_instrument_params.force_load:
    logger.info(f"Forcibly reading instrument params from "
                f"{input_instrument_param_path}")
else:
    logger.info(
        f"Reading instrument parameter mapping from {input_instrument_param_path}")

mapping = load_instrument_params(input_instrument_param_path.read_text(),
                                 testing_opts_load_instrument_params)
logger.debug(f"Mapped {len(mapping.melodic_instruments)} melodic instruments and "
             f"{len(mapping.drum_instruments)} drum instruments")

melodic_sample = args.test_sample_melodic_instruments
if melodic_sample is not None:
    logger.info(f"Sampling {melodic_sample} melodic instruments")

    final_output = "\n// generated melodic instrument sample\n\n"

    for instrument in melodic_sample:
        logger.info(f"Generating code for melodic instrument {instrument}")
        testing_opts_midi_to_song.replace_all_melodics_with = instrument
        song = convert_midi_to_song(mid, mapping, testing_opts_midi_to_song)
        h = encode_song_to_hex(song)
        final_output += f"""// melodics replaced with MIDI instrument {instrument}
info.setScore({instrument});
music.play(music.createSong(
    hex`{h}`
), music.PlaybackMode.UntilDone);
"""
else:
    song = convert_midi_to_song(mid, mapping, testing_opts_midi_to_song)
    h = encode_song_to_hex(song)
    final_output = f"hex`{h}`"
    logger.info("Finished converting MIDI file")

    if testing_opts_midi_to_song.generate_code:
        final_output = f"""music.play(music.createSong(
    {final_output}
), music.PlaybackMode.UntilDone);
"""
    if testing_opts_midi_to_song.replace_all_melodics_with is not None:
        final_output = (f"// melodics replaced with MIDI instrument "
                        f"{testing_opts_midi_to_song.replace_all_melodics_with}\n{final_output}")

output_path = Path(args.output) if args.output is not None else None
if output_path is not None:
    logger.info(f"Writing result to {output_path}")
    output_path.write_text(final_output)
else:
    if testing_opts_midi_to_song.generate_code:
        final_output = "\n" + final_output
    logger.info(f"Writing result to stdout")
    print(final_output)
