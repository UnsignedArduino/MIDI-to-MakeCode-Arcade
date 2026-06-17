import logging
from argparse import ArgumentParser, Namespace
from pathlib import Path
from typing import List, Tuple

from mido import MidiFile

from arcade.music import encode_song_to_hex
from midi_to_song import InstrumentParameterMapping, TestingOptionsForMIDIToSong, \
    convert_midi_to_song
from midi_to_song.models import TestingOptionsForLoadInstrumentParams
from utils.logger import create_logger
from utils.strings import parse_range

logger = create_logger(name=__name__, level=logging.INFO)


def generate_and_parse_args() -> Namespace:
    """
    Parse this CLI tool's arguments.

    :return: A `Namespace` object with parsed CLI arguments.
    """
    parser = ArgumentParser(
        description="Convert a MIDI file to a MakeCode Arcade song.")
    parser.add_argument("--input", "-i", type=Path, required=True,
                        help="Input MIDI file.")
    parser.add_argument("--input-instrument-params", "-p", type=Path,
                        required=True, help="Input instrument parameter mapping file.")
    parser.add_argument("--output", "-o", type=Path,
                        help="Output TypeScript file path, otherwise will write to "
                             "stdout. If specified, this will overwrite the file if it "
                             "already exists, and the parent directories must exist.")
    parser.add_argument("--generate-extra-code", action="store_true",
                        help="Generate extra code to help song visualizers display "
                             "properly. The output will be valid TypeScript code. "
                             "Incompatible with test options that sample melodic "
                             "instruments.")
    parser.add_argument("--debug", action="store_const",
                        const=logging.DEBUG, default=logging.INFO,
                        help="Include debug messages. Defaults to info and greater "
                             "severity messages only.")

    testing_group = parser.add_argument_group("Testing options")
    # Melodic instrument tests
    testing_group.add_argument("--test-replace-all-melodics-with", type=int,
                               default=None,
                               help="Replace all melodic tracks in a song with the "
                                    "specific MIDI instrument.")
    testing_group.add_argument("--test-ask-to-replace-all-melodics-with",
                               action="store_true",
                               help="Prompt the user to replace all melodic tracks in "
                                    "a song with a specific MIDI instrument.")
    testing_group.add_argument("--test-sample-melodic-instruments",
                               type=parse_range,
                               help="Pass in a range of MIDI instruments to sample, "
                                    "such as \"0,2,4-10\" to replicate the song "
                                    "several times and replace all melodic tracks in "
                                    "the song with the specific MIDI instrument. "
                                    "Basically does what "
                                    "`--test-replace-all-melodics-with` and "
                                    "`--test-generate-code` but with a bunch of "
                                    "specified instruments.")
    # Drum note tests
    testing_group.add_argument("--test-replace-all-drums-with", type=int,
                               default=None,
                               help="Replace all drum notes in a song with the "
                                    "specific drum note.")
    testing_group.add_argument("--test-ask-to-replace-all-drums-with",
                               action="store_true",
                               help="Prompt the user to replace all drum notes in "
                                    "a song with a specific drum note.")
    # Misc
    testing_group.add_argument("--test-generate-code", action="store_true",
                               help="Generate the MakeCode Arcade code to play the song.")
    testing_group.add_argument("--test-force-instrument-param-load",
                               action="store_true",
                               help="Forcibly load the instrument parameter mapping "
                                    "file, even if it would normally cause errors.")

    args = parser.parse_args()
    logger.debug(f"Received arguments: {args}")
    return args


def generate_testing_options(args: Namespace) -> Tuple[
    TestingOptionsForLoadInstrumentParams, TestingOptionsForMIDIToSong]:
    """
    Take CLI arguments and generate the testing options objects for both loading
    instrument parameters and MIDI song generation.

    :param args: A `Namespace` object with parsed CLI arguments.
    :return: A tuple of `TestingOptionsForLoadInstrumentParams` and
     `TestingOptionsForMIDIToSong`.
    """
    TOs_midi_to_song = TestingOptionsForMIDIToSong()
    TOs_load_instrument_params = TestingOptionsForLoadInstrumentParams()

    # Testing options for loading instrument params
    TOs_load_instrument_params.force_load = args.test_force_instrument_param_load
    # Testing options for MIDI song generation
    TOs_midi_to_song.replace_all_melodics_with = args.test_replace_all_melodics_with
    TOs_midi_to_song.replace_all_drums_with = args.test_replace_all_drums_with
    TOs_midi_to_song.generate_code = args.test_generate_code
    # Do any user prompting as necessary
    if args.test_ask_to_replace_all_melodics_with:
        TOs_midi_to_song.replace_all_melodics_with = int(
            input("Replace all melodics with MIDI instrument: "))
    if args.test_ask_to_replace_all_drums_with:
        TOs_midi_to_song.replace_all_drums_with = int(
            input("Replace all drums with MIDI drum note: "))
    # Logging
    if TOs_midi_to_song.replace_all_melodics_with is not None:
        logger.info(f"Replacing all melodic instruments with MIDI instrument "
                    f"{TOs_midi_to_song.replace_all_melodics_with} in final output")
    if TOs_midi_to_song.generate_code:
        logger.info(
            f"Final result will be valid MakeCode Arcade TypeScript code to play "
            f"the song")

    return TOs_load_instrument_params, TOs_midi_to_song


def generate_single_conversion(midi: MidiFile,
                               mapping: InstrumentParameterMapping,
                               generate_extra_code: bool,
                               testing_opts_for_midi_to_song: TestingOptionsForMIDIToSong) -> str:
    """
    Single conversion from a MIDI file with an instrument parameter mapping to the
    final output to be printed or written to a file, follwing the provided testing
    options.

    :param midi: A `MidiFile` object.
    :param mapping: An `InstrumentParameterMapping` object.
    :param generate_extra_code: Whether to generate extra code to help song visualizers
     display properly.
    :param testing_opts_for_midi_to_song: A `TestingOptionsForMIDIToSong` object.
    :return: The final output.
    """
    logger.info("Generating single conversion")
    (song,
     drum_note_list,
     track_instrument_list) = convert_midi_to_song(midi, mapping,
                                                   testing_opts_for_midi_to_song)
    h = encode_song_to_hex(song)
    final_output = f"hex`{h}`"
    logger.info("Finished converting MIDI file")
    # Generate sample code or
    if testing_opts_for_midi_to_song.generate_code:
        final_output = f"""music.play(music.createSong(
  {final_output}
), music.PlaybackMode.UntilDone);"""
    # Generate extra code
    elif generate_extra_code:
        logger.debug("Writing out extra code")
        final_output = f"""const songHex = {final_output};
const midiDrumNoteMap = {drum_note_list};
const trackInstrumentMap = {track_instrument_list};
"""
    # Add comments
    if testing_opts_for_midi_to_song.replace_all_melodics_with is not None:
        final_output = f"""// melodics replaced with MIDI instrument {testing_opts_for_midi_to_song.replace_all_melodics_with} 
{final_output}"""
    if testing_opts_for_midi_to_song.replace_all_drums_with is not None:
        final_output = f"""// drums replaced with MIDI drum note {testing_opts_for_midi_to_song.replace_all_drums_with}
{final_output}"""

    if any((testing_opts_for_midi_to_song.generate_code, generate_extra_code,
            testing_opts_for_midi_to_song.replace_all_melodics_with,
            testing_opts_for_midi_to_song.replace_all_drums_with)):
        final_output = f"""// Generated by https://github.com/UnsignedArduino/MIDI-to-MakeCode-Arcade
{final_output}"""

    return final_output


def generate_melodic_instrument_sample(midi: MidiFile,
                                       mapping: InstrumentParameterMapping,
                                       testing_opts_for_midi_to_song: TestingOptionsForMIDIToSong,
                                       melodics_to_sample: List[int]) -> str:
    """
    Multiple conversions from a MIDI file, overriding all melodic instruments with the
    specified instrument and generating the code. Then repeated for every MIDI melodic
    instrument to sample.

    :param midi: A `MidiFile` object.
    :param mapping: An `InstrumentParameterMapping` object.
    :param testing_opts_for_midi_to_song: A `TestingOptionsForMIDIToSong` object.
    :param melodics_to_sample: A list of MIDI instruments to sample.
    :return: The final output.
    """
    logger.info(f"Generating melodic instrument sample of {melodics_to_sample}")
    final_output = f"""// Generated by https://github.com/UnsignedArduino/MIDI-to-MakeCode-Arcade
// generated melodic instrument sample
// instruments {melodics_to_sample}\n"""

    for instrument in melodics_to_sample:
        logger.info(f"Generating code for melodic instrument {instrument}")
        testing_opts_for_midi_to_song.replace_all_melodics_with = instrument
        song, _, _ = convert_midi_to_song(midi, mapping, testing_opts_for_midi_to_song)
        h = encode_song_to_hex(song)
        final_output += f"""// melodics replaced with MIDI instrument {instrument}
info.setScore({instrument});
music.play(music.createSong(
  hex`{h}`
), music.PlaybackMode.UntilDone);
"""

    return final_output
