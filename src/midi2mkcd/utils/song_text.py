"""
Read a converted song back out of generated TypeScript text.

Shared by `recover-midi` (and later `render`): finds the single ``hex`...` ``
literal in a file, and, for `code_extra` output, the Song-Visualizer constants that
go with it.
"""

import json
import re
from dataclasses import dataclass, field

from midi2mkcd.errors import InvalidSongError

HEX_LITERAL = re.compile(r"hex`([^`]*)`")
# `const name: type = value;` on one line, as `render.to_typescript` writes them
CONST = re.compile(
    r"^\s*const\s+(?P<name>\w+)\s*(?::\s*[\w\[\]]+\s*)?=\s*(?P<value>.*?);?\s*$",
    re.MULTILINE,
)
EXTRA_CONSTS = ("midiDrumNoteMap", "trackInstrumentMap", "lyricTicks", "lyricTexts")


@dataclass(frozen=True)
class SongText:
    """A song's hex digits plus the `code_extra` constants, if the text had them."""

    # Bare hex digits, whitespace removed
    hex: str
    # True if the text is `code_extra` output (has `songHex` and `trackInstrumentMap`)
    is_code_extra: bool = False
    # The `code_extra` arrays; empty unless `is_code_extra`
    midi_drum_note_map: list[int] = field(default_factory=list)
    track_instrument_map: list[int] = field(default_factory=list)
    lyric_ticks: list[int] = field(default_factory=list)
    lyric_texts: list[str] = field(default_factory=list)


def parse_song_text(text: str) -> SongText:
    """
    Find the one song in generated TypeScript (any `-f` format of `convert`).

    :param text: The file's text.
    :return: The song's hex and, for `code_extra` text, its extra constants.
    :raises InvalidSongError: If the text has no ``hex`...` `` literal or more than
     one (e.g. `tune sample` output), the literal isn't hex, or a `code_extra`
     constant can't be parsed.
    """
    literals = HEX_LITERAL.findall(text)
    if len(literals) != 1:
        detail = "none" if not literals else str(len(literals))
        raise InvalidSongError(
            f"Expected exactly one hex`...` song literal, found {detail}"
        )
    digits = re.sub(r"\s+", "", literals[0])
    if not digits or len(digits) % 2 or re.fullmatch(r"[0-9A-Fa-f]*", digits) is None:
        raise InvalidSongError("The hex`...` literal isn't a valid hex string")

    consts = {m["name"]: m["value"] for m in CONST.finditer(text)}
    if "songHex" not in consts or "trackInstrumentMap" not in consts:
        return SongText(hex=digits)
    return SongText(
        hex=digits,
        is_code_extra=True,
        midi_drum_note_map=_int_array(consts, "midiDrumNoteMap"),
        track_instrument_map=_int_array(consts, "trackInstrumentMap"),
        lyric_ticks=_int_array(consts, "lyricTicks"),
        lyric_texts=_str_array(consts, "lyricTexts"),
    )


def _array(consts: dict[str, str], name: str) -> list[object]:
    """Parse a constant's array literal; a missing constant is an empty array."""
    if name not in consts:
        return []
    try:
        value = json.loads(consts[name])
    except json.JSONDecodeError as e:
        raise InvalidSongError(f"Could not parse {name}: {e}") from e
    if not isinstance(value, list):
        raise InvalidSongError(f"{name} is not an array")
    return value


def _int_array(consts: dict[str, str], name: str) -> list[int]:
    value = _array(consts, name)
    if not all(type(v) is int for v in value):
        raise InvalidSongError(f"{name} must only hold integers")
    return [v for v in value if isinstance(v, int)]


def _str_array(consts: dict[str, str], name: str) -> list[str]:
    value = _array(consts, name)
    if not all(isinstance(v, str) for v in value):
        raise InvalidSongError(f"{name} must only hold strings")
    return [v for v in value if isinstance(v, str)]
