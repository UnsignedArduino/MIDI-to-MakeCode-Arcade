"""
Developer tools for writing and auditioning instrument parameter files.

Not re-exported from the package root: import it as `from midi2mkcd import tuning`.
These tools may change between versions.
"""

import logging
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Literal

from mido import MidiFile
from yaml import YAMLError, safe_load

from midi2mkcd import render
from midi2mkcd.api import (
    ConversionResult,
    MidiInput,
    ParamsInput,
    _convert,
    _load_midi,
    _resolve_params,
)
from midi2mkcd.arcade.music_types import Envelope
from midi2mkcd.errors import InstrumentParamsError
from midi2mkcd.midi_to_song.instruments import (
    InstrumentParameterMapping,
    load_instrument_params_with_problems,
)
from midi2mkcd.midi_to_song.timeline.parser import (
    timeline_build,
    timeline_find_instrument_data,
    timeline_group_messages,
)
from midi2mkcd.utils.resources import blank_instrument_params_text
from midi2mkcd.utils.strings import format_ranges

__all__ = [
    "InstrumentUsage",
    "blank_instrument_params_text",
    "check_params",
    "convert_replaced",
    "drum_samples",
    "melodic_samples",
    "report",
    "sample_drums",
    "sample_melodics",
]

logger = logging.getLogger(__name__)

# GM1 melodic programs and the drum notes the bundled params cover (GM1 + GM2 range)
MELODIC_RANGE = range(128)
DRUM_RANGE = range(27, 88)

# MakeCode Arcade stores these as 16-bit unsigned (8-bit for LFO frequency) values;
# amplitudes and volumes are documented as 0-1024 in instrument_params.yaml
_U16_MAX = 0xFFFF
_U8_MAX = 0xFF
_VOLUME_MAX = 1024


def convert_replaced(
    midi: MidiInput,
    params: ParamsInput = None,
    *,
    melodics: int | None = None,
    drums: int | None = None,
) -> ConversionResult:
    """
    Convert a song with every melodic instrument and/or drum note replaced, to hear
    one instrument's parameters across a whole song. Replacement happens before
    notes are grouped into tracks, so all melodic tracks merge into one instrument.

    :param midi: See `midi2mkcd.convert`.
    :param params: See `midi2mkcd.convert`.
    :param melodics: Play every melodic note with this MIDI program.
    :param drums: Replace every drum note with this MIDI drum note.
    :return: A `ConversionResult` whose comments say what was replaced.
    """
    return _convert_replaced(
        _load_midi(midi), _resolve_params(params), melodics=melodics, drums=drums
    )


def _convert_replaced(
    midi_file: MidiFile,
    mapping: InstrumentParameterMapping,
    *,
    melodics: int | None = None,
    drums: int | None = None,
) -> ConversionResult:
    comments = []
    if melodics is not None:
        logger.info(
            f"Replacing all melodic instruments with MIDI instrument {melodics}"
        )
        comments.append(f"melodics replaced with MIDI instrument {melodics}")
    if drums is not None:
        logger.info(f"Replacing all drum notes with MIDI drum note {drums}")
        comments.append(f"drums replaced with MIDI drum note {drums}")
    result = _convert(
        midi_file, mapping, replace_melodics=melodics, replace_drums=drums
    )
    return replace(result, comments=tuple(comments))


def sample_melodics(
    midi: MidiInput, instruments: Iterable[int], params: ParamsInput = None
) -> str:
    """
    Build one MakeCode Arcade program that plays the song once per melodic
    instrument, with every melodic track replaced by that instrument. The score
    shows which instrument is playing.

    :param midi: See `midi2mkcd.convert`.
    :param instruments: The MIDI programs to sample, in play order.
    :param params: See `midi2mkcd.convert`.
    :return: The generated TypeScript.
    """
    samples = melodic_samples(midi, instruments, params)
    return render.sampler("melodic instrument sample", "instruments", samples)


def melodic_samples(
    midi: MidiInput, instruments: Iterable[int], params: ParamsInput = None
) -> list[tuple[int, ConversionResult]]:
    """
    The conversions `sample_melodics` plays: the song with every melodic track
    replaced by each instrument in turn, e.g. to render them with `audio`.

    :param midi: See `midi2mkcd.convert`.
    :param instruments: The MIDI programs to sample, in play order.
    :param params: See `midi2mkcd.convert`.
    :return: (MIDI program, conversion result) pairs, in play order.
    """
    midi_file = _load_midi(midi)
    mapping = _resolve_params(params)
    instruments = list(instruments)
    logger.info(f"Generating melodic instrument sample of {instruments}")
    return [(n, _convert_replaced(midi_file, mapping, melodics=n)) for n in instruments]


def sample_drums(
    midi: MidiInput, notes: Iterable[int], params: ParamsInput = None
) -> str:
    """
    Build one MakeCode Arcade program that plays the song once per drum note, with
    every drum note replaced by that note. The score shows which note is playing.

    :param midi: See `midi2mkcd.convert`.
    :param notes: The MIDI drum notes to sample, in play order.
    :param params: See `midi2mkcd.convert`.
    :return: The generated TypeScript.
    """
    samples = drum_samples(midi, notes, params)
    return render.sampler("drum sample", "drum notes", samples)


def drum_samples(
    midi: MidiInput, notes: Iterable[int], params: ParamsInput = None
) -> list[tuple[int, ConversionResult]]:
    """
    The conversions `sample_drums` plays: the song with every drum note replaced
    by each note in turn, e.g. to render them with `audio`.

    :param midi: See `midi2mkcd.convert`.
    :param notes: The MIDI drum notes to sample, in play order.
    :param params: See `midi2mkcd.convert`.
    :return: (MIDI drum note, conversion result) pairs, in play order.
    """
    midi_file = _load_midi(midi)
    mapping = _resolve_params(params)
    notes = list(notes)
    logger.info(f"Generating drum sample of {notes}")
    return [(n, _convert_replaced(midi_file, mapping, drums=n)) for n in notes]


@dataclass(frozen=True)
class InstrumentUsage:
    """How a song uses one melodic instrument or drum note."""

    kind: Literal["melodic", "drum"]
    number: int  # MIDI program (melodic) or MIDI drum note (drum)
    note_count: int
    lowest_note: int  # MIDI note numbers, before any pitch shift
    highest_note: int
    min_velocity: int
    max_velocity: int
    mapped: bool  # the params have an entry for it


def report(midi: MidiInput, params: ParamsInput = None) -> list[InstrumentUsage]:
    """
    List every melodic instrument and drum note a song uses, to see what needs
    tuning and in what range. Only parses the song, so it never fails because of
    instruments the params don't cover.

    :param midi: See `midi2mkcd.convert`.
    :param params: Checked for coverage only. See `midi2mkcd.convert`.
    :return: Melodic instruments by program, then drum notes by note.
    :raises InvalidMidiError: If the MIDI data can't be parsed.
    """
    midi_file = _load_midi(midi)
    mapping = _resolve_params(params)
    notes = timeline_group_messages(
        timeline_find_instrument_data(timeline_build(midi_file))
    )

    groups: dict[tuple[Literal["melodic", "drum"], int], list[tuple[int, int]]] = {}
    for n in notes:
        key: tuple[Literal["melodic", "drum"], int] = (
            ("drum", n.note) if n.is_drum else ("melodic", n.instrument)
        )
        groups.setdefault(key, []).append((n.note, n.velocity))

    usages = []
    for (kind, number), played in groups.items():
        pitches = [p for p, _ in played]
        velocities = [v for _, v in played]
        covered = (
            mapping.drum_instruments if kind == "drum" else mapping.melodic_instruments
        )
        usages.append(
            InstrumentUsage(
                kind=kind,
                number=number,
                note_count=len(played),
                lowest_note=min(pitches),
                highest_note=max(pitches),
                min_velocity=min(velocities),
                max_velocity=max(velocities),
                mapped=number in covered,
            )
        )
    usages.sort(key=lambda u: (u.kind == "drum", u.number))
    return usages


def check_params(params: str | InstrumentParameterMapping) -> list[str]:
    """
    Lint instrument parameters without playing anything.

    Given YAML text, invalid entries are reported instead of raised (so half-written
    files can be checked) and duplicate entries are found. Given a loaded mapping,
    the duplicate check is skipped, since loading keeps only the last duplicate.

    :param params: YAML text (not a path) or a loaded mapping.
    :return: Human-readable problems; empty if none were found.
    :raises InstrumentParamsError: If YAML text can't be parsed or its top-level
     structure is invalid.
    """
    problems: list[str] = []
    if isinstance(params, str):
        mapping, skipped = load_instrument_params_with_problems(
            params, allow_incomplete=True
        )
        problems.extend(skipped)
        problems.extend(_duplicate_problems(params))
    else:
        mapping = params

    for program, instrument in sorted(mapping.melodic_instruments.items()):
        where = f"Melodic instrument {program}"
        problems.extend(
            _envelope_problems(f"{where} amp_envelope", instrument.amp_envelope, True)
        )
        if instrument.pitch_envelope is not None:
            problems.extend(
                _envelope_problems(
                    f"{where} pitch_envelope", instrument.pitch_envelope, False
                )
            )
        for name, lfo, amplitude_max in (
            ("amp_lfo", instrument.amp_lfo, _VOLUME_MAX),
            ("pitch_lfo", instrument.pitch_lfo, _U16_MAX),
        ):
            if lfo is None:
                continue
            problems.extend(
                _range_problem(f"{where} {name} frequency", lfo.frequency, 0, _U8_MAX)
            )
            problems.extend(
                _range_problem(
                    f"{where} {name} amplitude", lfo.amplitude, 0, amplitude_max
                )
            )

    for note, drum in sorted(mapping.drum_instruments.items()):
        where = f"Drum note {note}"
        problems.extend(
            _range_problem(f"{where} start_freq", drum.start_frequency, 0, _U16_MAX)
        )
        problems.extend(
            _range_problem(f"{where} start_vol", drum.start_volume, 0, _VOLUME_MAX)
        )
        if not drum.steps:
            problems.append(f"{where} has no steps")
        if len(drum.steps) > _U8_MAX:
            problems.append(f"{where} has {len(drum.steps)} steps (max {_U8_MAX})")
        for i, step in enumerate(drum.steps):
            at = f"{where} step {i}"
            problems.extend(
                _range_problem(f"{at} target_freq", step.frequency, 0, _U16_MAX)
            )
            problems.extend(
                _range_problem(f"{at} target_vol", step.volume, 0, _VOLUME_MAX)
            )
            problems.extend(
                _range_problem(f"{at} duration", step.duration, 1, _U16_MAX)
            )

    missing_melodics = [
        p for p in MELODIC_RANGE if p not in mapping.melodic_instruments
    ]
    if missing_melodics:
        problems.append(
            f"No entry for melodic instruments {format_ranges(missing_melodics)}"
        )
    missing_drums = [n for n in DRUM_RANGE if n not in mapping.drum_instruments]
    if missing_drums:
        problems.append(f"No entry for drum notes {format_ranges(missing_drums)}")

    return problems


def _range_problem(what: str, value: object, low: int, high: int) -> list[str]:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return [f"{what} is {value!r}, expected a number"]
    if not low <= value <= high:
        return [f"{what} is {value}, expected {low}-{high}"]
    return []


def _envelope_problems(what: str, envelope: Envelope, is_amp: bool) -> list[str]:
    problems = []
    for name in ("attack", "decay", "release"):
        problems.extend(
            _range_problem(f"{what} {name}", getattr(envelope, name), 0, _U16_MAX)
        )
    level_max = _VOLUME_MAX if is_amp else _U16_MAX
    problems.extend(_range_problem(f"{what} sustain", envelope.sustain, 0, level_max))
    problems.extend(
        _range_problem(f"{what} amplitude", envelope.amplitude, 0, level_max)
    )
    return problems


def _duplicate_problems(yaml_text: str) -> list[str]:
    try:
        data = safe_load(yaml_text)
    except YAMLError as e:  # the loader already rejected bad YAML; kept for safety
        raise InstrumentParamsError(f"Invalid instrument params YAML: {e}") from e
    problems: list[str] = []
    for section, key, kind in (
        ("melodic_instruments", "instrument", "melodic instrument"),
        ("drum_instruments", "note", "drum note"),
    ):
        counts = Counter(
            entry[key]
            for entry in data[section]
            if isinstance(entry, dict) and key in entry
        )
        problems.extend(
            f"Duplicate {kind} {number} ({count} entries; the last one is used)"
            for number, count in sorted(counts.items(), key=lambda kv: str(kv[0]))
            if count > 1
        )
    return problems
