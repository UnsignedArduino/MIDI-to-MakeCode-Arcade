import logging
import sys
from argparse import ArgumentParser, ArgumentTypeError, Namespace
from pathlib import Path

from midi2mkcd.utils.strings import parse_range

logger = logging.getLogger(__name__)

_cli_log_handler: logging.Handler | None = None


def range_arg(value: str) -> list[int]:
    """`parse_range` for argparse, which shows `ArgumentTypeError` messages."""
    try:
        return parse_range(value)
    except ValueError as e:
        raise ArgumentTypeError(str(e)) from None


def configure_logging(level: int) -> None:
    """
    Send the package's log records to stderr at the given level, so stdout only
    carries generated code. Safe to call more than once (e.g. in tests): the
    handler installed by a previous call is replaced, not duplicated.

    :param level: The logging level for the `midi2mkcd` logger.
    """
    global _cli_log_handler
    package_logger = logging.getLogger("midi2mkcd")
    if _cli_log_handler is not None:
        package_logger.removeHandler(_cli_log_handler)
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    package_logger.addHandler(handler)
    package_logger.setLevel(level)
    _cli_log_handler = handler


def generate_and_parse_args() -> Namespace:
    """
    Parse this CLI tool's arguments.

    :return: A `Namespace` object with parsed CLI arguments.
    """
    parser = ArgumentParser(
        description="Convert a MIDI file to a MakeCode Arcade song."
    )
    parser.add_argument(
        "--input", "-i", type=Path, required=True, help="Input MIDI file."
    )
    parser.add_argument(
        "--input-instrument-params",
        "-p",
        type=Path,
        required=True,
        help="Input instrument parameter mapping file.",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        help="Output TypeScript file path, otherwise will write to "
        "stdout. If specified, this will overwrite the file if it "
        "already exists, and the parent directories must exist.",
    )
    parser.add_argument(
        "--generate-extra-code",
        action="store_true",
        help="Generate extra code to help song visualizers display "
        "properly. The output will be valid TypeScript code. "
        "Incompatible with test options that sample melodic "
        "instruments.",
    )
    parser.add_argument(
        "--debug",
        action="store_const",
        const=logging.DEBUG,
        default=logging.INFO,
        help="Include debug messages. Defaults to info and greater "
        "severity messages only.",
    )

    testing_group = parser.add_argument_group("Testing options")
    # Melodic instrument tests
    testing_group.add_argument(
        "--test-replace-all-melodics-with",
        type=int,
        default=None,
        help="Replace all melodic tracks in a song with the specific MIDI instrument.",
    )
    testing_group.add_argument(
        "--test-ask-to-replace-all-melodics-with",
        action="store_true",
        help="Prompt the user to replace all melodic tracks in "
        "a song with a specific MIDI instrument.",
    )
    testing_group.add_argument(
        "--test-sample-melodic-instruments",
        type=range_arg,
        help="Pass in a range of MIDI instruments to sample, "
        'such as "0,2,4-10" to replicate the song '
        "several times and replace all melodic tracks in "
        "the song with the specific MIDI instrument. "
        "Basically does what "
        "`--test-replace-all-melodics-with` and "
        "`--test-generate-code` but with a bunch of "
        "specified instruments.",
    )
    # Drum note tests
    testing_group.add_argument(
        "--test-replace-all-drums-with",
        type=int,
        default=None,
        help="Replace all drum notes in a song with the specific drum note.",
    )
    testing_group.add_argument(
        "--test-ask-to-replace-all-drums-with",
        action="store_true",
        help="Prompt the user to replace all drum notes in "
        "a song with a specific drum note.",
    )
    # Misc
    testing_group.add_argument(
        "--test-generate-code",
        action="store_true",
        help="Generate the MakeCode Arcade code to play the song.",
    )
    testing_group.add_argument(
        "--test-force-instrument-param-load",
        action="store_true",
        help="Forcibly load the instrument parameter mapping "
        "file, even if it would normally cause errors.",
    )
    testing_group.add_argument(
        "--test-copy-result-to-clipboard",
        action="store_true",
        help="If pyperclip (uv pip install pyperclip) is "
        "available and this option is specified, the "
        "output will also be copied to the clipboard.",
    )

    args = parser.parse_args()
    logger.debug(f"Received arguments: {args}")
    return args
