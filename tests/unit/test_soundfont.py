"""`soundfont.py`: the SF2 writer (checked with the independent reader in
`helpers.read_sf2`) and the params -> samples rendering. How it sounds in a
player (release factor, velocity curve) is still to be measured by hand; see
RENDER_FEATURES_PLAN.md, section 5."""

import math
import sys
from fractions import Fraction
from typing import Any

import pytest
from helpers import Sf2File, Sf2Header, Sf2Zone, read_sf2

from midi2mkcd import soundfont
from midi2mkcd.arcade.music_types import (
    LFO,
    DrumInstrument,
    DrumSoundStep,
    Envelope,
    Instrument,
)
from midi2mkcd.audio import note_instructions, synthesize
from midi2mkcd.errors import MissingDependencyError
from midi2mkcd.midi_to_song.instruments import InstrumentParameterMapping
from midi2mkcd.soundfont import (
    Sf2Instrument,
    Sf2Preset,
    Sf2Sample,
    SoundFontOptions,
    build_soundfont,
    estimate_size,
    velocity_layers,
    write_sf2,
)

np = pytest.importorskip("numpy")

RATE = 22050
KEY_RANGE, VEL_RANGE, SAMPLE_ID, SAMPLE_MODES = 43, 44, 53, 54
ROOT_KEY, SCALE_TUNING, RELEASE, INSTRUMENT = 58, 56, 38, 41
FLAT = Envelope(attack=0, decay=0, sustain=1024, release=100, amplitude=1024)
PLUCK = Envelope(attack=5, decay=200, sustain=0, release=100, amplitude=1024)
SINE, NOISE, SQUARE = 3, 5, 15


def instrument(
    waveform: int = SINE,
    envelope: Envelope = FLAT,
    *,
    pitch_lfo: LFO | None = None,
) -> Instrument:
    return Instrument(
        waveform=waveform,
        octave=0,
        amp_envelope=envelope,
        pitch_envelope=Envelope(0, 0, 0, 0, 0),
        amp_lfo=LFO(0, 0),
        pitch_lfo=pitch_lfo or LFO(0, 0),
    )


KICK = DrumInstrument(
    start_frequency=120,
    start_volume=1024,
    steps=[DrumSoundStep(waveform=SINE, frequency=40, volume=0, duration=150)],
    name="kick",
)


def mapping(
    melodics: dict[int, Instrument],
    drums: dict[int, DrumInstrument] | None = None,
    k: float = 0,
) -> InstrumentParameterMapping:
    return InstrumentParameterMapping(k, melodics, drums or {})


def build(params: InstrumentParameterMapping, **options: object) -> Sf2File:
    opts = SoundFontOptions(rate=RATE, **options)  # type: ignore[arg-type]
    return read_sf2(build_soundfont(params, opts))


def zones_of(sf: Sf2File, program: int, bank: int = 0) -> list[Sf2Zone]:
    for _name, prog, bnk, (zone,) in sf.presets:
        if (prog, bnk) == (program, bank):
            index = zone.gen(INSTRUMENT)
            assert index is not None
            return sf.instruments[index][1]
    raise AssertionError(f"no preset {bank}:{program}")


def sample_of(sf: Sf2File, zone: Sf2Zone) -> Sf2Header:
    index = zone.gen(SAMPLE_ID)
    assert index is not None
    return sf.samples[index]


def floats(sf: Sf2File, header: Sf2Header) -> Any:
    return np.array(sf.points(header), dtype=np.float64) / 32767


# --- The writer -------------------------------------------------------------


def test_write_sf2_round_trips() -> None:
    pcm = np.arange(-50, 50, dtype="<i2").tobytes()
    samples = [
        Sf2Sample("looped", pcm, 44100, 60, loop=(10, 90)),
        Sf2Sample("one-shot", pcm[:64], 44100, 36),
    ]
    zones = (
        soundfont.Sf2Zone(0, (0, 64), 60, vel_range=(0, 63), release_timecents=-1200),
        soundfont.Sf2Zone(0, (65, 127), 60, scale_tuning=0),
    )
    instruments = [
        Sf2Instrument("Lead", zones),
        Sf2Instrument("Kit", (soundfont.Sf2Zone(1, (36, 36), 36),)),
    ]
    presets = [Sf2Preset("Lead", 0, 80, 0), Sf2Preset("Kit", 128, 0, 1)]
    sf = read_sf2(write_sf2("A name", samples, instruments, presets, comment="odd!"))

    assert sf.info["ifil"] == b"\x02\x00\x01\x00"
    assert sf.info["INAM"].rstrip(b"\0") == b"A name"
    assert sf.info["ICMT"].rstrip(b"\0") == b"odd!"
    assert [(n, p, b) for n, p, b, _ in sf.presets] == [
        ("Lead", 80, 0),
        ("Kit", 0, 128),
    ]
    assert [z.gen(INSTRUMENT) for *_, (z,) in sf.presets] == [0, 1]

    looped, one_shot = sf.samples
    assert (looped.start, looped.end) == (0, 100)
    assert (looped.loop_start, looped.loop_end) == (10, 90)
    # 46 zero points after every sample
    assert one_shot.start == 100 + 46 and one_shot.end == one_shot.start + 32
    assert sf.smpl[200 : 2 * one_shot.start] == bytes(92)
    assert len(sf.smpl) == 2 * (one_shot.end + 46)
    assert sf.points(looped) == list(range(-50, 50))
    assert (looped.rate, looped.original_pitch, looped.sample_type) == (44100, 60, 1)

    lead = sf.instruments[0][1]
    for zone in lead:
        ops = [op for op, _ in zone.generators]
        assert ops[0] == KEY_RANGE and ops[-1] == SAMPLE_ID
        assert zone.modulators == list(soundfont.ZONE_MODULATORS)
    assert lead[0].range(KEY_RANGE) == (0, 64)
    assert lead[0].range(VEL_RANGE) == (0, 63)
    assert lead[0].generators[1][0] == VEL_RANGE
    assert lead[0].signed(RELEASE) == -1200
    assert lead[0].gen(SAMPLE_MODES) == 1
    assert lead[1].gen(VEL_RANGE) is None
    assert lead[1].gen(SCALE_TUNING) == 0
    assert sf.instruments[1][1][0].gen(SAMPLE_MODES) == 0


def test_names_are_truncated_and_ascii() -> None:
    pcm = bytes(64)
    sf = read_sf2(
        write_sf2(
            "x",
            [Sf2Sample("é" * 30, pcm, 8000, 60)],
            [Sf2Instrument("A" * 25, (soundfont.Sf2Zone(0, (60, 60), 60),))],
            [Sf2Preset("B" * 25, 0, 0, 0)],
        )
    )
    assert sf.samples[0].name == "?" * 19
    assert sf.instruments[0][0] == "A" * 19
    assert sf.presets[0][0] == "B" * 19


def test_velocity_modulator_makes_amplitude_linear() -> None:
    # Overrides SF2's default velocity -> attenuation (concave, 960 cB) by
    # matching its source, destination and amount source
    assert soundfont.ZONE_MODULATORS[0] == (0x0502, 48, 480, 0, 0)


# --- Helpers ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("frequency", "period"),
    [(1, 1000), (3, Fraction(1000, 3)), (10, 100), (20, 50), (15, 200), (11, 1000)],
)
def test_lfo_period_follows_the_simulators_sampling(
    frequency: int, period: Fraction
) -> None:
    assert soundfont._lfo_period_ms(LFO(frequency, 5)) == period


def test_lfo_period_none_when_off() -> None:
    assert soundfont._lfo_period_ms(None) is None
    assert soundfont._lfo_period_ms(LFO(5, 0)) is None
    assert soundfont._lfo_period_ms(LFO(0, 5)) is None


def test_lfo_loop_holds_every_lfos_period() -> None:
    assert soundfont._lfo_loop_ms(LFO(2, 1), LFO(5, 1)) == 1000
    assert soundfont._lfo_loop_ms(LFO(3, 1), None) == pytest.approx(1000 / 3)
    assert soundfont._lfo_loop_ms(None, None) is None


def test_key_groups() -> None:
    assert soundfont._key_groups([60, 61, 62, 63, 64], 1) == [
        (n, n, n) for n in range(60, 65)
    ]
    assert soundfont._key_groups(range(60, 65), 3) == [(60, 62, 61), (63, 64, 64)]
    # Gaps start a new group, so no unrequested key is mapped
    assert soundfont._key_groups([60, 61, 70, 71, 72], 3) == [
        (60, 61, 61),
        (70, 72, 71),
    ]


def test_velocity_layers() -> None:
    assert velocity_layers(1) == [(0, 127)]
    assert velocity_layers(2) == [(0, 64), (65, 127)]
    assert velocity_layers(4)[-1] == (96, 127)
    with pytest.raises(ValueError):
        velocity_layers(0)


def test_release_timecents() -> None:
    assert soundfont._release_timecents(0) == -12000
    # 500 ms x 2 = 1 s
    assert soundfont._release_timecents(500) == 0
    assert soundfont._release_timecents(10**9) == 8000


@pytest.mark.parametrize(
    "options",
    [
        SoundFontOptions(step=0),
        SoundFontOptions(rate=0),
        SoundFontOptions(velocity_layers=128),
        SoundFontOptions(notes=[128]),
        SoundFontOptions(notes=[]),
    ],
)
def test_bad_options_raise(options: SoundFontOptions) -> None:
    with pytest.raises(ValueError):
        build_soundfont(mapping({0: instrument()}), options)


# --- Rendering --------------------------------------------------------------


def test_presets_cover_programs_and_drum_kit() -> None:
    sf = build(
        mapping({0: instrument(), 41: instrument()}, {36: KICK}),
        notes=range(60, 63),
    )
    assert [(p, b) for _, p, b, _ in sf.presets] == [(0, 0), (41, 0), (0, 128)]
    assert sf.presets[1][0] == "Viola"
    assert [z.range(KEY_RANGE) for z in zones_of(sf, 41)] == [
        (60, 60),
        (61, 61),
        (62, 62),
    ]
    (kick,) = zones_of(sf, 0, 128)
    assert kick.range(KEY_RANGE) == (36, 36) and kick.gen(SAMPLE_MODES) == 0


def test_sustained_note_matches_the_simulator_until_the_loop() -> None:
    sine = instrument(envelope=Envelope(10, 100, 512, 100, 1024))
    sf = build(mapping({0: sine}), notes=[60])
    (zone,) = zones_of(sf, 0)
    header = sample_of(sf, zone)
    assert zone.signed(ROOT_KEY) == 60 and header.original_pitch == 60
    assert zone.gen(SAMPLE_MODES) == 1

    start = header.loop_start - header.start
    end = header.loop_end - header.start
    data = floats(sf, header)
    gain = 128 / 1024 * 127 / 128
    expected = (
        np.asarray(synthesize([(0.0, note_instructions(sine, 37, 2000, 1024))], RATE))
        * gain
    )
    # Before the crossfade, the sample is what the simulator plays
    fade = round(0.02 * RATE)
    np.testing.assert_allclose(
        data[: end - fade], expected[: end - fade], atol=1.5 / 32767
    )
    # After the attack and decay, plus margin and crossfade
    assert start == round(0.150 * RATE)
    # Loop is ~100 ms, moved by at most one waveform period to line up
    assert abs((end - start) - 0.1 * RATE) <= RATE / 262 + 1
    # The loop wraps smoothly: no jump bigger than the sine's steepest step
    wrap = abs(data[end - 1] - data[start])
    steepest = 2 * math.pi * 262 / RATE * gain * 512 / 1024
    assert wrap <= 1.5 * steepest
    # Post-loop points copy the loop start
    np.testing.assert_array_equal(data[end : end + 8], data[start : start + 8])


def test_level_matches_arcade_at_velocity_127() -> None:
    sf = build(mapping({0: instrument()}), notes=[60])
    data = floats(sf, sample_of(sf, zones_of(sf, 0)[0]))
    # Full-amplitude sine at music.volume() 128, velocity 127
    assert float(np.abs(data).max()) == pytest.approx(128 / 1024 * 127 / 128, rel=0.01)


def test_volume_scales_the_samples() -> None:
    quiet = build(mapping({0: instrument()}), notes=[60], volume=64)
    data = floats(quiet, sample_of(quiet, zones_of(quiet, 0)[0]))
    assert float(np.abs(data).max()) == pytest.approx(64 / 1024 * 127 / 128, rel=0.01)


def test_pitch_compensation_is_a_per_sample_gain() -> None:
    sf = build(mapping({0: instrument()}, k=1), notes=[60, 72])
    low, high = (
        float(np.abs(floats(sf, sample_of(sf, z))).max()) for z in zones_of(sf, 0)
    )
    assert high / low == pytest.approx(0.5, rel=0.01)


def test_velocity_layers_use_arcades_clamped_velocity() -> None:
    # k=1, an octave below MIDI 60: compensation doubles the velocity, which
    # Arcade clamps at 127
    sf = build(mapping({0: instrument()}, k=1), notes=[48], velocity_layers=2)
    soft, loud = zones_of(sf, 0)
    assert soft.range(VEL_RANGE) == (0, 64) and loud.range(VEL_RANGE) == (65, 127)
    peak = [float(np.abs(floats(sf, sample_of(sf, z))).max()) for z in (soft, loud)]
    unit = 128 / 1024 / 128  # per Arcade velocity step
    # Soft layer: velocity 64 -> 128 -> clamped 127, then the SF2 modulator
    # scales by velocity / 127, so the sample is stored at 127 * 127 / 64
    assert peak[0] == pytest.approx(unit * 127 * 127 / 64, rel=0.01)
    assert peak[1] == pytest.approx(unit * 127, rel=0.01)


def test_pluck_is_a_one_shot() -> None:
    sf = build(mapping({0: instrument(envelope=PLUCK)}), notes=[60])
    (zone,) = zones_of(sf, 0)
    header = sample_of(sf, zone)
    assert zone.gen(SAMPLE_MODES) == 0
    # Attack + decay; the silent release is trimmed
    assert round(0.19 * RATE) <= header.end - header.start <= round(0.205 * RATE) + 1


def test_pitch_lfo_loop_is_whole_lfo_periods() -> None:
    vibrato = instrument(pitch_lfo=LFO(5, 20))
    sf = build(mapping({0: vibrato}), notes=[60])
    header = sample_of(sf, zones_of(sf, 0)[0])
    # 5 Hz: 200 ms period, already over the 100 ms minimum
    loop = header.loop_end - header.loop_start
    assert abs(loop - 0.2 * RATE) <= RATE / 262 + 1


def test_loop_starts_after_the_lfos_first_point_past_the_decay() -> None:
    # The simulator samples a 1 Hz LFO every 500 ms; the ramp from the end of
    # the 200 ms attack to the 500 ms point doesn't repeat
    surf = Instrument(
        waveform=NOISE,
        octave=0,
        amp_envelope=Envelope(200, 0, 900, 500, 256),
        amp_lfo=LFO(1, 300),
    )
    plan = soundfont._plan_melodic(surf, 60)
    assert plan.loop_start_ms == 500 + 20 + 20
    assert plan.loop_ms == 1000


def test_noise_loops_long_and_ignores_pitch() -> None:
    sf = build(mapping({0: instrument(NOISE)}), notes=range(60, 63), step=3)
    (zone,) = zones_of(sf, 0)
    assert zone.range(KEY_RANGE) == (60, 62) and zone.signed(ROOT_KEY) == 61
    assert zone.gen(SCALE_TUNING) == 0
    header = sample_of(sf, zone)
    assert header.loop_end - header.loop_start == round(0.5 * RATE)


def test_step_pitch_shifts_from_the_middle_note() -> None:
    sf = build(mapping({0: instrument(SQUARE)}), notes=range(60, 66), step=3)
    zones = zones_of(sf, 0)
    assert [(z.range(KEY_RANGE), z.signed(ROOT_KEY)) for z in zones] == [
        ((60, 62), 61),
        ((63, 65), 64),
    ]
    assert all(z.gen(SCALE_TUNING) is None for z in zones)


def test_release_follows_the_amp_envelope() -> None:
    sf = build(mapping({0: instrument()}, {36: KICK}), notes=[60])
    # 100 ms x 2 = 0.2 s
    assert zones_of(sf, 0)[0].signed(RELEASE) == round(1200 * math.log2(0.2))
    assert zones_of(sf, 0, 128)[0].signed(RELEASE) == 8000


def test_drum_sample_is_the_simulators_hit() -> None:
    sf = build(mapping({}, {36: KICK}), notes=[60])
    header = sample_of(sf, zones_of(sf, 0, 128)[0])
    # The 150 ms step, with the silent end trimmed
    assert round(0.14 * RATE) <= header.end - header.start <= round(0.15 * RATE) + 1
    assert header.name == "D036"


def test_out_of_table_notes_warn(caplog: pytest.LogCaptureFixture) -> None:
    build(mapping({0: instrument()}), notes=[22, 60])
    assert "1 note(s) are outside the simulator's frequency table" in caplog.text


def test_estimate_is_an_upper_bound() -> None:
    params = mapping(
        {0: instrument(), 1: instrument(envelope=PLUCK), 2: instrument(NOISE)},
        {36: KICK},
    )
    options = SoundFontOptions(notes=range(40, 80), step=2, rate=RATE)
    sf = read_sf2(build_soundfont(params, options))
    assert len(sf.smpl) <= estimate_size(params, options) <= 1.2 * len(sf.smpl)


def test_progress_counts_samples() -> None:
    calls: list[tuple[int, int]] = []
    build_soundfont(
        mapping({0: instrument()}, {36: KICK}),
        SoundFontOptions(notes=[60, 61], rate=8000),
        progress=lambda done, total: calls.append((done, total)),
    )
    assert calls == [(0, 3), (1, 3), (2, 3), (3, 3)]


def test_bundled_params_build() -> None:
    sf = read_sf2(build_soundfont(None, SoundFontOptions(notes=[60], rate=8000)))
    assert len(sf.presets) == 129
    assert len(zones_of(sf, 0, 128)) == 61


def test_without_numpy_is_a_clean_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "numpy", None)
    with pytest.raises(MissingDependencyError):
        build_soundfont(mapping({0: instrument()}), SoundFontOptions(notes=[60]))
