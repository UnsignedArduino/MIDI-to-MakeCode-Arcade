import logging
from dataclasses import dataclass
from enum import IntEnum

from yaml import YAMLError, safe_load

from midi2mkcd.arcade.music_types import (
    LFO,
    DrumInstrument,
    DrumSoundStep,
    Envelope,
    Instrument,
)
from midi2mkcd.errors import InstrumentParamsError
from midi2mkcd.midi_to_song.models import TestingOptionsForLoadInstrumentParams

logger = logging.getLogger(__name__)


@dataclass
class InstrumentParameterMapping:
    # Compensation factor to scale by. Adjusts velocity to ensure that lower instruments
    # get a velocity boost while higher instruments are attenuated, helpful if you
    # tuned all instrument amplitudes at the same pitch. Pitches above MIDI note 60 are
    # reduced, pitches below are boosted. Set to 0 to disable.
    melodic_pitch_comp_k: float
    # The integer is the general MIDI instrument
    melodic_instruments: dict[int, Instrument]
    # The integer is the MIDI drum kit note
    drum_instruments: dict[int, DrumInstrument]


class WaveForm(IntEnum):
    TRIANGLE = 1
    SAWTOOTH = 2
    SINE = 3
    NOISE_TUNABLE = 4
    NOISE = 5
    SQUARE_10 = 11
    SQUARE_20 = 12
    SQUARE_30 = 13
    SQUARE_40 = 14
    SQUARE_50 = 15  # aka square
    CYCLE_16 = 16
    CYCLE_32 = 17
    CYCLE_64 = 18  # aka noise_tunable_2


def waveform_from_str(w: str) -> WaveForm:
    """
    Takes a string and returns the correct waveform integer.

    :param w: A string like "triangle" or "sine".
    :return: A `WaveForm` enum value.
    :raises InstrumentParamsError: If the string isn't a known waveform name.
    """
    w = w.lower()
    if w == "square":
        w = "square_50"
    if w == "noise_tunable_2":
        w = "cycle_64"
    try:
        return _WAVEFORMS_BY_NAME[w]
    except KeyError:
        raise InstrumentParamsError(f"Unknown waveform: {w!r}") from None


def _entry_error(kind: str, key: str, entry: object, e: Exception) -> str:
    """
    Describe an invalid instrument entry from the YAML for an error message.

    :param kind: "melodic instrument" or "drum instrument".
    :param key: The YAML key that identifies the entry ("instrument" or "note").
    :param entry: The entry as parsed from the YAML.
    :param e: The exception raised while loading the entry.
    :return: A human-readable message.
    """
    ident = entry.get(key, "?") if isinstance(entry, dict) else "?"
    if isinstance(e, InstrumentParamsError):
        detail = str(e)
    elif isinstance(e, KeyError):
        detail = f"missing field {e}"
    else:
        detail = f"{type(e).__name__}: {e}"
    return f"Invalid {kind} entry ({key} {ident}): {detail}"


_WAVEFORMS_BY_NAME = {
    "triangle": WaveForm.TRIANGLE,
    "sawtooth": WaveForm.SAWTOOTH,
    "sine": WaveForm.SINE,
    "noise_tunable": WaveForm.NOISE_TUNABLE,
    "noise": WaveForm.NOISE,
    "square_10": WaveForm.SQUARE_10,
    "square_20": WaveForm.SQUARE_20,
    "square_30": WaveForm.SQUARE_30,
    "square_40": WaveForm.SQUARE_40,
    "square_50": WaveForm.SQUARE_50,
    "cycle_16": WaveForm.CYCLE_16,
    "cycle_32": WaveForm.CYCLE_32,
    "cycle_64": WaveForm.CYCLE_64,
}


def load_instrument_params(
    yaml_text: str, testing_opts: TestingOptionsForLoadInstrumentParams | None = None
) -> InstrumentParameterMapping:
    """
    Take a YAML file specifying instrument data and return an instrument parameter
    mapping. For melodic instruments, the octave has been set to 0, duplicate as
    necessary for the full MIDI range (use octave offset 2 and 7 for full range)

    :param yaml_text: String holding the YAML file's text.
    :param testing_opts: Extra options used for testing, passed from the CLI.
    :return: An `InstrumentParameterMapping` object.
    :raises InstrumentParamsError: If the YAML can't be parsed, or an entry is missing
     or has invalid fields (unless `testing_opts.force_load` skips bad entries).
    """
    if testing_opts is None:
        testing_opts = TestingOptionsForLoadInstrumentParams()
    logger.debug(
        f"Loading instrument parameters from {len(yaml_text)} characters of YAML text"
    )
    try:
        data = safe_load(yaml_text)
    except YAMLError as e:
        raise InstrumentParamsError(f"Invalid instrument params YAML: {e}") from e

    if not isinstance(data, dict):
        raise InstrumentParamsError(
            "Invalid instrument params: expected a mapping at the top level"
        )
    for section in ("melodic_instruments", "drum_instruments"):
        if not isinstance(data.get(section), list):
            raise InstrumentParamsError(
                f"Invalid instrument params: '{section}' must be a list"
            )

    melodic_pitch_comp_k = data.get("melodic_pitch_comp_k", 0)
    logger.debug(f"{melodic_pitch_comp_k=}")

    mapping = InstrumentParameterMapping(
        melodic_pitch_comp_k=melodic_pitch_comp_k,
        melodic_instruments={},
        drum_instruments={},
    )

    logger.debug(
        f"Creating mappings for {len(data['melodic_instruments'])} melodic instruments"
    )
    for instr in data["melodic_instruments"]:
        try:
            # TODO: If pitch envelope or LFOs aren't defined in the YAML don't error out
            mapping.melodic_instruments[instr["instrument"]] = Instrument(
                waveform=waveform_from_str(instr["waveform"]),
                # Each note can range from 0-63, so we'll have two tracks, each with the
                # same two instruments but at different octave offsets
                octave=0,
                amp_envelope=Envelope(
                    attack=instr["amp_envelope"]["attack"],
                    decay=instr["amp_envelope"]["decay"],
                    sustain=instr["amp_envelope"]["sustain"],
                    release=instr["amp_envelope"]["release"],
                    amplitude=instr["amp_envelope"]["amplitude"],
                ),
                pitch_envelope=Envelope(
                    attack=instr["pitch_envelope"]["attack"],
                    decay=instr["pitch_envelope"]["decay"],
                    sustain=instr["pitch_envelope"]["sustain"],
                    release=instr["pitch_envelope"]["release"],
                    amplitude=instr["pitch_envelope"]["amplitude"],
                ),
                amp_lfo=LFO(
                    frequency=instr["amp_lfo"]["frequency"],
                    amplitude=instr["amp_lfo"]["amplitude"],
                ),
                pitch_lfo=LFO(
                    frequency=instr["pitch_lfo"]["frequency"],
                    amplitude=instr["pitch_lfo"]["amplitude"],
                ),
            )
        except Exception as e:
            if not testing_opts.force_load:
                raise InstrumentParamsError(
                    _entry_error("melodic instrument", "instrument", instr, e)
                ) from e
            # else:
            #     logger.warning(f"Error loading melodic instrument "
            #                    f"{instr['instrument']}: {e}. Skipping.")

    logger.debug(
        f"Creating mappings for {len(data['drum_instruments'])} drum instruments"
    )
    for sample in data["drum_instruments"]:
        try:
            mapping.drum_instruments[sample["note"]] = DrumInstrument(
                name=sample["_comment"],
                start_frequency=sample["start_freq"],
                start_volume=sample["start_vol"],
                steps=[
                    DrumSoundStep(
                        waveform=waveform_from_str(step["waveform"]),
                        frequency=step["target_freq"],
                        volume=step["target_vol"],
                        duration=step["duration"],
                    )
                    for step in sample["steps"]
                ],
            )
        except Exception as e:
            if not testing_opts.force_load:
                raise InstrumentParamsError(
                    _entry_error("drum instrument", "note", sample, e)
                ) from e

    return mapping
