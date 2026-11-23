"""The simulator ports against output from the real pxt source.

tests/fixtures/sim_cases.json holds the inputs (make_sim_cases.py) and
tests/fixtures/golden/sim_instructions.json what pxtsim/sound/song.ts and
sequencer.ts produce for them (make_sim_goldens.mjs, run by hand with Node).
"""

import json
import math
from functools import cache
from typing import Any

import pytest
from helpers import SIM_CASES, SIM_GOLDEN

from midi2mkcd.arcade.music import decode_song
from midi2mkcd.arcade.music_types import (
    LFO,
    DrumInstrument,
    DrumSoundStep,
    Envelope,
    Instrument,
)
from midi2mkcd.arcade.sim_sequencer import FREQUENCIES, sequence_song
from midi2mkcd.arcade.sim_song import render_drum_instrument, render_instrument

PXT_COMMIT = "19bd2103fc7fae218b75975891c921d2b5b1a419"


@cache
def cases() -> dict[str, Any]:
    with SIM_CASES.open(encoding="utf-8") as f:
        return json.load(f)  # type: ignore[no-any-return]


@cache
def golden() -> dict[str, Any]:
    with SIM_GOLDEN.open(encoding="utf-8") as f:
        return json.load(f)  # type: ignore[no-any-return]


def ids(kind: str) -> list[str]:
    return [c["id"] for c in cases()[kind]]


def case(kind: str, case_id: str) -> dict[str, Any]:
    return next(c for c in cases()[kind] if c["id"] == case_id)


def instrument_from_json(j: dict[str, Any]) -> Instrument:
    def lfo(key: str) -> LFO | None:
        return LFO(**j[key]) if key in j else None

    return Instrument(
        waveform=j["waveform"],
        octave=j["octave"],
        amp_envelope=Envelope(**j["ampEnvelope"]),
        pitch_envelope=Envelope(**j["pitchEnvelope"]) if "pitchEnvelope" in j else None,
        amp_lfo=lfo("ampLFO"),
        pitch_lfo=lfo("pitchLFO"),
    )


def drum_from_json(j: dict[str, Any]) -> DrumInstrument:
    return DrumInstrument(
        start_frequency=j["startFrequency"],
        start_volume=j["startVolume"],
        steps=[DrumSoundStep(**s) for s in j["steps"]],
    )


def test_golden_matches_cases_and_commit() -> None:
    assert golden()["pxt_commit"] == PXT_COMMIT
    for kind in ("notes", "drums", "songs"):
        assert list(golden()[kind]) == ids(kind), (
            f"{kind}: sim_cases.json and the golden disagree; "
            "rerun make_sim_goldens.mjs"
        )


def test_frequency_table() -> None:
    assert list(FREQUENCIES) == golden()["frequencies"]


@pytest.mark.parametrize("case_id", ids("notes"))
def test_render_instrument(case_id: str) -> None:
    c = case("notes", case_id)
    frequency = math.nan if c["frequency"] is None else c["frequency"]
    out = render_instrument(
        instrument_from_json(c["instrument"]), frequency, c["gate"], c["volume"]
    )
    assert out.hex() == golden()["notes"][case_id]


@pytest.mark.parametrize("case_id", ids("drums"))
def test_render_drum_instrument(case_id: str) -> None:
    c = case("drums", case_id)
    out = render_drum_instrument(drum_from_json(c["drum"]), c["volume"])
    assert out.hex() == golden()["drums"][case_id]


@pytest.mark.parametrize("case_id", ids("songs"))
def test_sequence_song(case_id: str) -> None:
    c = case("songs", case_id)
    song = decode_song(bytearray.fromhex(c["hex"]))
    played = [[s.tick, s.instructions.hex()] for s in sequence_song(song, c["volume"])]
    assert played == golden()["songs"][case_id]["played"]
