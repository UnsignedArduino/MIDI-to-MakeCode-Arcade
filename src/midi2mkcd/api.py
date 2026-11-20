"""
The public conversion API: turn MIDI data into a MakeCode Arcade song.

Everything public here is re-exported from the package root. Names starting with an
underscore are shared with `midi2mkcd.tuning` and are not part of the public API.
"""

import io
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Literal

from mido import MidiFile

from midi2mkcd import render
from midi2mkcd.arcade.music_types import Song
from midi2mkcd.errors import InvalidMidiError
from midi2mkcd.midi_to_song import convert_midi_to_song
from midi2mkcd.midi_to_song.instruments import (
    InstrumentParameterMapping,
    load_instrument_params,
)
from midi2mkcd.utils.resources import default_instrument_params_text

__all__ = [
    "SIMULATOR_NOTE_WARNING_THRESHOLD",
    "ConversionResult",
    "InstrumentParameterMapping",
    "MidiInput",
    "OutputFormat",
    "ParamsInput",
    "convert",
    "default_instrument_params_text",
    "load_instrument_params",
]

logger = logging.getLogger(__name__)

OutputFormat = Literal["hex", "play", "visualizer"]

# A path (`str` or path-like), raw bytes, or a binary file object
MidiInput = bytes | bytearray | memoryview | BinaryIO | os.PathLike[str] | str
# YAML text (not a path), an already-loaded mapping, or None for the bundled default
ParamsInput = str | InstrumentParameterMapping | None

# README: songs with 24k+ note events crashed the simulator on the first play, and
# ~15k played twice may also crash it.
SIMULATOR_NOTE_WARNING_THRESHOLD = 15_000


@dataclass(frozen=True)
class ConversionResult:
    """The converted song plus the extra data the output formats need."""

    song: Song
    # Index i is the MIDI drum note that drum index i plays in a drum track
    drum_idx_to_midi_drum: list[int]
    # Index i is the MIDI program of track i, with -1 for the standard drum kit
    track_idx_to_midi_instrument: list[int]
    # Starting tick of each lyric, parallel to `lyric_texts`
    lyric_ticks: list[int]
    lyric_texts: list[str]
    # Total note events (chords) across all tracks
    note_event_count: int
    # Human-readable warnings for a front end to display
    warnings: list[str]
    # Lines emitted as `// ...` comments in the generated code's header
    comments: tuple[str, ...] = ()

    def to_hex(self) -> str:
        """Return the encoded song as raw hex digits."""
        return render.song_hex(self)

    def to_typescript(self, format: OutputFormat = "hex") -> str:
        """
        Return the song as MakeCode Arcade TypeScript.

        :param format: `"hex"` for just the ``hex`...` `` literal, `"play"` for a
         statement that plays the song, or `"visualizer"` for the constants the
         Song-Visualizer project expects.
        :return: The generated code.
        """
        return render.to_typescript(self, format)


def convert(midi: MidiInput, params: ParamsInput = None) -> ConversionResult:
    """
    Convert a MIDI file into a MakeCode Arcade song.

    :param midi: The MIDI file as bytes, a binary file object, or a path.
    :param params: Instrument parameters as YAML text (not a path), an already-loaded
     mapping, or None for the bundled defaults.
    :return: A `ConversionResult`.
    :raises InvalidMidiError: If the MIDI data can't be parsed.
    :raises InstrumentParamsError: If the params are invalid, or the song uses an
     instrument or drum note that they don't cover.
    :raises SongConstraintError: If the song can't fit MakeCode Arcade's format.
    :raises OSError: If `midi` is a path that can't be read.
    """
    return _convert(_load_midi(midi), _resolve_params(params))


def _load_midi(midi: MidiInput) -> MidiFile:
    """
    Parse MIDI input into a `MidiFile`.

    :param midi: See `convert`.
    :return: The parsed MIDI file.
    :raises InvalidMidiError: If the data can't be parsed.
    :raises OSError: If `midi` is a path that can't be read.
    """
    if isinstance(midi, str | os.PathLike):
        path = Path(midi)
        logger.info(f"Reading MIDI file from {path}")
        data = path.read_bytes()
        source = f"MIDI file {path}"
    elif isinstance(midi, bytes | bytearray | memoryview):
        data = bytes(midi)
        source = "MIDI data"
    else:
        data = midi.read()
        source = "MIDI data"
    try:
        midi_file = MidiFile(file=io.BytesIO(data))
    except Exception as e:
        # mido raises many exception types for malformed data (OSError, EOFError,
        # KeyError, ValueError...)
        raise InvalidMidiError(f"Could not parse {source}: {e!r}") from e
    logger.debug(f"Found {len(midi_file.tracks)} tracks, length of {midi_file.length}s")
    return midi_file


def _resolve_params(params: ParamsInput) -> InstrumentParameterMapping:
    """
    Turn the `params` argument into a loaded mapping.

    :param params: See `convert`.
    :return: The instrument parameter mapping.
    :raises InstrumentParamsError: If YAML text is invalid.
    """
    if params is None:
        logger.debug("Using the bundled default instrument params")
        return load_instrument_params(default_instrument_params_text())
    if isinstance(params, str):
        return load_instrument_params(params)
    return params


def _convert(
    midi_file: MidiFile,
    mapping: InstrumentParameterMapping,
    *,
    replace_melodics: int | None = None,
    replace_drums: int | None = None,
) -> ConversionResult:
    """
    Convert an already-parsed MIDI file. Shared by `convert` and the tuning tools,
    which use the replacement arguments.

    :param midi_file: The parsed MIDI file. Not modified.
    :param mapping: The instrument parameter mapping. Not modified.
    :param replace_melodics: Play every melodic note with this MIDI program.
    :param replace_drums: Replace every drum note with this MIDI drum note.
    :return: A `ConversionResult` with no comments.
    """
    result = convert_midi_to_song(
        midi_file,
        mapping,
        replace_melodics=replace_melodics,
        replace_drums=replace_drums,
    )
    note_event_count = sum(len(track.notes) for track in result.song.tracks)
    warnings = []
    if note_event_count >= SIMULATOR_NOTE_WARNING_THRESHOLD:
        warning = (
            f"This song has {note_event_count} note events. Songs this long or "
            f"dense may crash the MakeCode Arcade simulator (it seems to leak memory "
            f"per note played); reloading the simulator page resets it."
        )
        logger.warning(warning)
        warnings.append(warning)
    return ConversionResult(
        song=result.song,
        drum_idx_to_midi_drum=result.drum_idx_to_midi_drum,
        track_idx_to_midi_instrument=result.track_idx_to_midi_instrument,
        lyric_ticks=result.lyric_ticks,
        lyric_texts=result.lyric_texts,
        note_event_count=note_event_count,
        warnings=warnings,
    )
