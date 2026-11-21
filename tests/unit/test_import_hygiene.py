import subprocess
import sys

CHECK = """
import logging
import sys

import midi2mkcd

assert "argparse" not in sys.modules, "argparse was imported"
assert "pyperclip" not in sys.modules, "pyperclip was imported"
assert "midi2mkcd.tuning" not in sys.modules, "midi2mkcd.tuning was imported"
handlers = logging.getLogger("midi2mkcd").handlers
assert [type(h) for h in handlers] == [logging.NullHandler], handlers
"""


def test_importing_the_package_has_no_side_effects() -> None:
    result = subprocess.run(
        [sys.executable, "-c", CHECK], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
