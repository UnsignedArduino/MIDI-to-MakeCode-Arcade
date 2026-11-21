"""
Exceptions raised for problems with the caller's input.

Every exception here derives from `Midi2MkcdError`, so a front end can catch that one
type to tell "this input can't be converted" apart from a bug. Internal invariant
violations (messages ending in "please report") are raised as `RuntimeError` instead.
"""


class Midi2MkcdError(Exception):
    """Base class for all errors caused by the input given to midi2mkcd."""


class InvalidMidiError(Midi2MkcdError):
    """The MIDI data could not be parsed."""


class InstrumentParamsError(Midi2MkcdError):
    """
    The instrument parameters are invalid (bad YAML, missing or invalid fields), or
    the song uses an instrument or drum note that has no parameters.
    """


class SongConstraintError(Midi2MkcdError):
    """The song cannot fit MakeCode Arcade's song format (too many tracks, etc.)."""


class InvalidSongError(Midi2MkcdError):
    """
    A MakeCode Arcade song (or the text holding it) could not be read: no single
    ``hex`...` `` literal, bad hex, or a buffer that doesn't decode.
    """
