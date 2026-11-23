# https://github.com/microsoft/pxt/blob/master/pxtsim/sound/song.ts
# Ported from microsoft/pxt @ 19bd210 (2026-11-19): renderInstrument,
# renderDrumInstrument and the helpers they call. decodeSong and friends in
# that file duplicate pxtlib/music.ts, which arcade/music.py already ports.
#
# This is the simulator's note -> "play instructions" step. Its output must be
# byte-identical to song.ts, so the JavaScript number semantics are reproduced
# on purpose (see the _js_* helpers): `x | 0` is ToInt32 (truncate toward zero,
# NaN and +-Infinity become 0, wrap modulo 2**32), `>>` converts to int32
# first, Math.max/Math.min return NaN if any argument is NaN, and division by
# zero gives +-Infinity or NaN instead of raising.
#
# An instruction is 12 bytes, little-endian 16-bit fields:
#     0 waveform | 1 flags (0) | 2 start Hz | 4 duration ms | 6 start volume
#     | 8 end volume | 10 end Hz
# Each is one linear ramp. A waveform-0 instruction is a silent rest of its
# duration. Like song.ts, the returned buffer keeps its unused zero-filled
# instruction slots at the end.
from __future__ import annotations

import logging
import math

from midi2mkcd.arcade.music_types import LFO, DrumInstrument, Envelope, Instrument

logger = logging.getLogger(__name__)

BUFFER_SIZE = 12


def _js_to_int32(value: float) -> int:
    """`value | 0` in JavaScript (ToInt32)."""
    if math.isnan(value) or math.isinf(value):
        return 0
    n = int(value) & 0xFFFFFFFF  # int() truncates toward zero
    return n - 0x1_0000_0000 if n >= 0x8000_0000 else n


def _js_max(*values: float) -> float:
    """`Math.max`: NaN if any argument is NaN."""
    if any(math.isnan(v) for v in values):
        return math.nan
    return max(values)


def _js_min(*values: float) -> float:
    """`Math.min`: NaN if any argument is NaN."""
    if any(math.isnan(v) for v in values):
        return math.nan
    return min(values)


def _js_div(a: float, b: float) -> float:
    """`a / b` with IEEE 754 results for a zero divisor instead of raising."""
    if b == 0:
        if a == 0 or math.isnan(a):
            return math.nan
        return math.copysign(math.inf, a) * math.copysign(1.0, b)
    return a / b


def _truthy(value: float) -> bool:
    """JavaScript truthiness of a number (0 and NaN are falsy)."""
    return value != 0 and not math.isnan(value)


def _lfo_on(lfo: LFO | None) -> bool:
    """`instrument.someLFO?.amplitude` used as a condition."""
    return lfo is not None and _truthy(lfo.amplitude)


def _envelope_on(envelope: Envelope | None) -> bool:
    """`instrument.someEnvelope?.amplitude` used as a condition."""
    return envelope is not None and _truthy(envelope.amplitude)


def render_instrument(
    instrument: Instrument, note_frequency: float, gate_length: float, volume: float
) -> bytes:
    """Port of `renderInstrument`. `note_frequency` may be NaN (an index
    outside the sequencer's frequency table), which ends up as 0 Hz."""
    amp_env = instrument.amp_envelope
    pitch_env = instrument.pitch_envelope
    amp_lfo = instrument.amp_lfo
    pitch_lfo = instrument.pitch_lfo

    total_duration = gate_length + amp_env.release

    amp_lfo_interval = (
        _js_max(_js_div(500, amp_lfo.frequency), 50)
        if amp_lfo is not None and _lfo_on(amp_lfo)
        else 50
    )
    pitch_lfo_interval = (
        _js_max(_js_div(500, pitch_lfo.frequency), 50)
        if pitch_lfo is not None and _lfo_on(pitch_lfo)
        else 50
    )

    time_points: list[float] = [0]

    next_ae_time: float = amp_env.attack
    next_pe_time: float = (
        pitch_env.attack
        if pitch_env is not None and _envelope_on(pitch_env)
        else total_duration
    )
    next_pl_time: float = pitch_lfo_interval if _lfo_on(pitch_lfo) else total_duration
    next_al_time: float = amp_lfo_interval if _lfo_on(amp_lfo) else total_duration

    def next_amp_env_time(time: float) -> float:
        if (
            time < amp_env.attack + amp_env.decay
            and amp_env.attack + amp_env.decay < gate_length
        ):
            return amp_env.attack + amp_env.decay
        elif time < gate_length:
            return gate_length
        else:
            return total_duration

    def next_pitch_env_time(time: float) -> float:
        # Only reached while the pitch envelope is on (otherwise next_pe_time
        # stays at total_duration, which is never < total_duration or <= time).
        assert pitch_env is not None
        if (
            time < pitch_env.attack + pitch_env.decay
            and pitch_env.attack + pitch_env.decay < gate_length
        ):
            return pitch_env.attack + pitch_env.decay
        elif time < gate_length:
            return gate_length
        elif time < gate_length + pitch_env.release:
            return _js_min(total_duration, gate_length + pitch_env.release)
        else:
            return total_duration

    time: float = 0
    while time < total_duration:
        if (
            next_ae_time <= next_pe_time
            and next_ae_time <= next_pl_time
            and next_ae_time <= next_al_time
        ):
            time = next_ae_time
            time_points.append(next_ae_time)
            next_ae_time = next_amp_env_time(time)
        elif (
            next_pe_time <= next_pl_time
            and next_pe_time <= next_al_time
            and next_pe_time < total_duration
        ):
            time = next_pe_time
            time_points.append(next_pe_time)
            next_pe_time = next_pitch_env_time(time)
        elif next_pl_time <= next_al_time and next_pl_time < total_duration:
            time = next_pl_time
            time_points.append(next_pl_time)
            next_pl_time += pitch_lfo_interval
        elif next_al_time < total_duration:
            time = next_al_time
            time_points.append(next_al_time)
            next_al_time += amp_lfo_interval
        elif time < gate_length:
            time = gate_length
            time_points.append(gate_length)
        else:
            time = total_duration
            time_points.append(total_duration)

        if time >= total_duration:
            break

        if next_ae_time <= time:
            next_ae_time = next_amp_env_time(time)
        if next_pe_time <= time:
            next_pe_time = next_pitch_env_time(time)
        while next_al_time <= time:
            next_al_time += amp_lfo_interval
        while next_pl_time <= time:
            next_pl_time += pitch_lfo_interval

    prev_amp = _js_to_int32(
        _instrument_volume_at_time(instrument, gate_length, 0, volume)
    )
    prev_pitch = _js_to_int32(
        _instrument_pitch_at_time(instrument, note_frequency, gate_length, 0)
    )
    prev_time: float = 0

    out = bytearray(BUFFER_SIZE * (len(time_points) + 1))
    ptr = 0
    for point in time_points[1:]:
        if point - prev_time < 5:
            continue

        next_amp = _js_to_int32(
            _instrument_volume_at_time(instrument, gate_length, point, volume)
        )
        next_pitch = _js_to_int32(
            _instrument_pitch_at_time(instrument, note_frequency, gate_length, point)
        )
        ptr = _add_note(
            out,
            ptr,
            _js_to_int32(point - prev_time),
            prev_amp,
            next_amp,
            instrument.waveform,
            prev_pitch,
            next_pitch,
        )

        prev_amp = next_amp
        prev_pitch = next_pitch
        prev_time = point

    # if the amp is still open, roll it off to prevent clipping
    if prev_amp:
        ptr = _add_note(
            out, ptr, 10, prev_amp, 0, instrument.waveform, prev_pitch, prev_pitch
        )
    return bytes(out)


def render_drum_instrument(sound: DrumInstrument, volume: float) -> bytes:
    """Port of `renderDrumInstrument`.

    song.ts throws a TypeError for a drum with no steps (it reads the last
    step's waveform). The sequencer plays notes in an async function, so in
    the simulator that drum is silent. Here it raises ``ValueError``; the
    sequencer port skips such drums."""
    if not sound.steps:
        raise ValueError("drum instrument has no steps (silent in the simulator)")

    prev_amp: float = sound.start_volume
    prev_freq: float = sound.start_frequency

    def scale_volume(value: float) -> float:
        return (value / 1024) * volume

    out = bytearray((len(sound.steps) + 1) * BUFFER_SIZE)

    # Each step goes in its own slot, whatever _add_note returns, so a step
    # with duration 0 leaves a zero-filled slot (as in song.ts).
    for i, step in enumerate(sound.steps):
        _add_note(
            out,
            i * BUFFER_SIZE,
            step.duration,
            scale_volume(prev_amp),
            scale_volume(step.volume),
            step.waveform,
            prev_freq,
            step.frequency,
        )
        prev_amp = step.volume
        prev_freq = step.frequency

    _add_note(
        out,
        len(sound.steps) * BUFFER_SIZE,
        10,
        scale_volume(prev_amp),
        0,
        sound.steps[-1].waveform,
        prev_freq,
        prev_freq,
    )

    return bytes(out)


def _instrument_pitch_at_time(
    instrument: Instrument, note_frequency: float, gate_length: float, time: float
) -> float:
    mod: float = 0
    if instrument.pitch_envelope is not None and _envelope_on(
        instrument.pitch_envelope
    ):
        mod += _envelope_value_at_time(instrument.pitch_envelope, time, gate_length)
    if instrument.pitch_lfo is not None and _lfo_on(instrument.pitch_lfo):
        mod += _lfo_value_at_time(instrument.pitch_lfo, time)
    return _js_max(note_frequency + mod, 0)


def _instrument_volume_at_time(
    instrument: Instrument, gate_length: float, time: float, max_volume: float
) -> int:
    mod: float = 0
    if _truthy(instrument.amp_envelope.amplitude):
        mod += _envelope_value_at_time(instrument.amp_envelope, time, gate_length)
    if instrument.amp_lfo is not None and _lfo_on(instrument.amp_lfo):
        mod += _lfo_value_at_time(instrument.amp_lfo, time)
    clamped = _js_max(_js_min(mod, instrument.amp_envelope.amplitude), 0)
    return _js_to_int32((clamped / 1024) * max_volume)


def _envelope_value_at_time(
    envelope: Envelope, time: float, gate_length: float
) -> float:
    # The divisions below can't divide by zero: each branch is only reached
    # when its divisor is positive. _js_div keeps that safe anyway.
    if time > gate_length:
        release_start_level = _envelope_value_at_time(
            envelope, gate_length, gate_length
        )

        if time - gate_length > envelope.release:
            return 0
        else:
            return release_start_level - _js_div(
                release_start_level, envelope.release
            ) * (time - gate_length)
    elif time < envelope.attack:
        return _js_div(envelope.amplitude, envelope.attack) * time
    elif time < envelope.attack + envelope.decay:
        return envelope.amplitude - _js_div(
            envelope.amplitude - ((envelope.sustain / 1024) * envelope.amplitude),
            envelope.decay,
        ) * (time - envelope.attack)
    else:
        return (envelope.sustain / 1024) * envelope.amplitude


def _lfo_value_at_time(lfo: LFO, time: float) -> float:
    # Same operation order as song.ts: ((t / 1000) * f) * 2 * PI
    return math.cos(((time / 1000) * lfo.frequency) * 2 * math.pi) * lfo.amplitude


def _add_note(
    snd_instr: bytearray,
    snd_instr_ptr: int,
    ms: float,
    beg: float,
    end: float,
    sound_wave: int,
    hz: float,
    end_hz: float,
) -> int:
    if ms > 0:
        _set_byte(snd_instr, snd_instr_ptr, sound_wave)
        _set_byte(snd_instr, snd_instr_ptr + 1, 0)
        _set_16_bit_number(snd_instr, snd_instr_ptr + 2, hz)
        _set_16_bit_number(snd_instr, snd_instr_ptr + 4, ms)
        _set_16_bit_number(snd_instr, snd_instr_ptr + 6, _js_to_int32(beg * 255) >> 6)
        _set_16_bit_number(snd_instr, snd_instr_ptr + 8, _js_to_int32(end * 255) >> 6)
        _set_16_bit_number(snd_instr, snd_instr_ptr + 10, end_hz)
        snd_instr_ptr += BUFFER_SIZE
    _set_byte(snd_instr, snd_instr_ptr, 0)
    return snd_instr_ptr


def _set_byte(buf: bytearray, offset: int, value: int) -> None:
    """`Uint8Array[offset] = value`: wraps modulo 256 and silently ignores an
    out-of-range offset (renderDrumInstrument writes one byte past its end)."""
    if 0 <= offset < len(buf):
        buf[offset] = value & 0xFF


def _set_16_bit_number(buf: bytearray, offset: int, value: float) -> None:
    """`set16BitNumber`: `Uint16Array[0] = value | 0`, little-endian."""
    v = _js_to_int32(value) & 0xFFFF
    _set_byte(buf, offset, v & 0xFF)
    _set_byte(buf, offset + 1, v >> 8)
