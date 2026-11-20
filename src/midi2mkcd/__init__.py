import logging

__version__ = "0.2.0"

# Library code only logs; the host application decides where records go.
logging.getLogger(__name__).addHandler(logging.NullHandler())

from midi2mkcd.api import (
    SIMULATOR_NOTE_WARNING_THRESHOLD,
    ConversionResult,
    OutputFormat,
    convert,
    default_instrument_params_text,
    load_instrument_params,
)
from midi2mkcd.errors import (
    InstrumentParamsError,
    InvalidMidiError,
    Midi2MkcdError,
    SongConstraintError,
)

__all__ = [
    "SIMULATOR_NOTE_WARNING_THRESHOLD",
    "ConversionResult",
    "InstrumentParamsError",
    "InvalidMidiError",
    "Midi2MkcdError",
    "OutputFormat",
    "SongConstraintError",
    "__version__",
    "convert",
    "default_instrument_params_text",
    "load_instrument_params",
]
