"""Instrument params -> SF2 SoundFont, to hear Arcade-like instruments in a MIDI
editor.

Every sample is rendered the way the simulator plays the note (`audio.py`,
Stage 1 and 2), so this needs numpy (the `audio` extra). The RIFF file itself
is written by hand with `struct` (`write_sf2`). With `SoundFontOptions.sf3`
the samples are Ogg Vorbis (SF3, MuseScore's format), encoded with soundfile
(the `compress` extra).

Melodic programs become bank 0 presets 0-127; the drum kit is bank 128
preset 0, one one-shot zone per drum note. A melodic sample holds the note's
attack and decay followed by a loop cut from its sustain (crossfaded at the
loop end), and the SF2 volume envelope adds the release. Instruments whose
sustain is silent (plucks) are one-shots.

Loudness: samples are rendered at full sequencer volume for resolution, then
scaled to what the simulator plays at `music.volume()` (default 128), so the
SF2 is as loud as Arcade at velocity 127. Each zone replaces SF2's default
velocity -> attenuation modulator with one that makes amplitude proportional
to velocity, as in the simulator, and turns off the default velocity -> filter
modulator.

Not re-exported from the package root.
"""

from __future__ import annotations

import logging
import math
import struct
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from fractions import Fraction
from typing import Any

from midi2mkcd.api import ParamsInput, _resolve_params
from midi2mkcd.arcade.music import (
    decode_drum_instrument,
    decode_instrument,
    encode_drum_instrument,
    encode_instrument,
)
from midi2mkcd.arcade.music_types import LFO, DrumInstrument, Instrument
from midi2mkcd.arcade.sim_sequencer import FREQUENCIES, MUSIC_DEFAULT_VOLUME
from midi2mkcd.audio import (
    ProgressCallback,
    _encode,
    _numpy,
    check_audio_format,
    drum_instructions,
    note_instructions,
    synthesize,
)
from midi2mkcd.errors import Midi2MkcdError
from midi2mkcd.midi_to_song.timeline.processor import pitch_compensation_scale
from midi2mkcd.utils.gm_names import drum_note_name, melodic_instrument_name
from midi2mkcd.utils.parallel import check_jobs, resolve_jobs, run_in_processes

logger = logging.getLogger(__name__)

DEFAULT_RATE = 22050
# The simulator's frequency table (Arcade note indices 0-96); outside it notes
# play at 0 Hz
TABLE_LOWEST_MIDI = 23
DEFAULT_NOTES = range(TABLE_LOWEST_MIDI, TABLE_LOWEST_MIDI + len(FREQUENCIES))
DRUM_BANK = 128

# SF2 releases fall linearly in dB to ~-100 dB; Arcade's fall linearly in
# amplitude over R ms, so an SF2 release of R sounds much shorter. Starting
# point from RENDER_FEATURES_PLAN.md, not yet measured with a player.
RELEASE_FACTOR = 2.0
# Default modulator amount is 960 cB (amplitude ~ velocity^2); half of it
# gives amplitude ~ velocity, as the simulator plays it. Not yet measured.
VELOCITY_ATTENUATION_CB = 480

_NOISE_WAVES = frozenset({4, 5})  # noise_tunable, noise
_PITCHLESS_WAVES = frozenset({5})  # noise ignores the note's frequency
_CYCLE_LENGTHS = {16: 16, 17: 32, 18: 64}
_CROSSFADE_MS = 20.0
_MARGIN_MS = 20.0
_MIN_LOOP_MS = 100.0
_NOISE_LOOP_MS = 500.0
_MAX_SEARCH_MS = 50.0
_MAX_LFO_LOOP_MS = 4000
# Above this normalised correlation the loop ends line up: crossfade linearly
# (equal-power would bump the level by up to 3 dB)
_LINEAR_CROSSFADE_CORRELATION = 0.5
_GUARD_POINTS = 46  # zero sample points required after every PCM sample
_SAMPLE_MONO = 1  # sfSampleType
_SAMPLE_VORBIS = 0x10  # SF3's flag for an Ogg Vorbis compressed sample
_POST_LOOP_POINTS = 8  # copies of the loop start after the loop end
_FULL_VOLUME = 1024  # sequencer volume the samples are rendered at
_INT16_MAX = 32767
_MAX_RIFF_BYTES = 0xFFFF_FFFF
# pdta indices (bags, generators, modulators, sample IDs) are 16-bit WORDs
_MAX_PDTA_RECORDS = 0xFFFF

# SF2 generator and modulator numbers (SoundFont 2.01 spec, section 8)
_GEN_INITIAL_FILTER_FC = 8
_GEN_RELEASE_VOL_ENV = 38
_GEN_INSTRUMENT = 41
_GEN_KEY_RANGE = 43
_GEN_VEL_RANGE = 44
_GEN_INITIAL_ATTENUATION = 48
_GEN_SAMPLE_ID = 53
_GEN_SAMPLE_MODES = 54
_GEN_SCALE_TUNING = 56
_GEN_OVERRIDING_ROOT_KEY = 58
_MIN_TIMECENTS = -12000
_MAX_TIMECENTS = 8000
# Drums play to the end in Arcade whatever the gate: release as slowly as SF2
# allows (about -1 dB per second) so a short note-off doesn't cut them
_DRUM_RELEASE_TIMECENTS = _MAX_TIMECENTS
_MIN_SAMPLE_POINTS = 32
# Note-on velocity, unipolar, negative, concave / linear
_MOD_SRC_VELOCITY_CONCAVE_NEG = 0x0502
_MOD_SRC_VELOCITY_LINEAR_NEG = 0x0102
# Note-on velocity, unipolar, positive, switch (FluidSynth's amount source for
# its velocity -> filter default)
_MOD_SRC_VELOCITY_SWITCH = 0x0C02


# ---------------------------------------------------------------------------
# SF2 file structure
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Sf2Sample:
    """A mono sample: 16-bit little-endian PCM, or for SF3 (`vorbis_frames`
    set) an Ogg Vorbis stream that decodes to that many sample points."""

    name: str
    data: bytes
    rate: int
    original_pitch: int  # MIDI note
    # (start, end) in sample points, end exclusive; None for a one-shot
    loop: tuple[int, int] | None = None
    vorbis_frames: int | None = None

    @property
    def frames(self) -> int:
        """The number of sample points."""
        if self.vorbis_frames is not None:
            return self.vorbis_frames
        return len(self.data) // 2


@dataclass(frozen=True)
class Sf2Zone:
    sample: int  # index into the samples
    key_range: tuple[int, int]
    root_key: int
    vel_range: tuple[int, int] | None = None
    release_timecents: int = _MIN_TIMECENTS
    scale_tuning: int | None = None  # cents per key; None for the default 100


@dataclass(frozen=True)
class Sf2Instrument:
    name: str
    zones: tuple[Sf2Zone, ...]


@dataclass(frozen=True)
class Sf2Preset:
    name: str
    bank: int
    program: int
    instrument: int  # index into the instruments


# (source, destination, amount, amount source, transform)
Sf2Modulator = tuple[int, int, int, int, int]

# Added to every instrument zone: velocity -> attenuation linear in amplitude,
# and the velocity -> filter cutoff defaults (both spellings players use) off
ZONE_MODULATORS: tuple[Sf2Modulator, ...] = (
    (_MOD_SRC_VELOCITY_CONCAVE_NEG, _GEN_INITIAL_ATTENUATION,
     VELOCITY_ATTENUATION_CB, 0, 0),
    (_MOD_SRC_VELOCITY_LINEAR_NEG, _GEN_INITIAL_FILTER_FC, 0, 0, 0),
    (_MOD_SRC_VELOCITY_LINEAR_NEG, _GEN_INITIAL_FILTER_FC, 0,
     _MOD_SRC_VELOCITY_SWITCH, 0),
)  # fmt: skip


def write_sf2(
    name: str,
    samples: Sequence[Sf2Sample],
    instruments: Sequence[Sf2Instrument],
    presets: Sequence[Sf2Preset],
    *,
    comment: str | None = None,
) -> bytes:
    """
    A SoundFont 2.01 file, or an SF3 file (version 3.0) if any sample is Ogg
    Vorbis (`Sf2Sample.vorbis_frames` set).

    Every instrument zone gets `ZONE_MODULATORS`, a looped sample loops
    continuously, and presets have one zone that selects their instrument. An
    instrument with more than one zone gets a global zone holding the
    modulators and the generators all its zones share, so the 16-bit record
    indices go further (see `instrument_zone_records`).

    :raises Midi2MkcdError: If the file would be too large for RIFF (4 GB) or
     have more records than SF2's 16-bit indices reach.
    """
    sf3 = any(sample.vorbis_frames is not None for sample in samples)
    info = _chunk(b"ifil", struct.pack("<HH", *((3, 0) if sf3 else (2, 1))))
    info += _chunk(b"isng", _zstr("EMU8000"))
    info += _chunk(b"INAM", _zstr(name))
    if comment:
        info += _chunk(b"ICMT", _zstr(comment))

    smpl = bytearray()
    shdr = bytearray()
    for sample in samples:
        shdr += _name(sample.name) + _add_sample_data(smpl, sample)
    shdr += _name("EOS") + bytes(26)

    sample_info = [(s.original_pitch, s.loop is not None) for s in samples]
    zone_records = [instrument_zone_records(i, sample_info) for i in instruments]
    check_record_counts(zone_records, len(samples))
    inst = bytearray()
    ibag = bytearray()
    imod = bytearray()
    igen = bytearray()
    bags = gens = mods = 0
    for instrument, records in zip(instruments, zone_records, strict=True):
        inst += _name(instrument.name) + struct.pack("<H", bags)
        for zone_gens, zone_mods in records:
            ibag += struct.pack("<HH", gens, mods)
            bags += 1
            for modulator in zone_mods:
                imod += struct.pack("<HHhHH", *modulator)
            mods += len(zone_mods)
            igen += b"".join(zone_gens)
            gens += len(zone_gens)
    inst += _name("EOI") + struct.pack("<H", bags)
    ibag += struct.pack("<HH", gens, mods)
    imod += bytes(10)
    igen += bytes(4)

    phdr = bytearray()
    pbag = bytearray()
    pgen = bytearray()
    for number, preset in enumerate(presets):
        phdr += _name(preset.name) + struct.pack(
            "<HHHIII", preset.program, preset.bank, number, 0, 0, 0
        )
        pbag += struct.pack("<HH", number, 0)
        pgen += struct.pack("<HH", _GEN_INSTRUMENT, preset.instrument)
    phdr += _name("EOP") + struct.pack("<HHHIII", 0, 0, len(presets), 0, 0, 0)
    pbag += struct.pack("<HH", len(presets), 0)
    pmod = bytes(10)
    pgen += bytes(4)

    pdta = b"".join(
        _chunk(tag, bytes(data))
        for tag, data in (
            (b"phdr", phdr), (b"pbag", pbag), (b"pmod", pmod), (b"pgen", pgen),
            (b"inst", inst), (b"ibag", ibag), (b"imod", imod), (b"igen", igen),
            (b"shdr", shdr),
        )
    )  # fmt: skip
    body = (
        b"sfbk"
        + _list(b"INFO", info)
        + _list(b"sdta", _chunk(b"smpl", bytes(smpl)))
        + _list(b"pdta", pdta)
    )
    if len(body) > _MAX_RIFF_BYTES:
        raise Midi2MkcdError(
            f"The SoundFont would be {len(body)} bytes, over RIFF's 4 GB limit"
        )
    return _chunk(b"RIFF", body)


def _add_sample_data(smpl: bytearray, sample: Sf2Sample) -> bytes:
    """Append the sample to the smpl data and return its shdr record after the
    name.

    PCM (SF2): start/end and the loop are absolute sample points, and the
    sample is followed by 46 zero points. Ogg Vorbis (SF3, as FluidSynth and
    SpessaSynth read it): start/end are byte offsets of the Ogg stream (end
    exclusive), the loop is in decoded points relative to the sample's start,
    the type has the compressed flag, and odd-length streams get one padding
    byte so the next sample starts on a 16-bit boundary."""
    loop = sample.loop if sample.loop is not None else (0, sample.frames)
    if sample.vorbis_frames is None:
        if len(sample.data) % 2:
            raise ValueError(f"sample {sample.name!r} has an odd byte count")
        start = len(smpl) // 2
        end = start + sample.frames
        smpl += sample.data + bytes(2 * _GUARD_POINTS)
        loop_start, loop_end = start + loop[0], start + loop[1]
        sample_type = _SAMPLE_MONO
    else:
        start = len(smpl)
        end = start + len(sample.data)
        smpl += sample.data + bytes(len(sample.data) % 2)
        loop_start, loop_end = loop
        sample_type = _SAMPLE_MONO | _SAMPLE_VORBIS
    return struct.pack(
        "<IIIIIBbHH",
        start, end, loop_start, loop_end, sample.rate,
        sample.original_pitch, 0, 0, sample_type,
    )  # fmt: skip


# A zone's records: its generators (4 bytes each) and modulators
ZoneRecords = tuple[list[bytes], tuple[Sf2Modulator, ...]]
# Generators that stay in their zone: they say which zone plays
_LOCAL_GENERATORS = frozenset({_GEN_KEY_RANGE, _GEN_VEL_RANGE, _GEN_SAMPLE_ID})


def instrument_zone_records(
    instrument: Sf2Instrument, sample_info: Sequence[tuple[int, bool]]
) -> list[ZoneRecords]:
    """
    The instrument's zones as written: with more than one zone, a global zone
    first with `ZONE_MODULATORS` and every generator all the zones have with
    the same amount, which the zones then leave out (the SF2 spec has zones
    inherit the global zone's generators and modulators).

    :param sample_info: (original pitch, looped) of each sample, by index.
    """
    local = [
        _zone_generators(zone, *sample_info[zone.sample]) for zone in instrument.zones
    ]
    if len(local) < 2:
        return [([g for _, g in gens], ZONE_MODULATORS) for gens in local]
    shared = set.intersection(*(set(gens) for gens in local))
    shared = {(op, g) for op, g in shared if op not in _LOCAL_GENERATORS}
    global_gens = [g for op, g in local[0] if (op, g) in shared]
    return [(global_gens, ZONE_MODULATORS)] + [
        ([g for op, g in gens if (op, g) not in shared], ()) for gens in local
    ]


def check_record_counts(
    zone_records: Iterable[list[ZoneRecords]], samples: int
) -> None:
    """:raises Midi2MkcdError: If SF2's 16-bit indices can't reach every
    instrument zone, generator, modulator or sample."""
    bags = gens = mods = 0
    for records in zone_records:
        bags += len(records)
        gens += sum(len(g) for g, _ in records)
        mods += sum(len(m) for _, m in records)
    for what, count in (
        ("instrument zones", bags), ("instrument generators", gens),
        ("instrument modulators", mods), ("samples", samples),
    ):  # fmt: skip
        if count > _MAX_PDTA_RECORDS:
            raise Midi2MkcdError(
                f"The SoundFont would have {count} {what}, over SF2's limit of "
                f"{_MAX_PDTA_RECORDS}; render fewer samples (a larger step, fewer "
                "velocity layers or a smaller note range)"
            )


def _zone_generators(
    zone: Sf2Zone, original_pitch: int, looped: bool
) -> list[tuple[int, bytes]]:
    """(operator, record) pairs: keyRange and velRange first, sampleID last,
    as the spec requires. overridingRootKey only if it isn't the sample's
    original pitch."""
    out = [(_GEN_KEY_RANGE, struct.pack("<HBB", _GEN_KEY_RANGE, *zone.key_range))]
    if zone.vel_range is not None:
        out.append(
            (_GEN_VEL_RANGE, struct.pack("<HBB", _GEN_VEL_RANGE, *zone.vel_range))
        )
    if zone.root_key != original_pitch:
        out.append(
            (
                _GEN_OVERRIDING_ROOT_KEY,
                struct.pack("<Hh", _GEN_OVERRIDING_ROOT_KEY, zone.root_key),
            )
        )
    if zone.scale_tuning is not None:
        out.append(
            (
                _GEN_SCALE_TUNING,
                struct.pack("<Hh", _GEN_SCALE_TUNING, zone.scale_tuning),
            )
        )
    out.append(
        (_GEN_SAMPLE_MODES, struct.pack("<Hh", _GEN_SAMPLE_MODES, 1 if looped else 0))
    )
    out.append(
        (
            _GEN_RELEASE_VOL_ENV,
            struct.pack("<Hh", _GEN_RELEASE_VOL_ENV, zone.release_timecents),
        )
    )
    out.append((_GEN_SAMPLE_ID, struct.pack("<HH", _GEN_SAMPLE_ID, zone.sample)))
    return out


def _chunk(tag: bytes, data: bytes) -> bytes:
    pad = b"\0" if len(data) % 2 else b""
    return tag + struct.pack("<I", len(data)) + data + pad


def _list(kind: bytes, data: bytes) -> bytes:
    return _chunk(b"LIST", kind + data)


def _ascii(text: str) -> bytes:
    return text.encode("ascii", "replace")


def _name(text: str) -> bytes:
    """A 20-byte name field: at most 19 characters, NUL-padded."""
    return _ascii(text)[:19].ljust(20, b"\0")


def _zstr(text: str) -> bytes:
    """A NUL-terminated INFO string, padded to an even length."""
    data = _ascii(text) + b"\0"
    return data + b"\0" if len(data) % 2 else data


# ---------------------------------------------------------------------------
# Params -> samples
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _MelodicPlan:
    """How one melodic sample is rendered and cut, all in ms."""

    looped: bool
    gate_ms: float
    loop_start_ms: float  # loop start (looped only)
    loop_ms: float  # nominal loop length (looped only)
    search_ms: float  # how far the loop end may move to line up (looped only)
    noise: bool

    def length_ms(self, release_ms: float) -> float:
        """Upper bound on the sample's length."""
        if self.looped:
            return self.loop_start_ms + self.loop_ms + self.search_ms
        return self.gate_ms + release_ms + 10


def _plan_melodic(instrument: Instrument, midi_note: int) -> _MelodicPlan:
    amp = instrument.amp_envelope
    noise = instrument.waveform in _NOISE_WAVES
    amp_lfo_on = instrument.amp_lfo is not None and bool(instrument.amp_lfo.amplitude)
    if not amp.amplitude or (not amp.sustain and not amp_lfo_on):
        # Silent once the decay ends: a one-shot of attack and decay
        return _MelodicPlan(False, float(amp.attack + amp.decay), 0, 0, 0, noise)

    steady = float(amp.attack + amp.decay)
    pitch = instrument.pitch_envelope
    if pitch is not None and pitch.amplitude:
        steady = max(steady, float(pitch.attack + pitch.decay))
    # The ramp from the envelope's last point to each LFO's next sample point
    # doesn't repeat; the sound is periodic only from there on
    for lfo in (instrument.amp_lfo, instrument.pitch_lfo):
        if lfo is not None and _lfo_period_ms(lfo) is not None:
            interval = max(500 / lfo.frequency, 50)
            steady = max(steady, math.ceil(steady / interval - 1e-9) * interval)
    loop_start = steady + _MARGIN_MS + _CROSSFADE_MS

    minimum = _NOISE_LOOP_MS if noise else _MIN_LOOP_MS
    lfo_loop = _lfo_loop_ms(instrument.amp_lfo, instrument.pitch_lfo)
    loop = minimum if lfo_loop is None else lfo_loop * math.ceil(minimum / lfo_loop)

    search = 0.0
    frequency = _note_hz(midi_note)
    if not noise and frequency > 0:
        period = 1000 / frequency * _CYCLE_LENGTHS.get(instrument.waveform, 1)
        search = min(period, _MAX_SEARCH_MS)
    gate = loop_start + loop + search + _MARGIN_MS
    return _MelodicPlan(True, gate, loop_start, loop, search, noise)


def _note_hz(midi_note: int) -> float:
    index = midi_note - TABLE_LOWEST_MIDI
    return float(FREQUENCIES[index]) if 0 <= index < len(FREQUENCIES) else 0.0


def _lfo_period_ms(lfo: LFO | None) -> Fraction | None:
    """How often the simulator's sampled LFO repeats, in ms.

    `renderInstrument` samples an LFO every max(500 / f, 50) ms. Up to 10 Hz
    that is every half period (period 1000 / f); faster LFOs are sampled every
    50 ms, which repeats after 20 / gcd(f, 20) samples."""
    if lfo is None or not lfo.amplitude or lfo.frequency <= 0:
        return None
    frequency = int(lfo.frequency)
    if frequency <= 10:
        return Fraction(1000, frequency)
    return Fraction(50 * 20, math.gcd(frequency, 20))


def _lfo_loop_ms(*lfos: LFO | None) -> float | None:
    """A loop length that holds a whole number of every LFO's periods (or the
    longest period, if that would be over 4 s)."""
    periods = [p for p in (_lfo_period_ms(lfo) for lfo in lfos) if p is not None]
    if not periods:
        return None
    loop = periods[0]
    for period in periods[1:]:
        loop = Fraction(
            math.lcm(loop.numerator, period.numerator),
            math.gcd(loop.denominator, period.denominator),
        )
    if loop > _MAX_LFO_LOOP_MS:
        loop = max(periods)
    return float(loop)


def _render_melodic(
    np_: Any, instrument: Instrument, midi_note: int, gain: float, rate: int
) -> tuple[Any, tuple[int, int] | None]:
    """Float samples (scaled by `gain`) and the loop, if any."""
    plan = _plan_melodic(instrument, midi_note)
    buf = note_instructions(
        instrument, midi_note - TABLE_LOWEST_MIDI, plan.gate_ms, _FULL_VOLUME
    )
    samples = np_.asarray(synthesize([(0.0, buf)], rate), dtype=np_.float64) * gain
    if not plan.looped:
        return _trim_silence(np_, samples), None

    def points(ms: float) -> int:
        return round(ms * rate / 1000)

    start = points(plan.loop_start_ms)
    fade = max(1, points(_CROSSFADE_MS))
    nominal_end = start + max(points(plan.loop_ms), 32)
    search = points(plan.search_ms)
    end, correlation = _best_loop_end(np_, samples, start, nominal_end, search, fade)
    linear = not plan.noise and correlation >= _LINEAR_CROSSFADE_CORRELATION
    looped = _crossfade_loop(
        np_, samples[:end].copy(), samples, start, end, fade, linear
    )
    data = np_.concatenate([looped, samples[start : start + _POST_LOOP_POINTS]])
    return data, (start, end)


def _best_loop_end(
    np_: Any, samples: Any, start: int, nominal: int, search: int, fade: int
) -> tuple[int, float]:
    """The loop end within `search` points of `nominal` whose preceding `fade`
    points best match those before the loop start, and how well they match
    (normalised correlation, -1 to 1)."""
    template = samples[start - fade : start]
    low = max(nominal - search, start + fade)
    high = min(nominal + search, len(samples) - _POST_LOOP_POINTS)
    high = max(high, low)
    region = samples[low - fade : high]
    dots = np_.correlate(region, template, mode="valid")
    squares = np_.concatenate([[0.0], np_.cumsum(region * region)])
    energies = squares[fade:] - squares[:-fade]
    norm = math.sqrt(float(template @ template)) * np_.sqrt(np_.maximum(energies, 0))
    with np_.errstate(divide="ignore", invalid="ignore"):
        scores = np_.where(norm > 0, dots / norm, 0.0)
    best = int(np_.argmax(scores))
    return low + best, float(scores[best])


def _crossfade_loop(
    np_: Any, out: Any, samples: Any, start: int, end: int, fade: int, linear: bool
) -> Any:
    """Bake a crossfade into the loop's last `fade` points, from the sound
    there into the sound just before the loop start, so jumping back to the
    start continues smoothly."""
    ramp = (np_.arange(fade) + 0.5) / fade
    if linear:
        fade_in, fade_out = ramp, 1 - ramp
    else:
        fade_in, fade_out = np_.sin(ramp * np_.pi / 2), np_.cos(ramp * np_.pi / 2)
    out[end - fade : end] = (
        samples[end - fade : end] * fade_out + samples[start - fade : start] * fade_in
    )
    return out


def _render_sample(
    sound: Instrument | DrumInstrument,
    root: int | None,
    gain: float,
    options: SoundFontOptions,
) -> tuple[bytes, int | None, tuple[int, int] | None]:
    """One SoundFont sample (a melodic `sound` at MIDI note `root`, or a drum
    with `root` None), encoded: `Sf2Sample`'s data, vorbis_frames and loop.
    Runs in a worker process with `jobs`."""
    np_ = _numpy()
    if isinstance(sound, DrumInstrument):
        data, loop = _render_drum(np_, sound, gain, options.rate), None
    else:
        assert root is not None
        data, loop = _render_melodic(np_, sound, root, gain, options.rate)
    encoded, frames = _encode_sample(np_, data, options)
    return encoded, frames, loop


def _render_drum(np_: Any, drum: DrumInstrument, gain: float, rate: int) -> Any:
    buf = drum_instructions(drum, _FULL_VOLUME)
    samples = np_.asarray(synthesize([(0.0, buf)], rate), dtype=np_.float64) * gain
    return _trim_silence(np_, samples)


def _trim_silence(np_: Any, samples: Any) -> Any:
    """Drop the trailing points that round to 0 in 16-bit (a one-shot's
    silent release, for example)."""
    audible = np_.flatnonzero(np_.abs(samples) * _INT16_MAX >= 0.5)
    return samples[: int(audible[-1]) + 1 if len(audible) else 0]


def _encode_sample(
    np_: Any, samples: Any, options: SoundFontOptions
) -> tuple[bytes, int | None]:
    """The sample's data and `Sf2Sample.vorbis_frames`: 16-bit PCM (None), or
    Ogg Vorbis with `options.sf3`. Padded with silence to the shortest sample
    SF2 allows."""
    if len(samples) < _MIN_SAMPLE_POINTS:
        samples = np_.concatenate(
            [samples, np_.zeros(_MIN_SAMPLE_POINTS - len(samples))]
        )
    samples = np_.clip(samples, -1.0, 1.0)
    if options.sf3:
        return _encode(np_, samples, options.rate, "ogg", options.quality), len(samples)
    data: bytes = np_.round(samples * _INT16_MAX).astype("<i2").tobytes()
    return data, None


def _release_timecents(release_ms: float) -> int:
    seconds = release_ms * RELEASE_FACTOR / 1000
    if seconds <= 0:
        return _MIN_TIMECENTS
    return max(_MIN_TIMECENTS, min(_MAX_TIMECENTS, round(1200 * math.log2(seconds))))


def velocity_layers(count: int) -> list[tuple[int, int]]:
    """`count` velocity ranges covering 0-127, lowest first."""
    if not 1 <= count <= 127:
        raise ValueError(f"velocity layer count must be 1-127, got {count}")
    tops = [round(127 * (i + 1) / count) for i in range(count)]
    return [(0 if i == 0 else tops[i - 1] + 1, top) for i, top in enumerate(tops)]


def _melodic_gain(
    midi_note: int, k: float, layer_top: int, layers: int, volume: float
) -> float:
    """How much to scale a sample rendered at full sequencer volume so the SF2
    plays it (at velocity `layer_top`) as loud as Arcade does.

    With one layer, pitch compensation is applied unclamped, so loud low notes
    are louder than in Arcade (which clamps the compensated velocity at 127).
    With several, each layer uses Arcade's exact (rounded, clamped) velocity."""
    scale = pitch_compensation_scale(midi_note - TABLE_LOWEST_MIDI, k)
    if layers == 1:
        velocity = layer_top * scale
    else:
        velocity = round(min(max(layer_top * scale, 1), 127))
    # The SF2's velocity modulator scales the layer by velocity / 127
    return volume / _FULL_VOLUME * velocity / 128 * 127 / layer_top


def _key_groups(notes: Iterable[int], step: int) -> list[tuple[int, int, int]]:
    """(lowest, highest, rendered) keys: consecutive notes in groups of
    `step`, each played by the sample of its middle note."""
    keys = sorted(set(notes))
    runs: list[list[int]] = []
    for key in keys:
        if runs and key == runs[-1][-1] + 1:
            runs[-1].append(key)
        else:
            runs.append([key])
    groups = []
    for run in runs:
        for i in range(0, len(run), step):
            chunk = run[i : i + step]
            groups.append((chunk[0], chunk[-1], chunk[len(chunk) // 2]))
    return groups


def _arcade_instrument(instrument: Instrument) -> Instrument:
    """The instrument as Arcade gets it: through the song encoding (integers,
    clamped field widths)."""
    return decode_instrument(encode_instrument(instrument), 0)


def _arcade_drum(drum: DrumInstrument) -> DrumInstrument:
    decoded = decode_drum_instrument(encode_drum_instrument(drum), 0)
    return replace(decoded, name=drum.name)


@dataclass(frozen=True)
class SoundFontOptions:
    notes: Sequence[int] = DEFAULT_NOTES
    step: int = 1
    rate: int = DEFAULT_RATE
    velocity_layers: int = 1
    volume: float = MUSIC_DEFAULT_VOLUME  # music.volume(), 0-255
    # SF3: Ogg Vorbis samples (needs soundfile), at `quality` (0-1, default
    # `audio.DEFAULT_QUALITY`; see `audio.to_audio_bytes`)
    sf3: bool = False
    quality: float | None = None

    def check(self) -> None:
        if self.quality is not None and not 0.0 <= self.quality <= 1.0:
            raise ValueError(f"quality must be 0-1, got {self.quality}")
        if self.step < 1:
            raise ValueError(f"step must be at least 1, got {self.step}")
        if self.rate <= 0:
            raise ValueError(f"rate must be positive, got {self.rate}")
        velocity_layers(self.velocity_layers)
        bad = [n for n in self.notes if not 0 <= n <= 127]
        if bad:
            raise ValueError(f"MIDI notes must be 0-127, got {bad[0]}")
        if not self.notes:
            raise ValueError("no notes to render")


def estimate_size(
    params: ParamsInput = None, options: SoundFontOptions | None = None
) -> int:
    """An upper bound on the size in bytes of `build_soundfont`'s output as
    SF2 (sample data only; the rest is a few hundred KB at most). An SF3 is
    much smaller, by an amount that depends on the sounds."""
    options = options or SoundFontOptions()
    options.check()
    mapping = _resolve_params(params)
    points = 0
    groups = _key_groups(options.notes, options.step)
    for instrument in mapping.melodic_instruments.values():
        arcade = _arcade_instrument(instrument)
        for _low, _high, root in groups:
            ms = _plan_melodic(arcade, root).length_ms(arcade.amp_envelope.release)
            points += options.velocity_layers * (
                math.ceil(ms * options.rate / 1000) + _POST_LOOP_POINTS + _GUARD_POINTS
            )
    for drum in mapping.drum_instruments.values():
        ms = sum(step.duration for step in drum.steps) + 10
        points += math.ceil(ms * options.rate / 1000) + _GUARD_POINTS
    return 2 * points


def build_soundfont(
    params: ParamsInput = None,
    options: SoundFontOptions | None = None,
    *,
    name: str = "midi2mkcd Arcade instruments",
    comment: str | None = None,
    progress: ProgressCallback | None = None,
    jobs: int | None = 1,
) -> bytes:
    """
    Render every instrument in the params as an SF2 SoundFont.

    :param params: See `midi2mkcd.convert`. Programs and drum notes without an
     entry are left out.
    :param options: Which notes to render (`notes`, every `step`th one with the
     others pitch-shifted from it), the sample rate, the number of velocity
     layers, and the `music.volume()` to match.
    :param progress: Called as ``progress(done, total)`` with the number of
     samples rendered: once with 0, then after each.
    :param jobs: Worker processes to render (and for SF3, encode) samples in:
     1 (the default) does it all in this process, with no pool; None means one
     per CPU. Where processes aren't available (Pyodide) it is always 1. The
     SoundFont is the same for every `jobs`.
    :raises MissingDependencyError: If numpy isn't installed, or soundfile for
     `options.sf3`.
    :raises Midi2MkcdError: If the file would be over 4 GB.
    """
    options = options or SoundFontOptions()
    options.check()
    check_jobs(jobs)
    _numpy()
    if options.sf3:
        check_audio_format("ogg", options.rate, options.quality)
    mapping = _resolve_params(params)
    size = estimate_size(mapping, options)
    if options.sf3:
        logger.info(
            f"Uncompressed size would be up to {size / 1e6:.0f} MB of samples; "
            "the SF3 will be much smaller"
        )
    else:
        logger.info(
            f"Expected size: up to {size / 1e6:.0f} MB of samples "
            f"(--step {options.step}; a larger step renders fewer notes)"
        )
    if size > _MAX_RIFF_BYTES and not options.sf3:
        raise Midi2MkcdError(
            f"The SoundFont would be about {size / 1e9:.1f} GB, over RIFF's 4 GB "
            "limit; render fewer notes (a larger step or a smaller note range)"
        )
    outside = sorted({n for n in options.notes if _note_hz(n) == 0})
    if outside:
        logger.warning(
            f"{len(outside)} note(s) are outside the simulator's frequency table "
            "(MIDI 23-119) and play with no pitch, as in the simulator"
        )

    groups = _key_groups(options.notes, options.step)
    layers = velocity_layers(options.velocity_layers)
    k = mapping.melodic_pitch_comp_k
    total = len(mapping.melodic_instruments) * len(groups) * len(layers) + len(
        mapping.drum_instruments
    )
    done = 0
    if progress is not None:
        progress(0, total)

    def advance() -> None:
        nonlocal done
        done += 1
        if progress is not None:
            progress(done, total)

    # Plan every sample (name, root key, drum note, looped, what to render) and
    # the instruments first, so the format's limits are checked before any
    # rendering and the samples can be rendered in any order or in processes
    planned: list[tuple[str, int, int | None, bool, tuple[Any, ...]]] = []
    instruments: list[Sf2Instrument] = []
    presets: list[Sf2Preset] = []
    for program, instrument in sorted(mapping.melodic_instruments.items()):
        arcade = _arcade_instrument(instrument)
        release = _release_timecents(arcade.amp_envelope.release)
        pitchless = arcade.waveform in _PITCHLESS_WAVES
        zones = []
        for low, high, root in groups:
            looped = _plan_melodic(arcade, root).looped
            for vel_low, vel_high in layers:
                gain = _melodic_gain(root, k, vel_high, len(layers), options.volume)
                suffix = f" V{vel_high}" if len(layers) > 1 else ""
                zone = Sf2Zone(
                    sample=len(planned),
                    key_range=(low, high),
                    root_key=root,
                    vel_range=(vel_low, vel_high) if len(layers) > 1 else None,
                    release_timecents=release,
                    scale_tuning=0 if pitchless else None,
                )
                zones.append(zone)
                planned.append(
                    (
                        f"P{program:03d} N{root:03d}{suffix}",
                        root,
                        None,
                        looped,
                        (arcade, root, gain, options),
                    )
                )
        name_ = melodic_instrument_name(program)
        instruments.append(Sf2Instrument(name_, tuple(zones)))
        presets.append(Sf2Preset(name_, 0, program, len(instruments) - 1))

    drum_gain = options.volume / _FULL_VOLUME * 127 / 128
    drum_zones = []
    for note, drum in sorted(mapping.drum_instruments.items()):
        if not drum.steps:
            logger.warning(f"Drum note {note} has no steps; left out")
            advance()
            continue
        drum_zones.append(
            Sf2Zone(
                sample=len(planned),
                key_range=(note, note),
                root_key=note,
                release_timecents=_DRUM_RELEASE_TIMECENTS,
            )
        )
        task = (_arcade_drum(drum), None, drum_gain, options)
        planned.append((f"D{note:03d}", note, note, False, task))
    if drum_zones:
        instruments.append(Sf2Instrument("Arcade Drum Kit", tuple(drum_zones)))
        presets.append(Sf2Preset("Arcade Drum Kit", DRUM_BANK, 0, len(instruments) - 1))
    sample_info = [(root, looped) for _, root, _, looped, _ in planned]
    check_record_counts(
        (instrument_zone_records(i, sample_info) for i in instruments), len(planned)
    )

    rendered: list[tuple[bytes, int | None, tuple[int, int] | None] | None] = [
        None
    ] * len(planned)

    def on_done(index: int, result: tuple[bytes, int | None, Any]) -> None:
        rendered[index] = result
        drum_note = planned[index][2]
        if drum_note is not None:
            logger.debug(
                f"Rendered drum note {drum_note} ({drum_note_name(drum_note)})"
            )
        advance()

    tasks = [task for *_, task in planned]
    workers = resolve_jobs(jobs, len(tasks))
    if workers > 1:
        run_in_processes(_render_sample, tasks, workers, on_done)
    else:
        for index, task in enumerate(tasks):
            on_done(index, _render_sample(*task))

    samples: list[Sf2Sample] = []
    for (sample_name, root, *_), result in zip(planned, rendered, strict=True):
        assert result is not None
        encoded, frames, loop = result
        samples.append(
            Sf2Sample(sample_name, encoded, options.rate, root, loop, frames)
        )
    logger.info(f"{len(presets)} presets, {len(samples)} samples at {options.rate} Hz")
    data = write_sf2(name, samples, instruments, presets, comment=comment)
    if options.sf3:
        logger.info(f"SF3 size: {len(data) / 1e6:.1f} MB")
    return data
