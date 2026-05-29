import logging
from dataclasses import dataclass
from enum import IntEnum
from typing import Dict

from yaml import safe_load

from arcade.music_types import DrumInstrument, DrumSoundStep, Envelope, Instrument, LFO
from utils.logger import create_logger

logger = create_logger(name=__name__, level=logging.INFO)


@dataclass
class InstrumentParameterMapping:
    # The integer is the general MIDI instrument
    melodic_instruments: Dict[int, Instrument]
    # The integer is the MIDI drum kit note
    drum_instruments: Dict[int, DrumInstrument]


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
    """
    w = w.lower()
    if w == "square":
        w = "square_50"
    if w == "noise_tunable_2":
        w = "cycle_64"
    return {
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
    }[w]


def load_instrument_params(yaml_text: str) -> InstrumentParameterMapping:
    """
    Take a YAML file specifying instrument data and return an instrument parameter
    mapping. For melodic instruments, the octave has been set to 0, duplicate as
    necessary for the full MIDI range (use octave offset 2 and 7 for full range)

    :param yaml_text: String holding the YAML file's text.
    :return: An `InstrumentParameterMapping` object.
    """
    logger.debug(f"Loading instrument parameters from {len(yaml_text)} characters of "
                 f"YAML text")
    data = safe_load(yaml_text)

    mapping = InstrumentParameterMapping(melodic_instruments={}, drum_instruments={})

    logger.debug(f"Creating mappings for {len(data["melodic_instruments"])} melodic "
                 f"instruments")
    for instr in data["melodic_instruments"]:
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
            )
        )

    logger.debug(f"Creating mappings for {len(data["drum_instruments"])} drum "
                 f"instruments")
    for sample in data["drum_instruments"]:
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
                ) for step in sample["steps"]
            ]
        )

    return mapping
