"""`audio.to_audio_bytes` and friends: WAV with the stdlib, the compressed
formats through soundfile (the `compress` extra; those tests skip without it)."""

import io
import logging
import sys
from pathlib import Path
from typing import Any

import pytest

from midi2mkcd.audio import (
    AUDIO_FORMATS,
    _has_compression_level,
    audio_format_for,
    check_audio_format,
    to_audio_bytes,
    to_wav_bytes,
)
from midi2mkcd.errors import MissingDependencyError

np = pytest.importorskip("numpy")

RATE = 48000


def tone(seconds: float = 1.0, rate: int = RATE) -> Any:
    t = np.arange(round(seconds * rate)) / rate
    return (
        0.3 * np.sin(2 * np.pi * 440 * t) + 0.2 * np.sin(2 * np.pi * 660 * t)
    ).astype(np.float32)


def decode(data: bytes) -> tuple[Any, int]:
    sf = pytest.importorskip("soundfile")
    samples, rate = sf.read(io.BytesIO(data), dtype="float64")
    return samples, rate


@pytest.mark.parametrize(
    ("name", "fmt"),
    [
        ("song.wav", "wav"),
        ("SONG.FLAC", "flac"),
        ("a.b.ogg", "ogg"),
        (Path("dir") / "x.opus", "opus"),
        ("x.Mp3", "mp3"),
    ],
)
def test_audio_format_for(name: str | Path, fmt: str) -> None:
    assert audio_format_for(name) == fmt


@pytest.mark.parametrize("name", ["song", "song.aiff", "song.wav.txt", ".ogg"])
def test_audio_format_for_rejects_other_extensions(name: str) -> None:
    with pytest.raises(ValueError, match=r"\.wav, \.flac, \.ogg, \.opus, \.mp3"):
        audio_format_for(name)


@pytest.mark.parametrize(
    ("fmt", "rate", "quality", "message"),
    [
        ("aiff", RATE, None, "unknown audio format"),
        ("wav", RATE, 1.01, "quality must be 0-1"),
        ("ogg", RATE, -0.5, "quality must be 0-1"),
        ("opus", 44100, None, "opus can't be written at 44100 Hz"),
        ("mp3", 96000, None, "mp3 can't be written at 96000 Hz"),
    ],
)
def test_check_audio_format_rejects(
    fmt: str, rate: int, quality: float | None, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        check_audio_format(fmt, rate, quality)


def test_wav_is_to_wav_bytes(caplog: pytest.LogCaptureFixture) -> None:
    samples = tone(0.1)
    with caplog.at_level(logging.DEBUG, logger="midi2mkcd"):
        assert to_audio_bytes(samples, RATE, "wav", quality=0.3) == to_wav_bytes(
            samples, RATE
        )
    assert "Quality is ignored for WAV" in caplog.text


def test_wav_needs_no_soundfile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "soundfile", None)
    check_audio_format("wav")
    assert to_audio_bytes(tone(0.01)).startswith(b"RIFF")


@pytest.mark.parametrize("fmt", [f for f in AUDIO_FORMATS if f != "wav"])
def test_compressed_formats_need_soundfile(
    fmt: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "soundfile", None)
    with pytest.raises(MissingDependencyError, match=r"midi2mkcd\[compress\]"):
        check_audio_format(fmt)
    with pytest.raises(MissingDependencyError):
        to_audio_bytes(tone(0.01), RATE, fmt)


def test_flac_is_lossless() -> None:
    samples = tone()
    samples[:10] = [1.5, -2, 1, -1, 0.5, 0, 0, 0, 0, 0]  # clipped like WAV
    decoded, rate = decode(to_audio_bytes(samples, RATE, "flac"))
    wav, _ = decode(to_wav_bytes(samples, RATE))
    assert rate == RATE
    np.testing.assert_array_equal(decoded, wav)


@pytest.mark.parametrize(
    ("fmt", "rate"), [("ogg", 22050), ("ogg", RATE), ("opus", RATE), ("mp3", 44100)]
)
def test_lossy_formats_keep_length_and_shape(fmt: str, rate: int) -> None:
    samples = tone(1.0, rate)
    data = to_audio_bytes(samples, rate, fmt)
    decoded, decoded_rate = decode(data)
    assert decoded_rate == rate
    assert len(decoded) == len(samples)
    assert np.corrcoef(samples, decoded)[0, 1] > 0.99
    rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
    assert float(np.sqrt(np.mean(decoded**2))) == pytest.approx(rms, rel=0.05)
    assert len(data) < len(to_wav_bytes(samples, rate)) / 3


@pytest.mark.parametrize("fmt", ["ogg", "opus", "mp3"])
def test_quality_trades_size(fmt: str) -> None:
    sf = pytest.importorskip("soundfile")
    if not _has_compression_level(sf):
        pytest.skip(f"soundfile {sf.__version__} can't set a compression level")
    samples = tone(2.0)
    rate = 44100 if fmt == "mp3" else RATE
    sizes = [len(to_audio_bytes(samples, rate, fmt, quality=q)) for q in (0, 0.5, 1)]
    assert sizes[0] < sizes[1] < sizes[2]


@pytest.mark.parametrize("fmt", ["flac", "ogg", "opus", "mp3"])
def test_old_soundfile_without_compression_level(
    fmt: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """soundfile < 0.13 (Pyodide's 0.12.1) has no `compression_level`: encode
    with libsndfile's default, warning only when a quality was asked for."""
    sf = pytest.importorskip("soundfile")
    real_write = sf.write

    def old_write(
        file: Any,
        data: Any,
        samplerate: int,
        subtype: str | None = None,
        endian: str | None = None,
        format: str | None = None,
        closefd: bool = True,
    ) -> None:
        real_write(file, data, samplerate, subtype, endian, format, closefd)

    monkeypatch.setattr(sf, "write", old_write)
    rate = 44100 if fmt == "mp3" else RATE
    samples = tone(0.5, rate)
    with caplog.at_level(logging.WARNING, logger="midi2mkcd"):
        data = to_audio_bytes(samples, rate, fmt)
    assert "Quality is ignored" not in caplog.text
    decoded, decoded_rate = decode(data)
    assert decoded_rate == rate
    assert len(decoded) == len(samples)
    with caplog.at_level(logging.WARNING, logger="midi2mkcd"):
        with_quality = to_audio_bytes(samples, rate, fmt, quality=0.9)
    assert "Quality is ignored" in caplog.text
    # Same encoder settings (Ogg's random stream serial makes the bytes differ)
    np.testing.assert_array_equal(decode(with_quality)[0], decoded)


def test_clipping_warns_for_compressed_formats(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pytest.importorskip("soundfile")
    samples = tone(0.1) * 4
    with caplog.at_level(logging.WARNING, logger="midi2mkcd"):
        to_audio_bytes(samples, RATE, "ogg")
    assert "sample(s) clipped" in caplog.text
