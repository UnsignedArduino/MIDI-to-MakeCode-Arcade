"""Runs the CLI in-process and checks its output, exit statuses and errors."""

import re
import sys
import wave
from pathlib import Path

import pytest
from helpers import FIXTURE_MIDI, GOLDEN, read_sf2

from midi2mkcd import (
    __version__,
    convert,
    default_instrument_params_text,
    render,
    tuning,
)
from midi2mkcd.cli import _minutes_seconds, _render_summary, main
from midi2mkcd.song_to_midi import song_to_midi

INPUT = str(FIXTURE_MIDI)

GOLDEN_ARGS = {
    "hex.ts": ["convert", INPUT],
    "code.ts": ["convert", INPUT, "-f", "code"],
    "code_extra.ts": ["convert", INPUT, "-f", "code_extra"],
    "replace.ts": ["tune", "replace", INPUT, "--melodics", "5"],
    "sample_0-2.ts": ["tune", "sample", INPUT, "--melodics", "0-2"],
}


def run(argv: list[str]) -> int:
    """Run the CLI, turning argparse's SystemExit into an exit status."""
    try:
        return main(argv)
    except SystemExit as e:
        assert isinstance(e.code, int)
        return e.code


@pytest.mark.parametrize(
    ("golden", "argv"), GOLDEN_ARGS.items(), ids=GOLDEN_ARGS.keys()
)
def test_output_file_matches_golden(
    golden: str, argv: list[str], tmp_path: Path
) -> None:
    out = tmp_path / golden
    assert run([*argv, "-o", str(out)]) == 0
    expected = (GOLDEN / golden).read_text(encoding="utf-8")
    assert out.read_text(encoding="utf-8") == expected + "\n"


def test_stdout_is_only_the_code(capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["convert", INPUT, "-f", "code"]) == 0
    captured = capsys.readouterr()
    assert captured.out == (GOLDEN / "code.ts").read_text(encoding="utf-8") + "\n"
    assert "Reading MIDI file" in captured.err


def test_verbose_adds_debug_logging(capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["convert", INPUT, "-v"]) == 0
    assert " - DEBUG - " in capsys.readouterr().err


def test_params_file_is_read(tmp_path: Path) -> None:
    params = tmp_path / "params.yaml"
    params.write_text(default_instrument_params_text(), encoding="utf-8")
    out = tmp_path / "out.ts"
    assert run(["convert", INPUT, "-p", str(params), "-o", str(out)]) == 0
    expected = (GOLDEN / "hex.ts").read_text(encoding="utf-8")
    assert out.read_text(encoding="utf-8") == expected + "\n"


def test_allow_incomplete_skips_bad_entries(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blank = tmp_path / "blank.yaml"
    blank.write_text(tuning.blank_instrument_params_text(), encoding="utf-8")
    # Without it the blank file fails to load; with it, the report still runs
    assert run(["tune", "report", INPUT, "-p", str(blank)]) == 1
    assert "error: Invalid" in capsys.readouterr().err
    assert run(["tune", "report", INPUT, "-p", str(blank), "--allow-incomplete"]) == 0
    assert " NO" in capsys.readouterr().out


def test_tune_report_prints_the_table(capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["tune", "report", INPUT]) == 0
    expected = render.usage_report(tuning.report(FIXTURE_MIDI))
    assert capsys.readouterr().out == expected + "\n"
    assert "String Ensemble 1" in expected


def test_tune_sample_drums(capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["tune", "sample", INPUT, "--drums", "36,38"]) == 0
    assert capsys.readouterr().out == tuning.sample_drums(FIXTURE_MIDI, [36, 38]) + "\n"


def test_params_show(capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["params", "show"]) == 0
    assert capsys.readouterr().out == default_instrument_params_text() + "\n"
    assert run(["params", "show", "--blank"]) == 0
    assert capsys.readouterr().out == tuning.blank_instrument_params_text() + "\n"


def test_params_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["params", "check"]) == 0
    assert capsys.readouterr().out == ""
    blank = tmp_path / "blank.yaml"
    blank.write_text(tuning.blank_instrument_params_text(), encoding="utf-8")
    assert run(["params", "check", "-p", str(blank)]) == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines == tuning.check_params(tuning.blank_instrument_params_text())
    assert lines


def test_garbage_midi_is_a_clean_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    garbage = tmp_path / "garbage.mid"
    garbage.write_bytes(b"not a midi file")
    assert run(["convert", str(garbage)]) == 1
    err = capsys.readouterr().err
    assert "error: Could not parse MIDI file" in err
    assert "Traceback" not in err


def test_missing_input_is_a_clean_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(["convert", str(tmp_path / "missing.mid")]) == 1
    err = capsys.readouterr().err
    assert "error: [Errno 2]" in err
    assert "Traceback" not in err


def test_verbose_reraises_errors(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        main(["convert", str(tmp_path / "missing.mid"), "-v"])


@pytest.mark.parametrize(
    "argv",
    [
        ["-h"],
        ["convert", "-h"],
        ["tune", "-h"],
        ["tune", "replace", "-h"],
        ["tune", "sample", "-h"],
        ["tune", "report", "-h"],
        ["params", "-h"],
        ["params", "show", "-h"],
        ["params", "check", "-h"],
        ["params", "soundfont", "-h"],
        ["recover-midi", "-h"],
        ["render", "-h"],
    ],
    ids=" ".join,
)
def test_help_exits_zero(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert run(argv) == 0
    assert capsys.readouterr().out.startswith("usage: midi2mkcd")


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["--version"]) == 0
    assert capsys.readouterr().out == f"midi2mkcd {__version__}\n"


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["tune"],
        ["params"],
        [INPUT],
        ["tune", "replace", INPUT],
        ["tune", "sample", INPUT, "--melodics", "0-2", "--drums", "36"],
        ["tune", "sample", INPUT],
        ["tune", "sample", INPUT, "--melodics", "x"],
        ["params", "check", "-o", "x"],
        ["convert", INPUT, "-f", "wav"],
        ["recover-midi", INPUT],
        ["render", INPUT],
        ["render", INPUT, "-o", "x.wav", "--volume", "256"],
        ["render", INPUT, "-o", "x.wav", "--rate", "0"],
        ["params", "soundfont"],
        ["params", "soundfont", "-o", "x.sf2", "--velocity-layers", "128"],
        ["params", "soundfont", "-o", "x.sf2", "--step", "0"],
        ["params", "soundfont", "-o", "x.sf2", "--notes", "x"],
        ["tune", "replace", INPUT, "--melodics", "5", "--wav", "x.wav", "-o", "x"],
        ["tune", "replace", INPUT, "--melodics", "5", "--wav", "x.wav", "--copy"],
        ["tune", "replace", INPUT, "--melodics", "5", "--wav", "x.wav", "-f", "hex"],
        ["tune", "sample", INPUT, "--melodics", "0", "--wav", "x.wav", "-o", "x"],
        ["tune", "sample", INPUT, "--melodics", "0", "--wav", "x.wav", "--copy"],
    ],
    ids=lambda argv: " ".join(Path(a).name for a in argv) or "bare",
)
def test_usage_errors_exit_two(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(argv) == 2
    assert "error:" in capsys.readouterr().err


def test_replace_error_names_the_options(capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["tune", "replace", INPUT]) == 2
    assert "give --melodics, --drums or both" in capsys.readouterr().err


def test_wav_error_names_the_clashing_options(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    wav = tmp_path / "x.wav"
    argv = ["tune", "replace", INPUT, "--drums", "36", "--wav", str(wav)]
    assert run([*argv, "-o", "x", "--copy", "-f", "code"]) == 2
    assert (
        "--wav can't be used with -o/--output, --copy, -f/--format"
        in capsys.readouterr().err
    )
    assert not wav.exists()


def test_recover_midi_writes_a_midi_file(tmp_path: Path) -> None:
    song = tmp_path / "song.ts"
    assert run(["convert", INPUT, "-f", "code_extra", "-o", str(song)]) == 0
    out = tmp_path / "recovered.mid"
    assert run(["recover-midi", str(song), "-o", str(out)]) == 0
    assert out.read_bytes() == song_to_midi(song.read_text(encoding="utf-8"))
    assert out.read_bytes().startswith(b"MThd")


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"// no song", "found none"),
        (b"hex`00` hex`01`", "found 2"),
        (b"\xff\xfe\x00garbage", "not a UTF-8 text file"),
    ],
    ids=["no literal", "two literals", "binary"],
)
def test_recover_midi_bad_input_is_a_clean_error(
    content: bytes, message: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    song = tmp_path / "song.ts"
    song.write_bytes(content)
    assert run(["recover-midi", str(song), "-o", str(tmp_path / "out.mid")]) == 1
    err = capsys.readouterr().err
    assert message in err
    assert "Traceback" not in err
    assert not (tmp_path / "out.mid").exists()


def _wav_frames(path: Path) -> tuple[int, int, bytes]:
    with wave.open(str(path)) as w:
        return w.getnchannels(), w.getframerate(), w.readframes(w.getnframes())


def test_render_midi_and_song_text_give_the_same_wav(tmp_path: Path) -> None:
    pytest.importorskip("numpy")
    from_midi = tmp_path / "midi.wav"
    assert run(["render", INPUT, "-o", str(from_midi), "--rate", "22050"]) == 0
    channels, rate, frames = _wav_frames(from_midi)
    assert (channels, rate) == (1, 22050)
    assert len(frames) > 2 * 22050 and any(frames)

    # Content decides the type: song text in a .mid file works
    song = tmp_path / "song.mid"
    song.write_text(convert(FIXTURE_MIDI).to_typescript("code"), encoding="utf-8")
    from_text = tmp_path / "text.wav"
    assert run(["render", str(song), "-o", str(from_text), "--rate", "22050"]) == 0
    assert _wav_frames(from_text) == (channels, rate, frames)


def test_render_options_change_the_output(tmp_path: Path) -> None:
    pytest.importorskip("numpy")
    outputs: list[bytes] = []
    for extra in ([], ["--volume", "255"], ["--no-velocity"]):
        out = tmp_path / f"{len(outputs)}.wav"
        assert run(["render", INPUT, "-o", str(out), "--rate", "8000", *extra]) == 0
        outputs.append(_wav_frames(out)[2])
    assert len(set(outputs)) == 3


@pytest.mark.parametrize(
    ("content", "params", "message"),
    [
        (b"RIFF\x00\x00\x00\x00RMIDdata", False, "RIFF MIDI"),
        (b"// no song", False, "found none"),
        (b"\xff\xfe\x00garbage", False, "neither a MIDI file nor"),
        (b"hex`0000`", True, "-p only applies to MIDI input"),
    ],
    ids=["rmi", "no literal", "binary", "text with -p"],
)
def test_render_bad_input_is_a_clean_error(
    content: bytes,
    params: bool,
    message: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    song = tmp_path / "input"
    song.write_bytes(content)
    argv = ["render", str(song), "-o", str(tmp_path / "out.wav")]
    if params:
        params_file = tmp_path / "params.yaml"
        params_file.write_text(default_instrument_params_text(), encoding="utf-8")
        argv += ["-p", str(params_file)]
    assert run(argv) == 1
    err = capsys.readouterr().err
    assert message in err
    assert "Traceback" not in err
    assert not (tmp_path / "out.wav").exists()


def test_render_progress_bar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    pytest.importorskip("numpy")
    tqdm = pytest.importorskip("tqdm")
    # Captured stderr isn't a terminal, so force the bar on to see it
    real_tqdm = tqdm.tqdm
    monkeypatch.setattr(
        tqdm, "tqdm", lambda **kwargs: real_tqdm(**{**kwargs, "disable": False})
    )
    out = tmp_path / "out.wav"
    assert run(["render", INPUT, "-o", str(out), "--rate", "8000"]) == 0
    err = capsys.readouterr().err
    assert "Rendering: 100%" in err
    assert " sound/s" in err or "sound/s]" in err


def test_render_logs_its_speed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pytest.importorskip("numpy")
    out = tmp_path / "out.wav"
    assert run(["render", INPUT, "-o", str(out), "--rate", "8000"]) == 0
    assert re.search(
        r"Rendered \d+\.\d\d s at 8000 Hz in \d+\.\d\d s \(speed=\d+\.\dx\)",
        capsys.readouterr().err,
    )
    assert _render_summary(217.68, 48000, 13.0) == (
        "Rendered 217.68 s at 48000 Hz in 13.00 s (speed=16.7x)"
    )
    assert _render_summary(1.0, 48000, 0.0) == "Rendered 1.00 s at 48000 Hz in 0.00 s"


def test_render_without_tqdm_still_works(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("numpy")
    monkeypatch.setitem(sys.modules, "tqdm", None)
    out = tmp_path / "out.wav"
    assert run(["render", INPUT, "-o", str(out), "--rate", "8000"]) == 0
    assert out.read_bytes().startswith(b"RIFF")


def test_tune_replace_wav_matches_rendering_its_code(tmp_path: Path) -> None:
    pytest.importorskip("numpy")
    code = tmp_path / "replace.ts"
    assert run(["tune", "replace", INPUT, "--melodics", "5", "-o", str(code)]) == 0
    from_code = tmp_path / "code.wav"
    assert run(["render", str(code), "-o", str(from_code)]) == 0
    direct = tmp_path / "direct.wav"
    assert run(["tune", "replace", INPUT, "--melodics", "5", "--wav", str(direct)]) == 0
    assert _wav_frames(direct) == _wav_frames(from_code)
    assert any(_wav_frames(direct)[2])


def test_tune_sample_wav_plays_each_instrument_in_turn(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pytest.importorskip("numpy")
    singles = []
    for program in (0, 40):
        out = tmp_path / f"{program}.wav"
        argv = ["tune", "replace", INPUT, "--melodics", str(program), "--wav", str(out)]
        assert run(argv) == 0
        singles.append(_wav_frames(out))
    capsys.readouterr()

    out = tmp_path / "sample.wav"
    assert run(["tune", "sample", INPUT, "--melodics", "0,40", "--wav", str(out)]) == 0
    err = capsys.readouterr().err
    channels, rate, frames = _wav_frames(out)
    assert (channels, rate) == (1, 48000)
    # The first song, 1 s of silence, then the second song
    first, second = singles[0][2], singles[1][2]
    gap = b"\x00\x00" * rate
    assert frames == first + gap + second

    seconds = len(first) // 2 // rate + 1
    assert " - INFO - 0:00 program 0 (Acoustic Grand Piano)" in err
    assert f" - INFO - 0:{seconds:02d} program 40 (Violin)" in err


def test_tune_sample_drums_wav_logs_drum_names(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pytest.importorskip("numpy")
    out = tmp_path / "drums.wav"
    assert run(["tune", "sample", INPUT, "--drums", "36", "--wav", str(out)]) == 0
    assert "0:00 drum note 36 (Bass Drum 1)" in capsys.readouterr().err
    assert out.read_bytes().startswith(b"RIFF")


def test_minutes_seconds() -> None:
    assert _minutes_seconds(0) == "0:00"
    assert _minutes_seconds(72_999.9) == "1:12"
    assert _minutes_seconds(3_600_000) == "60:00"


def test_render_without_numpy_is_a_clean_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setitem(sys.modules, "numpy", None)
    assert run(["render", INPUT, "-o", str(tmp_path / "out.wav")]) == 1
    assert 'needs numpy: pip install "midi2mkcd[audio]"' in capsys.readouterr().err


def test_params_soundfont_writes_an_sf2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "arcade.sf2"
    argv = ["params", "soundfont", "-o", str(out), "--notes", "60-61", "--step", "2"]
    assert run([*argv, "--rate", "8000"]) == 0
    sf = read_sf2(out.read_bytes())
    assert len(sf.presets) == 129
    assert len(sf.samples) == 128 + 61
    assert sf.samples[0].rate == 8000
    assert b"bundled params" in sf.info["ICMT"]
    err = capsys.readouterr().err
    assert "Expected size: up to" in err and "--step 2" in err


def test_params_soundfont_with_incomplete_params(tmp_path: Path) -> None:
    params = tmp_path / "partial.yaml"
    params.write_text(
        "melodic_instruments:\n"
        "  - instrument: 5\n"
        "    waveform: sine\n"
        "  - instrument: 7\n"
        "    waveform: square\n"
        "    amp_envelope: { attack: 5, decay: 50, sustain: 500, release: 50, "
        "amplitude: 512 }\n"
        "    pitch_envelope: { attack: 0, decay: 0, sustain: 0, release: 0, "
        "amplitude: 0 }\n"
        "    amp_lfo: { frequency: 0, amplitude: 0 }\n"
        "    pitch_lfo: { frequency: 0, amplitude: 0 }\n"
        "drum_instruments: []\n",
        encoding="utf-8",
    )
    out = tmp_path / "partial.sf2"
    argv = ["params", "soundfont", "-p", str(params), "-o", str(out), "--notes", "60"]
    assert run(argv) == 1
    assert run([*argv, "--allow-incomplete"]) == 0
    sf = read_sf2(out.read_bytes())
    assert [(name, program, bank) for name, program, bank, _ in sf.presets] == [
        ("Clavinet", 7, 0)
    ]
    assert b"partial.yaml" in sf.info["ICMT"]
