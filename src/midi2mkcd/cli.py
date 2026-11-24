"""
The `midi2mkcd` command: argument parsing, file I/O, logging setup and the
clipboard. Everything else is a library call.
"""

import logging
import sys
import textwrap
import time
from argparse import (
    ArgumentParser,
    ArgumentTypeError,
    Namespace,
    RawDescriptionHelpFormatter,
)
from collections.abc import Callable
from io import TextIOWrapper
from pathlib import Path
from typing import Any, Self, TextIO

from midi2mkcd import __version__, audio, render, soundfont, tuning
from midi2mkcd.api import (
    ParamsInput,
    convert,
    default_instrument_params_text,
    load_instrument_params,
)
from midi2mkcd.arcade.music_types import Song
from midi2mkcd.arcade.sim_sequencer import MUSIC_DEFAULT_VOLUME
from midi2mkcd.errors import InvalidMidiError, InvalidSongError, Midi2MkcdError
from midi2mkcd.song_to_midi import song_to_midi
from midi2mkcd.utils.gm_names import drum_note_name, melodic_instrument_name
from midi2mkcd.utils.song_text import decode_song_hex, parse_song_text
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
AUDIO_FORMATS_HELP = (
    "The format comes from OUT's extension: .wav (16-bit PCM) or, with\n"
    'soundfile (pip install "midi2mkcd[compress]"), .flac (lossless),\n'
    ".ogg (Vorbis), .opus (only at 8, 12, 16, 24 or 48 kHz) or .mp3\n"
    "(8 to 48 kHz)."
)


def range_arg(value: str) -> list[int]:
    """`parse_range` for argparse, which shows `ArgumentTypeError` messages."""
    try:
        return parse_range(value)
    except ValueError as e:
        raise ArgumentTypeError(str(e)) from None


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise ArgumentTypeError(f"not an integer: {value!r}") from None
    if number <= 0:
        raise ArgumentTypeError(f"must be positive: {number}")
    return number


def _volume_arg(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise ArgumentTypeError(f"not an integer: {value!r}") from None
    if not 0 <= number <= 255:
        raise ArgumentTypeError(f"must be 0-255: {number}")
    return number


def _quality_arg(value: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise ArgumentTypeError(f"not a number: {value!r}") from None
    if not 0.0 <= number <= 1.0:
        raise ArgumentTypeError(f"must be 0-1: {number}")
    return number


def _velocity_layers_arg(value: str) -> int:
    number = _positive_int(value)
    if number > 127:
        raise ArgumentTypeError(f"must be 1-127: {number}")
    return number


class _ProgressAwareHandler(logging.StreamHandler[TextIO]):
    """
    A stderr handler that doesn't break tqdm progress bars: once tqdm is
    imported (so a bar may be on screen), records go through ``tqdm.write``,
    which clears the bars, prints the line and redraws them below it.
    """

    def emit(self, record: logging.LogRecord) -> None:
        tqdm_module = sys.modules.get("tqdm")
        if tqdm_module is None:
            super().emit(record)
            return
        try:
            tqdm_module.tqdm.write(self.format(record), file=self.stream)
            self.flush()
        except RecursionError:
            raise
        except Exception:  # noqa: BLE001 - as StreamHandler.emit does
            self.handleError(record)


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
    handler = _ProgressAwareHandler(stream=sys.stderr)
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
    if getattr(args, "audio", None) is not None:
        # Not an argparse mutually exclusive group: -o and --copy go together
        clashes = [
            flag
            for flag, given in (
                ("-o/--output", args.output is not None),
                ("--copy", args.copy),
                ("-f/--format", getattr(args, "format", None) is not None),
            )
            if given
        ]
        if clashes:
            args.parser.error(f"--audio can't be used with {', '.join(clashes)}")
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
    parser.add_argument(
        "--version", action="version", version=f"midi2mkcd {__version__}"
    )
    commands = parser.add_subparsers(title="commands", required=True)

    p = _leaf(commands, "convert", _run_convert, "Convert a MIDI file to Arcade code.",
              "Convert a MIDI file to a MakeCode Arcade song.")  # fmt: skip
    _add_input(p, "MIDI file to convert.")
    _add_format(p, "hex")
    _add_params(p)
    _add_output(p, copy=True)

    p = _leaf(commands, "recover-midi", _run_recover_midi,
              "Recover a MIDI file from a converted song (lossy).",
              "Turn a converted song back into a MIDI file, for when the original\n"
              "is lost. Lossy: timing is quantized, note lengths include the gate\n"
              "fix, and velocities are approximate.\n\n"
              "INPUT is convert's output in any format. A code_extra file gives the\n"
              "exact programs, drum notes and lyrics; otherwise programs and drums\n"
              "are found by matching each track's instrument against PARAMS, and\n"
              "lyrics are lost.")  # fmt: skip
    _add_input(p, "Text file with one hex`...` song (convert's output).")
    p.add_argument("-p", "--params", type=Path, metavar="PARAMS",
                   help="The instrument parameter YAML the song was converted with, "
                   "to match instruments and undo pitch compensation. Default: the "
                   "bundled parameters.")  # fmt: skip
    _add_binary_output(p, "MIDI file to write")

    p = _leaf(commands, "render", _run_render,
              "Render a MIDI file or converted song to audio, as the simulator "
              "plays it.",
              "Synthesize a song the way the MakeCode Arcade simulator plays it\n"
              "(Chrome's Web Audio) and write a mono audio file. Needs numpy:\n"
              'pip install "midi2mkcd[audio]".\n\n'
              f"{AUDIO_FORMATS_HELP}\n\n"
              "INPUT is a MIDI file (converted first, with PARAMS) or a text file\n"
              "with one hex`...` song (convert's output in any format). The type\n"
              "is decided by the file's content, not its extension.\n\n"
              "Like the simulator, ticks are 20 ms apart, so converted songs play\n"
              "4% fast.")  # fmt: skip
    _add_input(p, "MIDI file, or text file with one hex`...` song.")
    p.add_argument("-p", "--params", type=Path, metavar="PARAMS",
                   help="Instrument parameter YAML file for MIDI input. Default: "
                   "the bundled parameters. Not allowed for song text input, whose "
                   "instruments are in the song.")  # fmt: skip
    p.add_argument("--rate", type=_positive_int, default=audio.DEFAULT_RATE,
                   metavar="HZ",
                   help=f"Sample rate. Default: {audio.DEFAULT_RATE}.")  # fmt: skip
    p.add_argument("--volume", type=_volume_arg, default=MUSIC_DEFAULT_VOLUME,
                   metavar="0-255",
                   help="music.volume() in the game. Default: "
                   f"{MUSIC_DEFAULT_VOLUME}, Arcade's default.")  # fmt: skip
    p.add_argument("--no-velocity", action="store_true",
                   help="Play every note at full velocity, as Arcade versions "
                   "that ignore the velocity section do.")  # fmt: skip
    _add_quality(p, "Ogg, Opus and MP3", "For FLAC it only trades encoding "
                 "speed against size; ignored for WAV.")  # fmt: skip
    _add_binary_output(p, "Audio file to write: .wav, .flac, .ogg, .opus or .mp3")

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
    _add_format(p, "code", track_given=True)
    _add_audio(p, "the replaced song")
    _add_tune_common(p)

    p = _leaf(tune, "sample", _run_tune_sample,
              "Play the song once per instrument in a range.",
              "Output one Arcade program that plays INPUT once per instrument in\n"
              "RANGE, each time with every melodic note (or every drum note)\n"
              "replaced by that instrument.\n\n"
              "With --audio, render the songs one after another to an audio\n"
              "file instead, 1 s apart, and log when each instrument starts.")  # fmt: skip
    _add_input(p, "MIDI file to tune against.")
    which = p.add_mutually_exclusive_group(required=True)
    which.add_argument("--melodics", type=range_arg, metavar="RANGE",
                       help='MIDI programs to sample, e.g. "0,2,4-10".')  # fmt: skip
    which.add_argument("--drums", type=range_arg, metavar="RANGE",
                       help='MIDI drum notes to sample, e.g. "35-40".')  # fmt: skip
    _add_audio(p, "the songs, one after another,")
    _add_tune_common(p)

    p = _leaf(tune, "report", _run_tune_report,
              "List the instruments and drum notes the song uses.",
              "Print a table of the instruments and drum notes INPUT uses (with\n"
              "their General MIDI names), their note and velocity ranges, and\n"
              "whether PARAMS has an entry for each.")  # fmt: skip
    _add_input(p, "MIDI file to report on.")
    _add_tune_common(p)

    params = _group(
        commands, "params", "Print, check or export instrument parameter files."
    )
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

    p = _leaf(params, "soundfont", _run_params_soundfont,
              "Export the instruments as an SF2 or SF3 SoundFont, for a MIDI editor.",
              "Render every instrument in PARAMS the way the simulator plays it\n"
              "and write an SF2 SoundFont: General MIDI programs in bank 0 and\n"
              "the drum kit in bank 128, as loud as Arcade at the given volume.\n"
              "Needs numpy: pip install \"midi2mkcd[audio]\".\n\n"
              "Melodic samples loop their sustain, and the SF2 release only\n"
              "approximates Arcade's. Rendering every note gives a large file\n"
              "(hundreds of MB): use --step to render fewer notes, or write an\n"
              "SF3 (OUT ending in .sf3: samples compressed with Ogg Vorbis, read\n"
              "by FluidSynth, MuseScore, Polyphone and SpessaSynth; needs\n"
              "soundfile: pip install \"midi2mkcd[compress]\").")  # fmt: skip
    p.add_argument("-p", "--params", type=Path, metavar="PARAMS",
                   help="Instrument parameter YAML file. Default: the bundled "
                   "parameters.")  # fmt: skip
    p.add_argument("--notes", type=range_arg, metavar="RANGE",
                   default=list(soundfont.DEFAULT_NOTES),
                   help='MIDI notes to map, e.g. "36-96". Default: 23-119, the '
                   "simulator's frequency table (others play with no pitch).")  # fmt: skip
    p.add_argument("--step", type=_positive_int, default=1, metavar="N",
                   help="Render every Nth note and pitch-shift the others from it. "
                   "Default: 1 (every note).")  # fmt: skip
    p.add_argument("--rate", type=_positive_int, default=soundfont.DEFAULT_RATE,
                   metavar="HZ",
                   help=f"Sample rate. Default: {soundfont.DEFAULT_RATE}.")  # fmt: skip
    p.add_argument("--velocity-layers", type=_velocity_layers_arg, default=1,
                   metavar="N",
                   help="Render N velocity layers per note, for Arcade's exact "
                   "loudness with pitch compensation, at N times the size. "
                   "Default: 1.")  # fmt: skip
    p.add_argument("--volume", type=_volume_arg, default=MUSIC_DEFAULT_VOLUME,
                   metavar="0-255",
                   help="music.volume() to match. Default: "
                   f"{MUSIC_DEFAULT_VOLUME}, Arcade's default.")  # fmt: skip
    p.add_argument("--allow-incomplete", action="store_true",
                   help="Skip incomplete or invalid entries in the params file "
                   "instead of failing.")  # fmt: skip
    _add_quality(p, "SF3 samples", "Ignored for SF2.")
    _add_binary_output(p, "SoundFont to write: SF3 if it ends in .sf3, else SF2")
    return parser


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


def _add_format(p: ArgumentParser, default: str, *, track_given: bool = False) -> None:
    """With `track_given`, args.format is None unless -f was given (the handler
    applies `default`), so it can be checked against other options."""
    p.add_argument("-f", "--format", choices=FORMATS,
                   default=None if track_given else default,
                   help=f"{FORMAT_HELP}\nDefault: {default}.")  # fmt: skip


def _add_audio(p: ArgumentParser, what: str) -> None:
    p.add_argument("--audio", "--wav", dest="audio", type=Path, metavar="OUT",
                   help=f"Render {what} to this audio file as the simulator plays "
                   "it (like 'midi2mkcd render', at its default quality) instead "
                   "of writing code. The format comes from the extension: .wav, "
                   ".flac, .ogg, .opus or .mp3. Not with -o, --copy or -f. Needs "
                   'numpy: pip install "midi2mkcd[audio]".')  # fmt: skip


def _add_quality(p: ArgumentParser, which: str, note: str) -> None:
    p.add_argument("--quality", type=_quality_arg, metavar="0-1",
                   help=f"Compression quality for {which}: higher is better and "
                   f"bigger. Default: {audio.DEFAULT_QUALITY}. {note}")  # fmt: skip


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


def _add_binary_output(p: ArgumentParser, what: str) -> None:
    p.add_argument("-o", "--output", type=Path, metavar="OUT", required=True,
                   help=f"{what} (overwritten if it exists). Required: binary "
                   "output never goes to stdout.")  # fmt: skip
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


def _run_recover_midi(args: Namespace) -> int:
    logger.info(f"Reading song from {args.input}")
    data = song_to_midi(_read_song_text(args.input), _read_params(args.params))
    return _emit_bytes(data, args.output)


def _run_render(args: Namespace) -> int:
    fmt = _audio_format(args, args.output, args.rate, args.quality)
    song = _read_render_input(args.input, args.params)
    events = audio.song_events(
        song, use_velocity=not args.no_velocity, global_volume=args.volume
    )
    return _write_audio(events, args.output, fmt, args.rate, args.quality)


def _audio_format(
    args: Namespace, output: Path, rate: int, quality: float | None = None
) -> str:
    """The output's format, checked before any rendering. A bad extension or
    rate is a usage error (exit 2); a missing soundfile is an error (exit 1)."""
    try:
        fmt = audio.audio_format_for(output)
        audio.check_audio_format(fmt, rate, quality)
    except ValueError as e:
        args.parser.error(str(e))
    return fmt


def _write_audio(
    events: list[audio.SongEvent],
    output: Path,
    fmt: str,
    rate: int = audio.DEFAULT_RATE,
    quality: float | None = None,
) -> int:
    """Synthesize the events with a progress bar and a speed log line, and write
    them as an audio file in `fmt`."""
    started = time.perf_counter()
    with _ProgressBar("Rendering", "sound") as progress:
        samples = audio.synthesize(events, rate, progress=progress)
    logger.info(
        _render_summary(len(samples) / rate, rate, time.perf_counter() - started)
    )
    data = audio.to_audio_bytes(samples, rate, fmt, quality=quality)
    return _emit_bytes(data, output)


def _render_summary(audio_s: float, rate: int, elapsed_s: float) -> str:
    """E.g. "Rendered 217.68 s at 48000 Hz in 13.02 s (speed=16.7x)", the
    speed as ffmpeg reports it: seconds of audio per second of wall time."""
    text = f"Rendered {audio_s:.2f} s at {rate} Hz in {elapsed_s:.2f} s"
    if elapsed_s > 0:
        text += f" (speed={audio_s / elapsed_s:.1f}x)"
    return text


class _ProgressBar:
    """A tqdm bar on stderr driven by a library ``progress(done, total)`` callback.

    tqdm comes with the `audio` extra; without it there is simply no bar. The
    bar only shows when stderr is a terminal (tqdm's ``disable=None``)."""

    def __init__(self, desc: str, unit: str) -> None:
        self._desc = desc
        self._unit = unit
        self._bar: Any = None
        self._unavailable = False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        if self._bar is not None:
            self._bar.close()

    def __call__(self, done: int, total: int) -> None:
        if self._unavailable:
            return
        if self._bar is None:
            try:
                from tqdm import tqdm
            except ImportError:
                logger.debug("No progress bar: tqdm isn't installed")
                self._unavailable = True
                return
            self._bar = tqdm(
                total=total, desc=self._desc, unit=self._unit, disable=None
            )
        self._bar.update(done - self._bar.n)


def _read_render_input(path: Path, params: Path | None) -> Song:
    """A MIDI file (converted with `params`) or song text, told apart by content."""
    data = path.read_bytes()
    if data.startswith(b"MThd"):
        logger.info(f"Converting MIDI file {path}")
        return convert(data, _read_params(params)).song
    if data.startswith(b"RIFF") and data[8:12] == b"RMID":
        raise InvalidMidiError(
            f"{path} is a RIFF MIDI (.rmi) file, which isn't supported; extract the "
            "MIDI file inside it with another tool"
        )
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise InvalidSongError(
            f"{path} is neither a MIDI file nor a text file with a song"
        ) from None
    if params is not None:
        raise Midi2MkcdError(
            "-p only applies to MIDI input: a song's instruments are already in it"
        )
    logger.info(f"Reading song from {path}")
    return decode_song_hex(parse_song_text(text).hex)


def _run_tune_replace(args: Namespace) -> int:
    fmt = None
    if args.audio is not None:
        fmt = _audio_format(args, args.audio, audio.DEFAULT_RATE)
    result = tuning.convert_replaced(
        args.input,
        _read_params(args.params, allow_incomplete=args.allow_incomplete),
        melodics=args.melodics,
        drums=args.drums,
    )
    if fmt is not None:
        return _write_audio(audio.song_events(result.song), args.audio, fmt)
    return _emit(result.to_typescript(args.format or "code"), args.output, args.copy)


def _run_tune_sample(args: Namespace) -> int:
    params = _read_params(args.params, allow_incomplete=args.allow_incomplete)
    if args.audio is None:
        if args.melodics is not None:
            text = tuning.sample_melodics(args.input, args.melodics, params)
        else:
            text = tuning.sample_drums(args.input, args.drums, params)
        return _emit(text, args.output, args.copy)

    fmt = _audio_format(args, args.audio, audio.DEFAULT_RATE)
    if args.melodics is not None:
        samples = tuning.melodic_samples(args.input, args.melodics, params)
    else:
        samples = tuning.drum_samples(args.input, args.drums, params)
    events, starts = audio.songs_events(result.song for _, result in samples)
    for (number, _), start_ms in zip(samples, starts, strict=True):
        name = (
            f"program {number} ({melodic_instrument_name(number)})"
            if args.melodics is not None
            else f"drum note {number} ({drum_note_name(number)})"
        )
        logger.info(f"{_minutes_seconds(start_ms)} {name}")
    return _write_audio(events, args.audio, fmt)


def _minutes_seconds(ms: float) -> str:
    """E.g. 72500 -> "1:12" (truncated to the second)."""
    seconds = int(ms // 1000)
    return f"{seconds // 60}:{seconds % 60:02d}"


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


def _run_params_soundfont(args: Namespace) -> int:
    sf3 = args.output.suffix.lower() == ".sf3"
    if not sf3 and args.quality is not None:
        logger.debug("--quality is ignored for SF2")
    params = _read_params(args.params, allow_incomplete=args.allow_incomplete)
    options = soundfont.SoundFontOptions(
        notes=args.notes,
        step=args.step,
        rate=args.rate,
        velocity_layers=args.velocity_layers,
        volume=args.volume,
        sf3=sf3,
        quality=args.quality if sf3 else None,
    )
    source = args.params.name if args.params is not None else "bundled params"
    with _ProgressBar("Rendering", "sample") as progress:
        data = soundfont.build_soundfont(
            params,
            options,
            comment=f"Made by midi2mkcd {__version__} from {source}",
            progress=progress,
        )
    return _emit_bytes(data, args.output)


def _read_params(path: Path | None, *, allow_incomplete: bool = False) -> ParamsInput:
    """Read a params file as text (or load it leniently); None means bundled."""
    if path is None:
        return None
    logger.info(f"Reading instrument params from {path}")
    text = path.read_text(encoding="utf-8")
    if allow_incomplete:
        return load_instrument_params(text, allow_incomplete=True)
    return text


def _read_song_text(path: Path) -> str:
    """Read a song file as UTF-8 text."""
    try:
        return path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise InvalidSongError(f"{path} is not a UTF-8 text file: {e}") from e


def _emit_bytes(data: bytes, output: Path) -> int:
    """Write binary output to a file (never stdout)."""
    output.write_bytes(data)
    logger.info(f"Wrote {output}")
    return 0


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
