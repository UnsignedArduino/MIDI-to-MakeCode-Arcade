"""Sanity checks for audio.py's Stage 2 (instructions -> samples). How close it
sounds to the simulator is checked by hand against a recording (see
RENDER_FEATURES_PLAN.md, section 4, Validation 2)."""

import io
import logging
import math
import struct
import sys
import wave
from dataclasses import replace

import pytest

from midi2mkcd import audio
from midi2mkcd.arcade.music_types import (
    DrumInstrument,
    DrumSoundStep,
    EnharmonicSpelling,
    Envelope,
    Instrument,
    Note,
    NoteEvent,
    Song,
    Track,
)
from midi2mkcd.audio import (
    note_instructions,
    render_song,
    synthesize,
    to_wav_bytes,
)
from midi2mkcd.errors import MissingDependencyError

np = pytest.importorskip("numpy")

RATE = 48000
FLAT = Envelope(attack=0, decay=0, sustain=1024, release=0, amplitude=1024)
C4 = 37  # Arcade note index of MIDI 60 (262 Hz in the simulator's table)


def flat(waveform: int) -> Instrument:
    return Instrument(waveform=waveform, octave=4, amp_envelope=FLAT)


def instruction(
    wave: int, hz: int, ms: int, v0: int, v1: int, end_hz: int | None = None
) -> bytes:
    return struct.pack(
        "<BBHHHHH", wave, 0, hz, ms, v0, v1, hz if end_hz is None else end_hz
    )


def rms_db(x: object) -> float:
    a = np.asarray(x, dtype=np.float64)
    return float(20 * np.log10(np.sqrt(np.mean(a**2))))


def peak_hz(x: object) -> float:
    a = np.asarray(x, dtype=np.float64)
    spectrum = np.abs(np.fft.rfft(a * np.hanning(len(a))))
    return float(np.fft.rfftfreq(len(a), 1 / RATE)[np.argmax(spectrum)])


def test_sine_level_and_frequency() -> None:
    samples = synthesize([(0.0, note_instructions(flat(3), C4, 500, 127))], RATE)
    steady = samples[int(0.05 * RATE) : int(0.45 * RATE)]
    # Measured in the simulator: -21.18 dBFS, i.e. (127 * 255 >> 6) / 1024 / 4 / sqrt 2
    expected = 20 * math.log10(((127 * 255) >> 6) / 1024 / 4 / math.sqrt(2))
    assert rms_db(steady) == pytest.approx(expected, abs=0.02)
    assert peak_hz(steady) == pytest.approx(262, abs=3)
    assert samples.dtype == np.float32


def test_squares_are_halved() -> None:
    sine = synthesize([(0.0, note_instructions(flat(3), C4, 500, 127))], RATE)
    square = synthesize([(0.0, note_instructions(flat(15), C4, 500, 127))], RATE)
    window = slice(int(0.05 * RATE), int(0.45 * RATE))
    # A +-1 square has rms 1 (sine: 1 / sqrt 2), then halved: -3 dB
    assert rms_db(square[window]) - rms_db(sine[window]) == pytest.approx(
        -3.01, abs=0.1
    )


@pytest.mark.parametrize("waveform", [1, 2])
def test_band_limited_oscillators(waveform: int) -> None:
    samples = synthesize([(0.0, note_instructions(flat(waveform), C4, 500, 127))], RATE)
    steady = samples[int(0.05 * RATE) : int(0.45 * RATE)]
    assert peak_hz(steady) == pytest.approx(262, abs=3)
    spectrum = np.abs(np.fft.rfft(steady * np.hanning(len(steady))))
    freqs = np.fft.rfftfreq(len(steady), 1 / RATE)
    # Nothing above Nyquist folds back below the fundamental's 20th harmonic
    assert spectrum[freqs > 23000].max() < spectrum.max() * 1e-3


def test_silence_before_the_first_note_and_length() -> None:
    buf = note_instructions(flat(3), C4, 500, 127)
    samples = synthesize([(1000.0, buf)], RATE)
    total_ms = sum(
        struct.unpack_from("<H", buf, i + 4)[0] for i in range(0, len(buf), 12)
    )
    assert len(samples) == math.ceil((1000 + total_ms) * RATE / 1000)
    assert not samples[:RATE].any()
    assert samples[RATE + 100 :].any()


def test_sounds_are_summed() -> None:
    buf = note_instructions(flat(3), C4, 500, 127)
    one = synthesize([(0.0, buf)], RATE)
    two = synthesize([(0.0, buf), (0.0, buf)], RATE)
    np.testing.assert_allclose(two, 2 * one, atol=1e-6)


def test_note_stops_at_the_end_of_its_instructions() -> None:
    # One 100 ms instruction that stays loud: the simulator stops the note anyway
    samples = synthesize([(0.0, instruction(3, 440, 100, 4080, 4080))], RATE)
    assert len(samples) == RATE // 10


def test_a_rest_keeps_the_last_level() -> None:
    buf = instruction(3, 440, 50, 4080, 4080) + instruction(0, 0, 50, 0, 0)
    samples = synthesize([(0.0, buf)], RATE)
    assert rms_db(samples[int(0.06 * RATE) : int(0.1 * RATE)]) == pytest.approx(
        rms_db(samples[int(0.01 * RATE) : int(0.05 * RATE)]), abs=0.1
    )


def test_waveform_change_fades_the_previous_waveform() -> None:
    # Sine at full level, then a silent square: the sine decays with a 15 ms
    # time constant instead of stopping
    buf = instruction(3, 440, 50, 4080, 4080) + instruction(15, 440, 100, 0, 0)
    samples = synthesize([(0.0, buf)], RATE)

    def level(ms: float) -> float:
        i = int(ms * RATE / 1000)
        return float(np.abs(samples[i : i + 110]).max())  # ~1 cycle of 440 Hz

    assert level(50) == pytest.approx(level(40), rel=0.05)
    assert level(65) == pytest.approx(level(40) * math.exp(-15 / 15), rel=0.1)
    assert level(140) < level(40) * 0.01


def test_out_of_range_notes() -> None:
    # 0 Hz: a sine is silent, a square is a DC offset
    assert not synthesize([(0.0, note_instructions(flat(3), 97, 200, 127))], RATE).any()
    square = synthesize([(0.0, note_instructions(flat(15), 97, 200, 127))], RATE)
    steady = square[int(0.05 * RATE) : int(0.15 * RATE)]
    assert np.all(steady > 0)
    assert np.ptp(steady) == pytest.approx(0, abs=1e-6)


def test_noise_ignores_frequency() -> None:
    low = synthesize([(0.0, note_instructions(flat(5), 10, 200, 127))], RATE)
    high = synthesize([(0.0, note_instructions(flat(5), 80, 200, 127))], RATE)
    np.testing.assert_array_equal(low, high)


def test_buffers_match_the_simulator() -> None:
    # Hand-checked against playInstructions.ts
    square_10 = audio._buffer(11)
    assert int((square_10 == 1).sum()) == 103  # i < 102.4
    noise = audio._buffer(5)
    assert len(noise) == 100000
    # The first xorshift value from 0xf01ba80, computed with JS int32 semantics
    x = 0xF01BA80
    x = audio._int32(x ^ (x << 13))
    x = audio._int32(x ^ (x >> 17))
    x = audio._int32(x ^ (x << 5))
    assert noise[0] == pytest.approx((x & 1023) / 512 - 1)
    assert len(audio._buffer(4)) == 131072
    assert set(np.unique(audio._buffer(16))) <= {-1.0, 0.0, 1.0}


def test_drum_is_shorter_than_a_held_note() -> None:
    drum = DrumInstrument(100, 1024, [DrumSoundStep(5, 200, 0, 30)])
    note = Note(C4, EnharmonicSpelling.NORMAL)
    song = Song(
        measures=1,
        beats_per_measure=4,
        beats_per_minute=120,
        ticks_per_beat=24,
        tracks=[
            Track(0, flat(3), [NoteEvent([note], 0, 48)]),
            Track(
                1,
                flat(3),
                [NoteEvent([Note(0, EnharmonicSpelling.NORMAL)], 0, 1)],
                drums=[drum],
            ),
        ],
    )
    samples = render_song(song, RATE)
    drum_only = render_song(replace(song, tracks=song.tracks[1:]), RATE)
    assert len(drum_only) < RATE * 0.05 < RATE * 0.9 < len(samples)


def test_wav_header_and_clipping(caplog: pytest.LogCaptureFixture) -> None:
    samples = np.array([0.0, 0.5, -0.5, 2.0, -3.0], dtype=np.float32)
    with caplog.at_level(logging.WARNING, logger="midi2mkcd.audio"):
        data = to_wav_bytes(samples, 22050)
    assert "2 sample(s) clipped (peak 3.000" in caplog.text
    with wave.open(io.BytesIO(data)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 22050)
        frames = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    assert frames.tolist() == [0, 16384, -16384, 32767, -32767]


def test_loud_chord_clips(caplog: pytest.LogCaptureFixture) -> None:
    buf = note_instructions(flat(3), C4, 200, 1024)
    with caplog.at_level(logging.WARNING, logger="midi2mkcd.audio"):
        to_wav_bytes(synthesize([(0.0, buf)] * 8, RATE), RATE)
    assert "clipped" in caplog.text


def test_progress_is_reported_per_sound() -> None:
    buf = note_instructions(flat(3), C4, 50, 127)
    calls: list[tuple[int, int]] = []
    synthesize(
        [(0.0, buf)] * 3, RATE, progress=lambda done, total: calls.append((done, total))
    )
    assert calls == [(0, 3), (1, 3), (2, 3), (3, 3)]


def test_missing_numpy_is_a_clean_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "numpy", None)
    with pytest.raises(MissingDependencyError, match=r"midi2mkcd\[audio\]"):
        synthesize([], RATE)
