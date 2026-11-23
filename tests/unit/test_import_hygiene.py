import subprocess
import sys

CHECK = """
import logging
import sys

import midi2mkcd

assert "argparse" not in sys.modules, "argparse was imported"
assert "pyperclip" not in sys.modules, "pyperclip was imported"
assert "midi2mkcd.tuning" not in sys.modules, "midi2mkcd.tuning was imported"
assert "midi2mkcd.audio" not in sys.modules, "midi2mkcd.audio was imported"
handlers = logging.getLogger("midi2mkcd").handlers
assert [type(h) for h in handlers] == [logging.NullHandler], handlers
"""


# Stage 1 of audio.py must work without the `audio` extra: numpy (and the
# fit extra's scipy/cma, and the compress extra's soundfile) are only imported
# inside the functions that need them.
AUDIO_CHECK = """
import sys

import midi2mkcd
import midi2mkcd.audio
import midi2mkcd.cli
import midi2mkcd.soundfont

for name in ("numpy", "scipy", "cma", "tqdm", "soundfile"):
    assert name not in sys.modules, f"{name} was imported"
"""


def run_check(code: str) -> None:
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_importing_the_package_has_no_side_effects() -> None:
    run_check(CHECK)


def test_importing_audio_does_not_import_optional_dependencies() -> None:
    run_check(AUDIO_CHECK)
