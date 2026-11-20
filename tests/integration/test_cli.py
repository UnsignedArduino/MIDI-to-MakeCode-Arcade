"""Runs the CLI in-process and checks its output, exit statuses and errors."""

from pathlib import Path

import pytest
from helpers import FIXTURE_MIDI, GOLDEN

from midi2mkcd import __version__, default_instrument_params_text, render, tuning
from midi2mkcd.cli import main

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
