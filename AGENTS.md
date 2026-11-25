# AGENTS.md

This file provides guidance to AI coding agents when working with code in this repository.

## Project

`midi2mkcd` converts a MIDI file into a MakeCode Arcade song (a hex buffer pasted into Arcade TypeScript as `hex\`...\``). It also converts songs back to MIDI, renders audio that sounds like the simulator, helps tune instruments, and exports the instrument params as a SoundFont. Supports GM1 melodic instruments, the GM1/GM2 standard drum kit, and GS/XG SysEx drum switching. Requires Python >= 3.14 and `uv`.

## Commands

```bash
uv sync                                   # venv + deps (incl. dev group)
uv run midi2mkcd convert song.mid [-p my.yaml] [-o out.ts] [-f hex|code|code_extra]
uv run midi2mkcd recover-midi song.ts -o song.mid    # song text -> MIDI (lossy)
uv run midi2mkcd render song.mid -o song.wav         # also .flac/.ogg/.opus/.mp3
uv run midi2mkcd tune {report,replace,sample} song.mid ...
uv run midi2mkcd params {show,check,soundfont} ...   # soundfont: -o x.sf2 or x.sf3
uv run midi2mkcd ... -v                   # debug logging + tracebacks
uv run pytest                             # parallel via pytest-xdist; -n0 for serial
uv run ruff check . && uv run ruff format .
uv run mypy src tests                     # strict
uv run pre-commit run --all-files
```

CI (`.github/workflows/`) runs lint (ruff, mypy) and `pytest -m unit` / `-m integration` on Ubuntu/Windows/macOS. A PR's last commit message can skip test tiers with `[skip-unit-tests]`, `[no-integration]`, `[skip-tests]` (see `check-skip.yaml`).

## Testing

Tests compare against golden files in `tests/fixtures/golden/`, generated from `tests/fixtures/fixture.mid`. The simulator ports are checked against `golden/sim_instructions.json`, produced by real pxt code: `uv run python tests/fixtures/make_sim_cases.py`, then `node tests/fixtures/make_sim_goldens.mjs <pxt clone>` (Node >= 22.13, not in CI). The final check for output is still playing it in MakeCode Arcade. `testing/` (gitignored) holds local sample MIDIs and synth calibration scripts.

## Architecture

A library with a thin CLI. `cli.py` only parses arguments, reads files, configures logging and calls one library function per command; binary outputs require `-o` and never go to stdout. Public entry point is `api.py::convert`, re-exported from the package root. Other modules (not re-exported): `tuning.py` (tune tools), `song_to_midi/` (reverse conversion), `render.py` (all TypeScript/report text), `audio.py` (song -> simulator play instructions -> samples, mimicking `pxtsim/sound/playInstructions.ts`), `soundfont.py` (hand-written SF2/SF3 writer), `errors.py` (`Midi2MkcdError` hierarchy).

Optional extras: numpy (+ tqdm) is `audio`, soundfile is `compress`. Import them only inside the functions that need them; a missing one raises `MissingDependencyError` naming the extra. The library never prints; long work reports through a `progress(done, total)` callback.

The bundled default params are `src/midi2mkcd/resources/instrument_params.yaml` (hand-tuned; its header documents units and waveform IDs).

**Core pipeline** (`midi_to_song/__init__.py::convert_midi_to_song`): a chain of stages over dataclasses from `midi_to_song/models.py`:

1. `timeline/parser.py`: absolute-time messages, per-port/channel instrument state (program changes, drum switching), note on/off pairing, lyrics.
2. Melodic pitch shift to Arcade's numbering (plus an empirical octave down); drums aren't shifted.
3. `timeline/processor.py`: gate-length fixes (attack < gate + release, avoids a pxt bug), quantizing, grouping by instrument, chords, and track splitting when format limits would be exceeded.
4. `timeline/validation.py`: hard format checks.
5. Mapping into `arcade/music_types.py` (octave offsets, per-track drum lists).

The song is fixed at 120 BPM, 4/4, 24 ticks/beat (1 tick = 1/48 s); MIDI tempo is converted to absolute time, not carried over.

Format limits behind most of the complexity: notes are 6 bits relative to the track octave (64-note range), lengths are 16-bit, and no overlap within a track except as chords. Exceeding these splits tracks rather than dropping notes.

**`arcade/`** ports pxt's `music.ts`/`pxtmusic.d.ts` and the simulator's `song.ts`/`sequencer.ts` (`sim_*.py`, reproducing JS number semantics), pinned to the commit in their headers. Keep it faithful to upstream pxt. The byte layout is documented at the top of `music_types.py`.

## Conventions

- mypy strict (`mido` is untyped and ignored); ruff targets py314, and 3.14 syntax like unparenthesized `except A, B:` is used.
- Library modules only do `logger = logging.getLogger(__name__)`. Only the CLI configures logging (stderr), so stdout carries only generated output.
