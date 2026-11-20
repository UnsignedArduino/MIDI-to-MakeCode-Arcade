"""
The `midi2mkcd` command: argument parsing, file I/O, logging setup and the
clipboard. Everything else is a library call.
"""

import logging
import sys
import textwrap
from argparse import (
    ArgumentParser,
    ArgumentTypeError,
    Namespace,
    RawDescriptionHelpFormatter,
)
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, version
from io import TextIOWrapper
from pathlib import Path
from typing import Any

from midi2mkcd import render, tuning
from midi2mkcd.api import (
    ParamsInput,
    convert,
    default_instrument_params_text,
    load_instrument_params,
)
from midi2mkcd.errors import Midi2MkcdError
from midi2mkcd.utils.strings import parse_range

logger = logging.getLogger(__name__)

_cli_log_handler: logging.Handler | None = None

FORMATS = ("hex", "code", "code_extra")
FORMAT_HELP = (
    "hex: a hex`...` literal.\n"
    "code: a statement that plays the song in Arcade.\n"
    "code_extra: constants for the Song-Visualizer project."
)
COPY_HELP = (
    "Also copy the output to the clipboard (needs the 'optional' extra: "
    'pip install "midi2mkcd[optional]").'
)
COPY_INSTALL_ERROR = '--copy needs pyperclip: pip install "midi2mkcd[optional]"'


def range_arg(value: str) -> list[int]:
    """`parse_range` for argparse, which shows `ArgumentTypeError` messages."""
    try:
        return parse_range(value)
    except ValueError as e:
        raise ArgumentTypeError(str(e)) from None


def configure_logging(level: int) -> None:
    """
    Send the package's log records to stderr at the given level, so stdout only
    carries generated output. Safe to call more than once (e.g. in tests): the
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


class _HelpFormatter(RawDescriptionHelpFormatter):
    """Keeps descriptions as written and line breaks inside option help."""

    def _split_lines(self, text: str, width: int) -> list[str]:
        return [
            line
            for paragraph in text.splitlines()
            for line in textwrap.wrap(paragraph, width) or [""]
        ]


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return the exit status."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.handler is _run_tune_replace and args.melodics is args.drums is None:
        args.parser.error("give --melodics, --drums or both")
    configure_logging(logging.DEBUG if args.verbose else logging.INFO)
    logger.debug(f"Received arguments: {args}")
    try:
        status: int = args.handler(args)
        return status
    except (Midi2MkcdError, OSError) as e:
        if args.verbose:
            raise
        print(f"error: {e}", file=sys.stderr)
        return 1


def build_parser() -> ArgumentParser:
    """Build the argument parser for every command."""
    parser = ArgumentParser(
        prog="midi2mkcd",
        description="Convert a MIDI file to a MakeCode Arcade song.",
        epilog="Run 'midi2mkcd COMMAND -h' for a command's options.",
        formatter_class=_HelpFormatter,
    )
    parser.add_argument("--version", action="version", version=_version())
    commands = parser.add_subparsers(title="commands", required=True)

    p = _leaf(commands, "convert", _run_convert, "Convert a MIDI file to Arcade code.",
              "Convert a MIDI file to a MakeCode Arcade song.")  # fmt: skip
    _add_input(p, "MIDI file to convert.")
    _add_format(p, "hex")
    _add_params(p)
    _add_output(p, copy=True)

    tune = _group(commands, "tune", "Tools for auditioning instrument parameters "
                  "against a song.")  # fmt: skip
    p = _leaf(tune, "replace", _run_tune_replace,
              "Convert the song with every melodic and/or drum instrument replaced.",
              "Convert INPUT with every melodic note played by one program and/or\n"
              "every drum note played as one drum note. Give at least one of\n"
              "--melodics and --drums.")  # fmt: skip
    _add_input(p, "MIDI file to tune against.")
    p.add_argument("--melodics", type=int, metavar="N",
                   help="Play every melodic note with MIDI program N.")  # fmt: skip
    p.add_argument("--drums", type=int, metavar="N",
                   help="Play every drum note as MIDI drum note N.")  # fmt: skip
    _add_format(p, "code")
    _add_tune_common(p)

    p = _leaf(tune, "sample", _run_tune_sample,
              "Play the song once per instrument in a range.",
              "Output one Arcade program that plays INPUT once per instrument in\n"
              "RANGE, each time with every melodic note (or every drum note)\n"
              "replaced by that instrument.")  # fmt: skip
    _add_input(p, "MIDI file to tune against.")
    which = p.add_mutually_exclusive_group(required=True)
    which.add_argument("--melodics", type=range_arg, metavar="RANGE",
                       help='MIDI programs to sample, e.g. "0,2,4-10".')  # fmt: skip
    which.add_argument("--drums", type=range_arg, metavar="RANGE",
                       help='MIDI drum notes to sample, e.g. "35-40".')  # fmt: skip
    _add_tune_common(p)

    p = _leaf(tune, "report", _run_tune_report,
              "List the instruments and drum notes the song uses.",
              "Print a table of the instruments and drum notes INPUT uses (with\n"
              "their General MIDI names), their note and velocity ranges, and\n"
              "whether PARAMS has an entry for each.")  # fmt: skip
    _add_input(p, "MIDI file to report on.")
    _add_tune_common(p)

    params = _group(commands, "params", "Print or check instrument parameter files.")
    p = _leaf(params, "show", _run_params_show,
              "Print the bundled instrument parameters (or a blank template).",
              "Print the bundled instrument parameters, as a starting point for\n"
              "your own file.")  # fmt: skip
    p.add_argument("--blank", action="store_true",
                   help="Print a blank template instead.")  # fmt: skip
    p.add_argument("-o", "--output", type=Path, metavar="OUT",
                   help="Write the YAML here instead of stdout.")  # fmt: skip
    _add_verbose(p)

    p = _leaf(params, "check", _run_params_check, "Lint an instrument parameter file.",
              "Lint an instrument parameter file and print one problem per line.\n"
              "Exit status 1 if there are any.")  # fmt: skip
    p.add_argument("-p", "--params", type=Path, metavar="PARAMS",
                   help="File to check. Default: the bundled parameters.")  # fmt: skip
    _add_verbose(p)
    return parser


def _version() -> str:
    try:
        return f"midi2mkcd {version('midi2mkcd')}"
    except PackageNotFoundError:
        return "midi2mkcd (unknown version)"


def _group(commands: Any, name: str, summary: str) -> Any:
    group = commands.add_parser(
        name, help=summary, description=summary, formatter_class=_HelpFormatter
    )
    return group.add_subparsers(title="commands", required=True)


def _leaf(
    commands: Any,
    name: str,
    handler: Callable[[Namespace], int],
    summary: str,
    description: str,
) -> ArgumentParser:
    p: ArgumentParser = commands.add_parser(
        name, help=summary, description=description, formatter_class=_HelpFormatter
    )
    p.set_defaults(handler=handler, parser=p)
    return p


def _add_input(p: ArgumentParser, help: str) -> None:
    p.add_argument("input", type=Path, metavar="INPUT", help=help)


def _add_format(p: ArgumentParser, default: str) -> None:
    p.add_argument("-f", "--format", choices=FORMATS, default=default,
                   help=f"{FORMAT_HELP}\nDefault: {default}.")  # fmt: skip


def _add_params(p: ArgumentParser) -> None:
    p.add_argument("-p", "--params", type=Path, metavar="PARAMS",
                   help="Instrument parameter YAML file. Default: the bundled "
                   "parameters (see 'midi2mkcd params show').")  # fmt: skip


def _add_output(p: ArgumentParser, *, copy: bool) -> None:
    p.add_argument("-o", "--output", type=Path, metavar="OUT",
                   help="Write the output to this file (UTF-8, overwritten if it "
                   "exists). Default: stdout.")  # fmt: skip
    if copy:
        p.add_argument("--copy", action="store_true", help=COPY_HELP)
    _add_verbose(p)


def _add_verbose(p: ArgumentParser) -> None:
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Show debug logging on stderr, and tracebacks for "
                   "errors.")  # fmt: skip


def _add_tune_common(p: ArgumentParser) -> None:
    _add_params(p)
    p.add_argument("--allow-incomplete", action="store_true",
                   help="Skip incomplete or invalid entries in the params file "
                   "instead of failing. Songs that use a skipped instrument "
                   "still fail.")  # fmt: skip
    _add_output(p, copy=True)


def _run_convert(args: Namespace) -> int:
    result = convert(args.input, _read_params(args.params))
    return _emit(result.to_typescript(args.format), args.output, args.copy)


def _run_tune_replace(args: Namespace) -> int:
    result = tuning.convert_replaced(
        args.input,
        _read_params(args.params, allow_incomplete=args.allow_incomplete),
        melodics=args.melodics,
        drums=args.drums,
    )
    return _emit(result.to_typescript(args.format), args.output, args.copy)


def _run_tune_sample(args: Namespace) -> int:
    params = _read_params(args.params, allow_incomplete=args.allow_incomplete)
    if args.melodics is not None:
        text = tuning.sample_melodics(args.input, args.melodics, params)
    else:
        text = tuning.sample_drums(args.input, args.drums, params)
    return _emit(text, args.output, args.copy)


def _run_tune_report(args: Namespace) -> int:
    params = _read_params(args.params, allow_incomplete=args.allow_incomplete)
    text = render.usage_report(tuning.report(args.input, params))
    return _emit(text, args.output, args.copy)


def _run_params_show(args: Namespace) -> int:
    if args.blank:
        text = tuning.blank_instrument_params_text()
    else:
        text = default_instrument_params_text()
    return _emit(text, args.output, copy=False)


def _run_params_check(args: Namespace) -> int:
    if args.params is None:
        text = default_instrument_params_text()
    else:
        text = args.params.read_text(encoding="utf-8")
    problems = tuning.check_params(text)
    for problem in problems:
        print(problem)
    if not problems:
        logger.info("No problems found")
    return 1 if problems else 0


def _read_params(path: Path | None, *, allow_incomplete: bool = False) -> ParamsInput:
    """Read a params file as text (or load it leniently); None means bundled."""
    if path is None:
        return None
    logger.info(f"Reading instrument params from {path}")
    text = path.read_text(encoding="utf-8")
    if allow_incomplete:
        return load_instrument_params(text, allow_incomplete=True)
    return text


def _emit(text: str, output: Path | None, copy: bool) -> int:
    """Write the output to a file or stdout, then optionally to the clipboard."""
    if output is not None:
        output.write_text(text + "\n", encoding="utf-8")
        logger.info(f"Wrote {output}")
    else:
        # Redirected stdout on Windows defaults to the ANSI code page, which can't
        # encode every lyric; the console itself handles Unicode already
        if not sys.stdout.isatty() and isinstance(sys.stdout, TextIOWrapper):
            sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(text + "\n")
        sys.stdout.flush()
    if copy:
        try:
            import pyperclip
        except ImportError:
            print(f"error: {COPY_INSTALL_ERROR}", file=sys.stderr)
            return 1
        pyperclip.copy(text)
        logger.info("Copied to clipboard")
    return 0
