"""Audio rendering: an Arcade song played the way the MakeCode simulator plays it.

Stage 1 (this module so far) turns notes and songs into the simulator's
12-byte "play instructions", byte-identical to pxt. It is plain Python over
the line-by-line ports in `arcade/sim_song.py` and `arcade/sim_sequencer.py`,
and needs no optional dependencies.

Stage 2 (`synthesize`, `render_song`, `to_wav_bytes`) turns instructions into
samples the way `pxtsim/sound/playInstructions.ts` does with Web Audio in
Chrome, and needs numpy (the `audio` extra). numpy is only ever imported inside
those functions, so this module stays importable without it.

Not re-exported from the package root.
"""

from __future__ import annotations

import functools
import io
import logging
import math
import struct
import wave as wav_file
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from midi2mkcd.arcade.music_types import DrumInstrument, Instrument, Song
from midi2mkcd.arcade.sim_sequencer import (
    MUSIC_DEFAULT_VOLUME,
    metronome_interval_ms,
    note_frequency,
    sequence_song,
)
from midi2mkcd.arcade.sim_song import render_drum_instrument, render_instrument
from midi2mkcd.errors import MissingDependencyError

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

logger = logging.getLogger(__name__)

# A sound the simulator starts: (start time in ms, its play instructions)
SongEvent = tuple[float, bytes]
# progress(done, total), e.g. to drive a progress bar; the library never prints
ProgressCallback = Callable[[int, int], None]

DEFAULT_RATE = 48000
NUMPY_INSTALL_ERROR = 'audio rendering needs numpy: pip install "midi2mkcd[audio]"'


def note_instructions(
    instrument: Instrument, note_index: int, gate_ms: float, volume: float
) -> bytes:
    """Play instructions for one melodic note.

    `note_index` is the decoded Arcade note (MIDI note - 23, after the track's
    octave offset). An index outside the simulator's 0-96 frequency table
    plays at 0 Hz, as in the simulator. `volume` is the sequencer volume,
    `music.volume() * velocity / 128` (127 for velocity 127 at the default
    music volume)."""
    return render_instrument(instrument, note_frequency(note_index), gate_ms, volume)


def drum_instructions(drum: DrumInstrument, volume: float) -> bytes:
    """Play instructions for one drum hit. Raises ``ValueError`` for a drum
    with no steps (which the simulator can't play; it stays silent)."""
    return render_drum_instrument(drum, volume)


def song_events(
    song: Song,
    *,
    use_velocity: bool = True,
    global_volume: float = MUSIC_DEFAULT_VOLUME,
) -> list[SongEvent]:
    """Every sound the simulator starts while playing `song` once, in the
    order it starts them, as (start ms, instructions).

    Start times use the simulator's real tick spacing (the metronome's
    truncated `setInterval` delay: 20 ms for converted songs, so they play 4%
    fast), counted from the first tick. Gates use the exact tick length.

    `use_velocity=False` plays every note event at velocity 128, as Arcade
    versions that ignore the velocity section do. `global_volume` is
    `music.volume()` (default 128, a game can set 0-255; clamped to 0-1024 as
    in the simulator)."""
    if not use_velocity:
        song = replace(
            song,
            tracks=[
                replace(
                    track,
                    notes=[replace(event, velocity=None) for event in track.notes],
                )
                for track in song.tracks
            ],
        )

    interval = metronome_interval_ms(song)
    sounds = sequence_song(song, global_volume)
    events = [(float(s.tick * interval), s.instructions) for s in sounds]
    out_of_range = _count_out_of_range_notes(song)
    if out_of_range:
        logger.info(
            "%d melodic note(s) are outside the simulator's frequency table "
            "(MIDI 23-119) and play at 0 Hz",
            out_of_range,
        )
    logger.debug("%d sounds, tick interval %d ms", len(events), interval)
    return events


def _count_out_of_range_notes(song: Song) -> int:
    return sum(
        1
        for track in song.tracks
        if track.drums is None
        for event in track.notes
        for note in event.notes
        if math.isnan(note_frequency(note.note))
    )


# ---------------------------------------------------------------------------
# Stage 2: play instructions -> samples (pxtsim/sound/playInstructions.ts)
# ---------------------------------------------------------------------------
#
# How the simulator plays one instruction buffer (one note or drum hit), which
# this reproduces:
# - Every generator (one per waveform the buffer uses) is created and started
#   while the buffer is scheduled, so all of them run from the note's start.
#   Its gain starts at 0.
# - Per instruction: the gain is set to the start volume and ramps linearly to
#   the end volume; oscillators (1-3) set their start frequency and ramp to the
#   end one. Looped-buffer sources (4, 5, 11-18) only ramp their playbackRate
#   to the end value, from the previous automation event (or, the first time,
#   from the creation rate at the note's start).
# - When the waveform changes, the previous waveform's gain decays towards 0
#   with `setTargetAtTime(0, t, 0.015)`.
# - Waveform-0 instructions are rests: they only advance time.
# - The note stops at the end of its instructions (`U.delay(totalDuration)` then
#   `stop()`), so the final 15 ms fade on the note's own gain barely runs and is
#   left out.

_SET_TARGET_TIME_CONSTANT_MS = 15.0
# AudioBufferSourceNode.playbackRate is k-rate: read once per render quantum
_RENDER_QUANTUM = 128
_OSCILLATORS = (1, 2, 3)
_TRIANGLE, _SAWTOOTH, _SINE = _OSCILLATORS
_TUNABLE_NOISE = 4
_NOISE = 5
_SQUARES = range(11, 16)
_CYCLES = range(16, 19)
# Waveforms that getGenerator can make
_KNOWN_WAVEFORMS = frozenset(
    {*_OSCILLATORS, _TUNABLE_NOISE, _NOISE, *_SQUARES, *_CYCLES}
)
# Chrome's PeriodicWave tables: size, and ranges per octave of fundamental
_PERIODIC_WAVE_SIZE = 4096
_RANGES_PER_OCTAVE = 3
_INT16_MAX = 32767


@dataclass(frozen=True)
class _Instruction:
    wave: int
    start_hz: int
    duration_ms: int
    start_volume: int
    end_volume: int
    end_hz: int


@dataclass
class _WaveSchedule:
    """What one note schedules on one waveform's generator."""

    # (start ms, end ms, instruction), times relative to the note's start
    ramps: list[tuple[float, float, _Instruction]]
    # Times (relative ms) at which the gain starts decaying towards 0
    fades: list[float]


def _numpy() -> Any:
    try:
        import numpy
    except ImportError:
        raise MissingDependencyError(NUMPY_INSTALL_ERROR) from None
    return numpy


def _parse_instructions(buf: bytes) -> list[_Instruction]:
    """Split a buffer into 12-byte instructions (a trailing partial one is
    ignored, as the simulator's reads past the end give 0-duration rests)."""
    usable = len(buf) - len(buf) % 12
    return [
        _Instruction(wave, start_hz, duration, start_volume, end_volume, end_hz)
        for wave, _flags, start_hz, duration, start_volume, end_volume, end_hz in (
            struct.iter_unpack("<BBHHHHH", buf[:usable])
        )
    ]


def _schedule(
    instructions: list[_Instruction],
) -> tuple[dict[int, _WaveSchedule], float]:
    """Each waveform's schedule and the note's total duration in ms."""
    schedules: dict[int, _WaveSchedule] = {}
    time = 0.0
    total = 0.0
    current = 0
    for instruction in instructions:
        duration = float(instruction.duration_ms)
        total += duration
        wave = instruction.wave
        if wave == 0:
            time += duration
            continue
        if wave not in _KNOWN_WAVEFORMS:
            # getGenerator returns null and the simulator throws here; what was
            # scheduled so far still plays
            logger.debug(f"Unknown waveform {wave}; the rest of the sound is silent")
            break
        if current and wave != current:
            schedules[current].fades.append(time)
        schedules.setdefault(wave, _WaveSchedule([], [])).ramps.append(
            (time, time + duration, instruction)
        )
        current = wave
        time += duration
    return schedules, total


def synthesize(
    events: Iterable[SongEvent],
    rate: int = DEFAULT_RATE,
    *,
    progress: ProgressCallback | None = None,
) -> NDArray[np.float32]:
    """
    Play sounds the way the simulator does (Web Audio in Chrome), mixed into
    one mono float32 buffer.

    :param events: (start ms, play instructions), e.g. from `song_events`.
    :param rate: Sample rate in Hz (the simulator uses its AudioContext's rate).
    :param progress: Called as ``progress(done, total)`` with the number of
     sounds synthesized so far: once with 0 before the first, then after each.
    :return: Samples, unclipped, long enough for every sound to finish. Sample 0
     is time 0.
    :raises MissingDependencyError: If numpy isn't installed.
    """
    np_ = _numpy()
    if rate <= 0:
        raise ValueError(f"rate must be positive, got {rate}")
    notes = []
    end_ms = 0.0
    for start_ms, buf in events:
        schedules, total = _schedule(_parse_instructions(buf))
        notes.append((start_ms, schedules, total))
        end_ms = max(end_ms, start_ms + total)
    out = np_.zeros(_first_sample_at(end_ms, rate), dtype=np_.float64)
    if progress is not None:
        progress(0, len(notes))
    for done, (start_ms, schedules, total) in enumerate(notes, start=1):
        _render_note(np_, out, start_ms, total, schedules, rate)
        if progress is not None:
            progress(done, len(notes))
    result: NDArray[np.float32] = out.astype(np_.float32)
    return result


def _first_sample_at(ms: float, rate: int) -> int:
    """Index of the first sample whose time is at or after `ms`."""
    return max(0, math.ceil(ms * rate / 1000 - 1e-9))


def _render_note(
    np_: Any,
    out: Any,
    start_ms: float,
    total_ms: float,
    schedules: dict[int, _WaveSchedule],
    rate: int,
) -> None:
    n0 = _first_sample_at(start_ms, rate)
    n1 = _first_sample_at(start_ms + total_ms, rate)
    if n1 <= n0 or not schedules:
        return
    samples = np_.arange(n0, n1)
    # Each sample's time relative to the note's start, in ms
    t = samples * (1000 / rate) - start_ms
    acc = np_.zeros(n1 - n0)
    for wave, schedule in schedules.items():
        gain = _gain_curve(np_, t, schedule)
        if not gain.any():
            continue
        if wave in _SQUARES:
            gain *= 0.5
        acc += gain * _generator(np_, wave, schedule, t, samples, rate)
    out[n0:n1] += acc


def _index(np_: Any, t: Any, ms: float) -> int:
    """First index into `t` at or after `ms`."""
    return int(np_.searchsorted(t, ms - 1e-9, side="left"))


def _gain_curve(np_: Any, t: Any, schedule: _WaveSchedule) -> Any:
    """The waveform's gain (`(v / 1024) / 4`, squares not yet halved) per sample."""
    gain = np_.zeros(len(t))
    events: list[tuple[float, int, float, _Instruction | None]] = [
        (start, 1, end, instruction) for start, end, instruction in schedule.ramps
    ]
    events += [(at, 0, at, None) for at in schedule.fades]
    events.sort(key=lambda e: (e[0], e[1]))
    value = 0.0
    hold_from = 0  # index from which `value` holds (or decays)
    decay_from: float | None = None  # relative ms the current decay started
    for start, _kind, end, instruction in events:
        i_start = _index(np_, t, start)
        _fill_hold(np_, gain, t, hold_from, i_start, value, decay_from)
        if instruction is None:
            # setTargetAtTime: decay from whatever the gain is at `start`
            value = _value_at(value, decay_from, start)
            decay_from = start
            hold_from = i_start
            continue
        g0 = instruction.start_volume / 1024 / 4
        g1 = instruction.end_volume / 1024 / 4
        i_end = _index(np_, t, end)
        if i_end > i_start:
            gain[i_start:i_end] = g0 + (g1 - g0) * (t[i_start:i_end] - start) / (
                end - start
            )
        value = g1
        decay_from = None
        hold_from = max(i_start, i_end)
    _fill_hold(np_, gain, t, hold_from, len(t), value, decay_from)
    return gain


def _value_at(value: float, decay_from: float | None, ms: float) -> float:
    if decay_from is None:
        return value
    return value * math.exp(-(ms - decay_from) / _SET_TARGET_TIME_CONSTANT_MS)


def _fill_hold(
    np_: Any,
    gain: Any,
    t: Any,
    i0: int,
    i1: int,
    value: float,
    decay_from: float | None,
) -> None:
    if i1 <= i0 or value == 0:
        return
    if decay_from is None:
        gain[i0:i1] = value
    else:
        gain[i0:i1] = value * np_.exp(
            -(t[i0:i1] - decay_from) / _SET_TARGET_TIME_CONSTANT_MS
        )


def _generator(
    np_: Any, wave: int, schedule: _WaveSchedule, t: Any, samples: Any, rate: int
) -> Any:
    """The waveform's generator output per sample (before gain), running from
    the note's start."""
    if wave in _OSCILLATORS:
        frequency = _oscillator_frequency(np_, schedule, t)
        phase = _phase(np_, frequency / rate)
        if wave == _SINE:
            return np_.sin(2 * np_.pi * phase)
        return _periodic_wave(np_, wave, phase, frequency, rate)

    buffer = _buffer(wave)
    if wave == _NOISE:
        position = np_.arange(len(t), dtype=np_.float64)
    else:
        samples_per_cycle = 1024 if wave in _SQUARES else 4
        playback_rate = _playback_rate(
            np_, schedule, t, samples, rate, samples_per_cycle
        )
        position = _phase(np_, playback_rate)
    return _looped(np_, np_.asarray(buffer), position)


def _phase(np_: Any, step: Any) -> Any:
    """Running sum of `step`, starting at 0 on the first sample."""
    phase = np_.empty(len(step))
    phase[0] = 0.0
    np_.cumsum(step[:-1], out=phase[1:])
    return phase


def _oscillator_frequency(np_: Any, schedule: _WaveSchedule, t: Any) -> Any:
    """OscillatorNode.frequency per sample: the creation frequency until the
    first instruction, then setValueAtTime + linearRampToValueAtTime per
    instruction, holding the end value in between."""
    first = schedule.ramps[0][2]
    freq = np_.full(len(t), float(first.start_hz))
    for start, end, instruction in schedule.ramps:
        i_start = _index(np_, t, start)
        i_end = _index(np_, t, end)
        f0, f1 = float(instruction.start_hz), float(instruction.end_hz)
        if i_end > i_start:
            freq[i_start:i_end] = f0 + (f1 - f0) * (t[i_start:i_end] - start) / (
                end - start
            )
        freq[max(i_start, i_end) :] = f1
    return freq


def _playback_rate(
    np_: Any,
    schedule: _WaveSchedule,
    t: Any,
    samples: Any,
    rate: int,
    samples_per_cycle: int,
) -> Any:
    """AudioBufferSourceNode.playbackRate per sample (k-rate, so constant over
    each 128-sample render quantum): the creation value at the note's start,
    then a chain of linear ramps to each instruction's end frequency."""
    scale = samples_per_cycle / rate
    first = schedule.ramps[0][2]
    xs = [0.0]
    ys = [first.start_hz * scale]
    for _start, end, instruction in schedule.ramps:
        # A ramp ending where the previous event is jumps there
        xs.append(max(end, xs[-1] + 1e-9))
        ys.append(instruction.end_hz * scale)
    quantum_start = (samples // _RENDER_QUANTUM) * _RENDER_QUANTUM
    t_quantum = t - (samples - quantum_start) * (1000 / rate)
    return np_.interp(t_quantum, xs, ys)


def _looped(np_: Any, buffer: Any, position: Any) -> Any:
    """Looped buffer playback with linear interpolation, as Chrome does."""
    size = len(buffer)
    position = np_.mod(position, size)
    i0 = np_.floor(position).astype(np_.int64)
    frac = position - i0
    i0 %= size
    i1 = (i0 + 1) % size
    return buffer[i0] * (1 - frac) + buffer[i1] * frac


@functools.cache
def _buffer(wave: int) -> Any:
    """The simulator's looped buffer for a waveform (sample values don't depend
    on the sample rate; only the playback rate does)."""
    np_ = _numpy()
    if wave == _NOISE:
        return np_.array(
            [((x & 1023) / 512.0) - 1.0 for x in _xorshift(100000)], dtype=np_.float32
        )
    if wave == _TUNABLE_NOISE:
        out = np_.zeros(131072, dtype=np_.float32)
        for i, x in enumerate(_xorshift(131072 // 4)):
            if x & 0x8000:
                out[4 * i : 4 * i + 4] = (1.0, 1.0, -1.0, -1.0)
        return out
    if wave in _SQUARES:
        duty = (wave - 10) * 10
        index = np_.arange(1024)
        return np_.where(index < duty / 100 * 1024, 1.0, -1.0).astype(np_.float32)
    if wave in _CYCLES:
        bits = wave - 16 + 4
        cycle_bits = (0x2DF0EB47, 0xC8165A93)
        mask = (0xF, 0x1F, 0x3F)[bits - 4]
        out = np_.zeros(1024, dtype=np_.float32)
        for i in range(0, 1024, 4):
            cycle = (i // 4) & mask
            if cycle_bits[cycle >> 5] & (1 << (cycle & 0x1F)):
                out[i : i + 4] = (1.0, 1.0, -1.0, -1.0)
        return out
    raise ValueError(f"No buffer for waveform {wave}")


def _xorshift(count: int) -> list[int]:
    """The simulator's xorshift sequence on JS int32s, seed 0xf01ba80."""
    out = []
    x = 0xF01BA80
    for _ in range(count):
        x = _int32(x ^ (x << 13))
        x = _int32(x ^ (x >> 17))
        x = _int32(x ^ (x << 5))
        out.append(x)
    return out


def _int32(x: int) -> int:
    x &= 0xFFFFFFFF
    return x - (1 << 32) if x & 0x80000000 else x


def _periodic_wave(np_: Any, wave: int, phase: Any, frequency: Any, rate: int) -> Any:
    """A band-limited triangle or sawtooth like Chrome's OscillatorNode: wavetables
    with fewer partials for higher fundamentals (three ranges per octave),
    blended between the two nearest ranges, normalised so the full-band table
    peaks at 1 (which puts the sawtooth's Gibbs overshoot inside +-1)."""
    tables = _periodic_wave_tables(wave)
    size = _PERIODIC_WAVE_SIZE
    range_count = tables.shape[0]
    lowest = rate / size
    ratio = np_.maximum(np_.abs(frequency), 1e-9) / lowest
    pitch_range = 1 + _RANGES_PER_OCTAVE * np_.log2(np_.maximum(ratio, 1e-12))
    pitch_range = np_.clip(pitch_range, 0, range_count - 1)
    lower = np_.floor(pitch_range).astype(np_.int64)
    higher = np_.minimum(lower + 1, range_count - 1)
    blend = pitch_range - lower

    position = np_.mod(phase, 1.0) * size
    i0 = np_.floor(position).astype(np_.int64) % size
    frac = position - np_.floor(position)
    i1 = (i0 + 1) % size

    def lookup(r: Any) -> Any:
        return tables[r, i0] * (1 - frac) + tables[r, i1] * frac

    return lookup(lower) * (1 - blend) + lookup(higher) * blend


@functools.cache
def _periodic_wave_tables(wave: int) -> Any:
    np_ = _numpy()
    size = _PERIODIC_WAVE_SIZE
    max_partials = size // 2
    range_count = round(_RANGES_PER_OCTAVE * math.log2(size))
    n = np_.arange(max_partials, dtype=np_.float64)
    coefficients = np_.zeros(max_partials)
    nz = n[1:]
    if wave == _SAWTOOTH:
        coefficients[1:] = 2 / (np_.pi * nz) * np_.where(nz % 2 == 1, 1.0, -1.0)
    elif wave == _TRIANGLE:
        odd = nz % 2 == 1
        sign = np_.where((nz % 4) == 1, 1.0, -1.0)
        coefficients[1:] = np_.where(odd, 8 / (np_.pi**2 * nz**2) * sign, 0.0)
    else:
        raise ValueError(f"No periodic wave for waveform {wave}")
    tables = np_.empty((range_count, size))
    scale = 1.0
    for r in range(range_count):
        partials = int(max_partials * 2 ** (-r / _RANGES_PER_OCTAVE))
        spectrum = np_.zeros(size // 2 + 1, dtype=np_.complex128)
        spectrum[1:partials] = -0.5j * coefficients[1:partials] * size
        table = np_.fft.irfft(spectrum, size)
        if r == 0:
            scale = 1 / float(np_.abs(table).max())
        tables[r] = table * scale
    return tables


def render_song(
    song: Song,
    rate: int = DEFAULT_RATE,
    *,
    use_velocity: bool = True,
    global_volume: float = MUSIC_DEFAULT_VOLUME,
    progress: ProgressCallback | None = None,
) -> NDArray[np.float32]:
    """`synthesize(song_events(song, ...))`: the song as the simulator plays it
    once, until every sound has finished. `progress` is passed to `synthesize`."""
    events = song_events(song, use_velocity=use_velocity, global_volume=global_volume)
    return synthesize(events, rate, progress=progress)


def to_wav_bytes(samples: NDArray[np.float32], rate: int = DEFAULT_RATE) -> bytes:
    """
    A mono 16-bit PCM WAV file. Samples past +-1 are clipped, as the output
    device would, with a WARNING giving the peak and how many were clipped.
    """
    np_ = _numpy()
    data = np_.asarray(samples, dtype=np_.float64)
    if len(data):
        peak = float(np_.abs(data).max())
        clipped = int(np_.count_nonzero(np_.abs(data) > 1.0))
        if clipped:
            logger.warning(
                f"{clipped} sample(s) clipped (peak {peak:.3f}, "
                f"{20 * math.log10(peak):+.1f} dBFS)"
            )
        else:
            logger.debug(f"Peak {peak:.3f}")
    pcm = np_.round(np_.clip(data, -1.0, 1.0) * _INT16_MAX).astype("<i2")
    buf = io.BytesIO()
    with wav_file.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()
