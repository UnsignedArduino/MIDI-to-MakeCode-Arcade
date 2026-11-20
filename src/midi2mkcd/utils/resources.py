from importlib.resources import files


def _resource_text(name: str) -> str:
    return files("midi2mkcd").joinpath("resources", name).read_text(encoding="utf-8")


def default_instrument_params_text() -> str:
    """Return the bundled default instrument parameters YAML as text."""
    return _resource_text("instrument_params.yaml")


def blank_instrument_params_text() -> str:
    """
    Return the bundled blank instrument parameters template as YAML text. Its
    entries are incomplete, so it only loads with `allow_incomplete=True`.
    """
    return _resource_text("blank_instrument_params.yaml")
