# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

CLI tool (`midi2mkcd`) that converts a MIDI file into a MakeCode Arcade song: a hex-encoded buffer you paste into Arcade TypeScript as `hex\`...\``. It supports GM1 melodic instruments (0-127), the GM1 standard drum kit, the GM2 extended drum range (27-87, standard kit only), and Roland GS/Yamaha XG SysEx drum-track switching. Requires Python >= 3.14 and `uv`.

## Commands

```bash
uv sync                                   # create venv + install deps (incl. dev group)
uv run python -m midi2mkcd -i song.mid -p examples/instrument_params.yaml            # print buffer to stdout
uv run python -m midi2mkcd -i song.mid -p examples/instrument_params.yaml -o out.ts  # write (overwrites)
uv run python -m midi2mkcd ... --generate-extra-code   # extra TS for the Song-Visualizer project
uv run python -m midi2mkcd ... --debug                 # debug logging
uv run ruff check .
uv run ruff format .
uv run mypy src                           # strict mode
uv run pre-commit run --all-files         # ruff check, ruff format --check, mypy
```

There is no test suite. Testing is manual: convert MIDI files and play the output in MakeCode Arcade. The `testing/` directory (gitignored) holds local sample MIDI files. The `--test-*` CLI flags help with tuning instruments: replacing every melodic or drum instrument, generating ready-to-run Arcade code, sampling a range of instruments, forcing a partial params file to load, and copying the result to the clipboard (needs the optional `pyperclip`). The README documents the workflows.

`-p` (instrument parameter YAML) is always required. `examples/instrument_params.yaml` is the hand-tuned reference; its header comment documents units (ms, amplitude 0-1024, Hz) and the waveform name → Arcade ID mapping.

## Architecture

`main.py` handles the CLI, loads the MIDI file with `mido` and the YAML params with `midi_to_song/instruments.py`, then calls `cli.py` (`generate_single_conversion` / `generate_melodic_instrument_sample`). Those call `convert_midi_to_song` and turn the result into TypeScript text.

**`midi_to_song/__init__.py::convert_midi_to_song`** is the core pipeline. It runs a chain of `timeline_*` stages, each producing a new list of typed dataclasses from `midi_to_song/models.py` (the variables are named `global_timeline_0..8`):

1. `timeline/parser.py`: builds an absolute-time message list across all tracks; resolves per-port/channel instrument state (program changes, CC and SysEx drum switching, channel 10); pairs note on/off into complete notes; extracts lyrics.
2. A pitch shift for melodic notes (MIDI 60 → Arcade's different C4 numbering, plus an empirical extra octave down). Drums aren't shifted because they map to per-track drum indices.
3. `timeline/processor.py`: pitch compensation; gate-length fixes (keeps attack < gate + release to avoid a pxt playback bug); quantizing to song ticks; grouping by instrument; splitting tracks whose note range exceeds the 64-note window per octave offset; grouping notes with identical start/end/velocity into chords; splitting tracks whose encoded note bytes would overflow the 16-bit length field.
4. `timeline/validation.py`: hard checks that raise on violations of Arcade format constraints.
5. Final mapping into `arcade/music_types.py` dataclasses. It picks each melodic track's octave offset, and for drum tracks it builds the per-track drum list plus the MIDI-drum → index mapping.

The song is fixed at 120 BPM, 4 beats/measure, 24 ticks/beat (1 tick = 1/48 s). All MIDI timing is converted to absolute seconds and then quantized to this grid; MIDI tempo is not carried into the song header.

**`arcade/`** is a Python port of pxt's `pxtlib/music.ts` (encode/decode) and `localtypings/pxtmusic.d.ts` (types). The byte layout is documented in a comment at the top of `music_types.py`. Keep this code faithful to upstream pxt. Per-chord velocity is encoded as an extra trailing section, which only Arcade beta reads right now.

Format limits that drive most of the pipeline's complexity: each note is stored as 6 bits relative to the track's octave (a 64-note range), track/note-byte lengths are 16-bit, and polyphony is per-chord with no overlap within a track. Breaking any of these splits tracks rather than dropping notes.

## Conventions

- mypy runs in `strict` mode (`mido` is untyped and ignored). Ruff targets py314. Note that Python 3.14 syntax such as unparenthesized `except A, B:` is used.
- Logging goes through `utils/logger.create_logger(name=__name__, ...)`. `--debug` raises all stdout loggers to DEBUG.
