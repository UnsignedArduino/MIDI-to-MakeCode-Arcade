"""`utils/parallel.py` and the `jobs` option of `audio.synthesize` and
`soundfont.build_soundfont`: worker processes give the same result, and
jobs=1 (or no processes, as in Pyodide) never starts a pool."""

import os
import sys
from typing import Any

import pytest

from midi2mkcd import audio, soundfont
from midi2mkcd.arcade.music_types import (
    DrumInstrument,
    DrumSoundStep,
    Envelope,
    Instrument,
)
from midi2mkcd.audio import note_instructions, synthesize
from midi2mkcd.midi_to_song.instruments import InstrumentParameterMapping
from midi2mkcd.soundfont import SoundFontOptions, build_soundfont
from midi2mkcd.utils import parallel

np = pytest.importorskip("numpy")

RATE = 8000
ENVELOPE = Envelope(attack=10, decay=50, sustain=600, release=80, amplitude=1024)


def events() -> list[audio.SongEvent]:
    """Overlapping notes on several waveforms, out of time order."""
    result = []
    for i in range(40):
        instrument = Instrument(waveform=(1, 2, 3, 4, 15)[i % 5], octave=4,
                                amp_envelope=ENVELOPE)  # fmt: skip
        buf = note_instructions(instrument, 20 + i % 30, 150 + 10 * i, 127)
        result.append((float((i * 37) % 900), buf))
    return result


def params() -> InstrumentParameterMapping:
    kick = DrumInstrument(
        start_frequency=120,
        start_volume=1024,
        steps=[DrumSoundStep(waveform=3, frequency=40, volume=0, duration=150)],
        name="kick",
    )
    melodic = {p: Instrument(waveform=w, octave=4, amp_envelope=ENVELOPE)
               for p, w in ((0, 3), (1, 15), (2, 5))}  # fmt: skip
    return InstrumentParameterMapping(0, melodic, {36: kick, 38: kick})


@pytest.fixture
def no_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail if anything starts worker processes."""

    def fail(*args: Any) -> None:
        raise AssertionError("worker processes were started")

    monkeypatch.setattr(audio, "run_in_processes", fail)
    monkeypatch.setattr(soundfont, "run_in_processes", fail)


def test_resolve_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "process_cpu_count", lambda: 6)
    assert parallel.resolve_jobs(1, 100) == 1
    assert parallel.resolve_jobs(4, 100) == 4
    assert parallel.resolve_jobs(None, 100) == 6
    # Never more workers than tasks
    assert parallel.resolve_jobs(4, 2) == 2
    assert parallel.resolve_jobs(None, 0) == 1
    monkeypatch.setattr(os, "process_cpu_count", lambda: None)
    assert parallel.resolve_jobs(None, 100) == 1


@pytest.mark.parametrize("platform", ["emscripten", "wasi"])
def test_no_processes_in_pyodide(
    monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    assert not parallel.processes_available()
    assert parallel.resolve_jobs(None, 100) == 1
    assert parallel.resolve_jobs(8, 100) == 1


def test_no_processes_without_semaphores(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "multiprocessing.synchronize", None)
    assert not parallel.processes_available()


@pytest.mark.parametrize("jobs", [0, -1])
def test_bad_jobs(jobs: int) -> None:
    with pytest.raises(ValueError, match="jobs must be at least 1"):
        synthesize([], RATE, jobs=jobs)
    with pytest.raises(ValueError, match="jobs must be at least 1"):
        build_soundfont(params(), SoundFontOptions(notes=[60], rate=RATE), jobs=jobs)


def test_synthesize_in_processes_matches_serial() -> None:
    calls: list[tuple[int, int]] = []
    serial = synthesize(events(), RATE)
    pooled = synthesize(
        events(), RATE, jobs=2, progress=lambda d, t: calls.append((d, t))
    )
    assert pooled.dtype == serial.dtype and len(pooled) == len(serial)
    # Batches are mixed in another order than the notes: only float rounding
    np.testing.assert_allclose(pooled, serial, rtol=0, atol=1e-6)
    assert calls[0] == (0, 40) and calls[-1] == (40, 40)
    assert [d for d, _ in calls] == sorted(d for d, _ in calls)


def test_synthesize_jobs_1_uses_no_pool(no_pool: None) -> None:
    serial = synthesize(events(), RATE)
    assert synthesize(events(), RATE, jobs=1).tobytes() == serial.tobytes()


def test_synthesize_without_processes_is_serial(
    no_pool: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    serial = synthesize(events(), RATE)
    monkeypatch.setattr(sys, "platform", "emscripten")
    assert synthesize(events(), RATE, jobs=None).tobytes() == serial.tobytes()


def test_soundfont_in_processes_is_identical() -> None:
    options = SoundFontOptions(notes=range(58, 63), step=2, rate=RATE)
    calls: list[tuple[int, int]] = []
    serial = build_soundfont(params(), options)
    pooled = build_soundfont(
        params(), options, jobs=2, progress=lambda d, t: calls.append((d, t))
    )
    assert pooled == serial
    total = 3 * 3 + 2
    assert calls == [(d, total) for d in range(total + 1)]


def test_soundfont_jobs_1_uses_no_pool(no_pool: None) -> None:
    build_soundfont(params(), SoundFontOptions(notes=[60], rate=RATE), jobs=1)


def test_soundfont_without_processes_is_serial(
    no_pool: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "platform", "emscripten")
    build_soundfont(params(), SoundFontOptions(notes=[60], rate=RATE), jobs=None)
