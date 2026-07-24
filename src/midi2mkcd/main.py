import logging
from pathlib import Path

from mido import MidiFile

from midi2mkcd.cli import generate_and_parse_args, generate_melodic_instrument_sample, \
    generate_single_conversion, generate_testing_options
from midi2mkcd.midi_to_song.instruments import load_instrument_params
from midi2mkcd.utils.logger import create_logger, set_all_stdout_logger_levels

try:
    import pyperclip

    CLIPBOARD_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    CLIPBOARD_AVAILABLE = False

logger = create_logger(name=__name__, level=logging.INFO)


def main():
    args = generate_and_parse_args()
    debug = bool(args.debug)
    if debug:
        set_all_stdout_logger_levels(logging.DEBUG)
    logger.debug(f"Received arguments: {args}")

    (testing_opts_for_load_instrument_params,
     testing_opts_for_midi_to_song) = generate_testing_options(args)

    input_path = Path(args.input)
    logger.info(f"Reading MIDI file from {input_path}")

    mid = MidiFile(input_path)
    logger.debug(f"Found {len(mid.tracks)} tracks, length of {mid.length}s")

    input_instrument_param_path = Path(args.input_instrument_params)
    logger.info(
        f"{"Forcibly reading" if testing_opts_for_load_instrument_params.force_load else "Reading"}"
        f" instrument params from {input_instrument_param_path}")

    mapping = load_instrument_params(input_instrument_param_path.read_text(),
                                     testing_opts_for_load_instrument_params)
    logger.debug(f"Mapped {len(mapping.melodic_instruments)} melodic instruments and "
                 f"{len(mapping.drum_instruments)} drum instruments")

    melodic_sample = args.test_sample_melodic_instruments
    generate_extra_code = args.generate_extra_code
    if melodic_sample and generate_extra_code:
        raise ValueError("Melodic sample option and generating extra code are mutually "
                         "exclusive!")
    if testing_opts_for_midi_to_song.generate_code and generate_extra_code:
        raise ValueError(
            "Generating sample code and generating extra code are mutually "
            "exclusive!")

    if melodic_sample is not None:
        final_output = generate_melodic_instrument_sample(mid, mapping,
                                                          testing_opts_for_midi_to_song,
                                                          melodic_sample)
    else:
        final_output = generate_single_conversion(mid, mapping, generate_extra_code,
                                                  testing_opts_for_midi_to_song)

    output_path = Path(args.output) if args.output is not None else None
    if output_path is not None:
        logger.info(f"Writing result to {output_path}")
        output_path.write_text(final_output)
    else:
        logger.info(f"Writing result to stdout")
        print(f"\n{final_output}\n")

    if args.test_copy_result_to_clipboard:
        if not CLIPBOARD_AVAILABLE:
            raise RuntimeError(
                "pyperclip is not available (pip install pyperclip), cannot "
                "automatically copy result to the clipboard!")
        pyperclip.copy(final_output)
        logger.info("Copied result to clipboard")


if __name__ == "__main__":
    main()
