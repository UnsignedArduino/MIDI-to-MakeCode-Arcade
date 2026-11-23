"""Tests for the port of pxtsim/sound/song.ts.

The expected buffers here are worked out by hand from song.ts. The byte-exact
reference is the golden file from the Node script under tools/ (to come).
"""

import math
import struct

import pytest

from midi2mkcd.arcade.music_types import (
    LFO,
    DrumInstrument,
    DrumSoundStep,
    Envelope,
    Instrument,
)
from midi2mkcd.arcade.sim_song import (
    BUFFER_SIZE,
    _js_div,
    _js_max,
    _js_min,
    _js_to_int32,
    render_drum_instrument,
    render_instrument,
)


def instr(waveform: int, hz: int, ms: int, beg: int, end: int, end_hz: int) -> bytes:
    """One instruction as song.ts's addNote writes it (volumes already scaled)."""
    return struct.pack("<BBHHHHH", waveform, 0, hz, ms, beg, end, end_hz)


def flat_instrument(**amp: int) -> Instrument:
    envelope = {
        "attack": 0,
        "decay": 0,
        "sustain": 1024,
        "release": 0,
        "amplitude": 1024,
    }
    envelope.update(amp)
    return Instrument(waveform=3, octave=4, amp_envelope=Envelope(**envelope))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.0, 0),
        (1.9, 1),
        (-1.9, -1),
        (math.nan, 0),
        (math.inf, 0),
        (-math.inf, 0),
        (2.0**31, -(2**31)),
        (2.0**32 + 5, 5),
        (-(2.0**31) - 1, 2**31 - 1),
    ],
)
def test_js_to_int32(value: float, expected: int) -> None:
    assert _js_to_int32(value) == expected


def test_js_math_helpers() -> None:
    assert math.isnan(_js_max(math.nan, 0))
    assert math.isnan(_js_max(0, math.nan))
    assert math.isnan(_js_min(5, math.nan))
    assert _js_div(500, 0) == math.inf
    assert _js_div(-1, 0) == -math.inf
    assert math.isnan(_js_div(0, 0))


def test_flat_note() -> None:
    out = render_instrument(flat_instrument(), 262, 100, 1024)
    # time points [0, 0, 100]: the 0 -> 0 step is under 5 ms and skipped
    assert len(out) == BUFFER_SIZE * 4
    assert out == (
        instr(3, 262, 100, 4080, 4080, 262)
        + instr(3, 262, 10, 4080, 0, 262)  # roll-off, the amp is still open
        + bytes(2 * BUFFER_SIZE)
    )


def test_gate_shorter_than_attack() -> None:
    # Attack 200 ms, gate 21 ms: the next point after 0 is the end of the
    # attack (200), where the release (from the gate's level, 1024 * 21/200)
    # has already brought the level down to 107.52 * 21/200 = 11.29.
    instrument = flat_instrument(attack=200, release=200)
    out = render_instrument(instrument, 262, 21, 1024)
    assert out == (
        instr(3, 262, 200, 0, (11 * 255) >> 6, 262)
        + instr(3, 262, 21, (11 * 255) >> 6, 0, 262)
        + bytes(2 * BUFFER_SIZE)  # no roll-off: the amp ended at 0
    )


def test_volume_is_truncated_before_scaling() -> None:
    out = render_instrument(flat_instrument(), 262, 100, 127)
    assert out[:BUFFER_SIZE] == instr(
        3, 262, 100, (127 * 255) >> 6, (127 * 255) >> 6, 262
    )


def test_out_of_table_frequency_is_0_hz() -> None:
    out = render_instrument(flat_instrument(), math.nan, 100, 1024)
    assert out[:BUFFER_SIZE] == instr(3, 0, 100, 4080, 4080, 0)


def test_lfo_points_every_50_ms_at_least() -> None:
    instrument = flat_instrument()
    instrument.amp_lfo = LFO(frequency=20, amplitude=100)  # 500/20 = 25 -> 50 ms
    out = render_instrument(instrument, 262, 200, 1024)
    durations = [
        struct.unpack_from("<H", out, i + 4)[0] for i in range(0, len(out), BUFFER_SIZE)
    ]
    # cos(2 pi 20 t) is 1 at every 50 ms point, so the clamp keeps 1024
    assert durations[:5] == [50, 50, 50, 50, 10]
    assert struct.unpack_from("<H", out, 6)[0] == 4080


def test_lfo_frequency_0_does_not_raise() -> None:
    instrument = flat_instrument()
    instrument.pitch_lfo = LFO(frequency=0, amplitude=10)  # interval Infinity
    out = render_instrument(instrument, 262, 100, 1024)
    # cos(0) * 10 is a constant +10 Hz
    assert out[:BUFFER_SIZE] == instr(3, 272, 100, 4080, 4080, 272)


def test_drum() -> None:
    drum = DrumInstrument(
        start_frequency=100,
        start_volume=1024,
        steps=[DrumSoundStep(waveform=5, frequency=200, volume=512, duration=30)],
    )
    out = render_drum_instrument(drum, 128)
    assert out == (
        instr(5, 100, 30, (128 * 255) >> 6, (64 * 255) >> 6, 200)
        + instr(5, 200, 10, (64 * 255) >> 6, 0, 200)
    )


def test_drum_zero_duration_step_leaves_an_empty_slot() -> None:
    drum = DrumInstrument(
        start_frequency=100,
        start_volume=1024,
        steps=[
            DrumSoundStep(waveform=1, frequency=150, volume=1024, duration=0),
            DrumSoundStep(waveform=5, frequency=200, volume=0, duration=20),
        ],
    )
    out = render_drum_instrument(drum, 1024)
    assert out == (
        bytes(BUFFER_SIZE)
        + instr(5, 150, 20, 4080, 0, 200)
        + instr(5, 200, 10, 0, 0, 200)
    )


def test_drum_without_steps_raises() -> None:
    with pytest.raises(ValueError, match="no steps"):
        render_drum_instrument(DrumInstrument(100, 1024, []), 1024)
