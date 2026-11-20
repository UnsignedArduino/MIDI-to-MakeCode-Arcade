from importlib.resources import files


def default_instrument_params_text() -> str:
    """Return the bundled default instrument parameters YAML as text."""
    ref = files("midi2mkcd").joinpath("resources", "instrument_params.yaml")
    return ref.read_text(encoding="utf-8")
