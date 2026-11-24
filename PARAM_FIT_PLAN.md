# Feature plan: `params fit` (single layer)

This file is self-contained. It is written for a fresh coding-agent session in
the `midi2mkcd` repo (branch `param-fit`) that has **only this file** as
design context, plus the repo itself and its `AGENTS.md`. It replaces the
`params fit` section of the old `RENDER_FEATURES_PLAN.md` (now deleted) and
carries over every fact from that file that this feature depends on.

```
midi2mkcd params fit TARGET.sf2 (--program N | --drum NOTE) [-p BASE] -o OUT.yaml
                     [--bank N] [--budget N] [--fluidsynth PATH] [--allow-incomplete] [-v]
```

**Goal:** search the Arcade instrument parameters (waveform, envelopes, LFOs)
so that one instrument, played through our simulator-accurate synth, sounds as
close as possible to the same program in a real SoundFont. Write a params
YAML that is the base file with only that entry replaced.

This is a known problem ("synthesizer sound matching" / inverse synthesis).
The parameter space is small, which makes it tractable.

Facts marked **(verified)** were read from the pxt source or measured against
simulator recordings during the earlier render work; everything else is
design.

### Status (update as work lands)

| Step | Status |
| ---- | ------ |
| 1. `tuning.py` → `tuning/` package | Not started |
| 2. Pure helpers + unit tests (phrase, normalisation, gate fix, YAML splice) | Not started |
| 3. FluidSynth target rendering + segmentation | Not started |
| 4. Loss | Not started |
| 5. Melodic search (CMA-ES) + `params fit --program` CLI | Not started |
| 6. Checked by hand against a real SF2 | Not started |
| 7. Drums (`--drum`) | Not started |
| Two-layer instruments | Deferred, out of scope (see the end) |

---

## 1. What already exists (read the code, this is a map)

```
src/midi2mkcd/
  api.py             # convert(midi, params=None) -> ConversionResult; private _load_midi,
                     #   _resolve_params, _convert(midi_file, mapping, *, replace_melodics, replace_drums)
  tuning.py          # convert_replaced, sample_melodics, melodic_samples, sample_drums, drum_samples,
                     #   report, check_params, InstrumentUsage; __all__ lists them; NOT re-exported
  render.py          # ALL text output (TypeScript, reports). Not audio.
  audio.py           # song -> play instructions -> samples (simulator-accurate); numpy lazily
  soundfont.py       # params -> SF2/SF3 (renders every instrument per note with audio.py)
  errors.py          # Midi2MkcdError > InvalidMidiError, InstrumentParamsError, SongConstraintError,
                     #   InvalidSongError, MissingDependencyError
  cli.py             # argparse; file I/O; logging; _ProgressBar (tqdm); _add_* option helpers
  resources/instrument_params.yaml        # bundled default params (hand-tuned)
  resources/blank_instrument_params.yaml  # template
  midi_to_song/
    __init__.py      # convert_midi_to_song: timeline stages, pitch shift, final mapping
    instruments.py   # load_instrument_params(_with_problems), WaveForm, waveform_from_str,
                     #   InstrumentParameterMapping(melodic_pitch_comp_k, melodic_instruments,
                     #   drum_instruments)
    timeline/processor.py  # pitch_compensation_scale, PITCH_COMP_REF_NOTE, timeline_fix_gate_lens
  arcade/
    music.py         # encode_instrument / decode_instrument etc. (port of pxt music.ts)
    music_types.py   # Instrument, Envelope, LFO, DrumInstrument, DrumSoundStep (byte layout at top)
    sim_song.py      # port of pxtsim/sound/song.ts: render_instrument, render_drum_instrument
    sim_sequencer.py # port of pxtsim/sound/sequencer.ts: FREQUENCIES, note_frequency, tick_to_ms
  utils/gm_names.py  # melodic_instrument_name(program), drum_note_name(note)
  utils/resources.py # default_instrument_params_text(), blank_instrument_params_text()
```

### `audio.py` functions this feature uses

```python
def note_instructions(instrument: Instrument, note_index: int, gate_ms: float, volume: float) -> bytes
def drum_instructions(drum: DrumInstrument, volume: float) -> bytes   # ValueError if no steps
def synthesize(events: Iterable[tuple[float, bytes]], rate: int = 48000, *, progress=None) -> np.ndarray
    # events: (start ms, instructions); float32 mono; output stops at the end of each sound's instructions
def to_wav_bytes(samples, rate) -> bytes
```

- `note_index` is the Arcade note index, **MIDI note − 23** (the simulator's
  frequency table starts at B0 = MIDI 23; indices 0–96 = MIDI 23–119 are
  valid; outside that the note plays at 0 Hz: silent for sine/saw/triangle, a
  DC thump for squares, unchanged for noise) **(verified)**.
- `volume` is the sequencer volume: `music.volume() · velocity / 128`, where
  `music.volume()` defaults to **128**. So by default **volume = velocity**
  (127 at velocity 127) **(verified)**. Velocity is exactly linear in
  amplitude (−6 dB at half velocity) **(verified)**.
- `synthesize` matches the Chrome simulator to ~0.1 dB **(verified** against a
  calibration recording). Noise (waveform 5) uses the simulator's xorshift
  buffer with a fixed seed (`0xf01ba80`), so renders are **deterministic**.
  Speed: roughly 750 sounds/s on long songs; a short note is much cheaper.
  Measure the per-evaluation cost before choosing default budgets.
- numpy is the `audio` extra, imported only inside the functions that need it;
  a missing one raises `MissingDependencyError` naming the extra.

`soundfont.py` is the closest existing example of rendering a params instrument
note by note; copy its patterns:
- It round-trips each instrument through `decode_instrument(encode_instrument(i), 0)`
  so the render uses exactly the integers Arcade will store. Do the same for
  every candidate.
- It applies pitch compensation with `pitch_compensation_scale(midi - 23, k)`.

### How the converter treats a melodic note (the fit must reproduce this)

- **Pitch:** Arcade note index = MIDI − 23.
- **Pitch compensation** scales **velocity**, not pitch:
  `v' = clamp(round(v · 2^(−(n − 37)/12 · k)), 1, 127)` with `n` the Arcade
  note index (37 = MIDI 60) and `k = melodic_pitch_comp_k` from the params
  file (`processor.py::pitch_compensation_scale`). Drums are not compensated.
- **Gate fix** (`timeline_fix_gate_lens`): if a note's duration ≤ attack −
  release, it is lengthened to the next whole tick above that (one tick more
  if it lands exactly). It depends on the **candidate's** attack and release,
  so factor the rule into a pure function (e.g. `fixed_gate_seconds(duration,
  attack_ms, release_ms)`) used by both the converter and the fit, with a
  unit test; don't change the converter's behaviour (golden tests must pass).
- **Timing:** songs are 120 BPM, 24 ticks/beat; 1 tick = 1/48 s. Durations
  are quantised to ticks; the simulator's gate is `tick_to_ms(ticks)`
  (20.833 ms per tick, unrounded) **(verified)**. So choose test-phrase
  durations that are whole ticks; then the MIDI duration FluidSynth plays and
  the Arcade gate are the same.
- **Out-of-range:** only fit with notes in MIDI 23–119.

### How the simulator renders one note (what the parameters mean)

`render_instrument(instrument, freq, gate_ms, volume)` **(verified)**:

- Total length = gate + amp release. Instructions are linear ramps between
  time points: amp-envelope stage boundaries, pitch-envelope boundaries (if
  its amplitude ≠ 0), and LFO points every `max(500 / lfo.frequency, 50)` ms
  (if that LFO's amplitude ≠ 0). Points closer than 5 ms are skipped.
- Amp = envelope + amp LFO, clamped to `[0, amp_envelope.amplitude]`, then
  `/ 1024 · volume`, truncated.
- Envelope: linear attack 0 → amplitude, linear decay to
  `sustain / 1024 · amplitude` (**sustain is a fraction of the amplitude**,
  so sustain > amplitude isn't an error), hold, then linear release to 0 over
  `release` ms from the value at the gate.
- Pitch = note frequency + pitch envelope + pitch LFO, **in Hz** (not cents).
  The same setting is a much deeper bend on low notes than on high notes.
- LFO value = `cos(2π · f · t / 1000) · amplitude`, sampled at the LFO points
  (≥ 50 ms apart), so fast LFOs alias into slow ramps.
- Output gain: `(v / 1024) / 4`, halved again for square waves (11–15). Squares
  and noise buffers are not band-limited; triangle/saw/sine are.

### Params file format

- Units: attack/decay/release in **ms** (16-bit), sustain and amplitudes
  **0–1024**, LFO frequency in **Hz** (one byte, 0–255), pitch amplitudes in
  **Hz** (16-bit).
- Waveforms (13): `triangle` 1, `sawtooth` 2, `sine` 3, `noise_tunable` 4,
  `noise` 5, `square_10`..`square_50` 11–15 (`square` = `square_50`),
  `cycle_16/32/64` 16–18 (`noise_tunable_2` = `cycle_64`).
- Layout (bundled file):

```yaml
_comment: "...file header..."
melodic_pitch_comp_k: 0.1
melodic_instruments:
  # Pianos
  - instrument: 0
    _comment: Acoustic Grand Piano
    waveform: triangle
    amp_envelope: { attack: 5, decay: 500, sustain: 200, release: 200, amplitude: 768 }
    pitch_envelope: { attack: 0, decay: 0, sustain: 0, release: 0, amplitude: 0 }
    amp_lfo: { frequency: 0, amplitude: 0 }
    pitch_lfo: { frequency: 0, amplitude: 0 }
drum_instruments:
  # GM2 start
  - note: 28
    _comment: Slap
    start_freq: 300
    start_vol: 200
    steps:
      - { waveform: noise, target_freq: 0, target_vol: 50, duration: 100 }
      - { waveform: noise, target_freq: 0, target_vol: 0, duration: 200 }
```

- Drum steps: each is one linear ramp from the previous (volume, frequency)
  to the step's, using the step's waveform, over `duration` ms; volumes scale
  `v / 1024 · volume`; a final 10 ms ramp to 0.
- A drum's `_comment` becomes its `DrumInstrument.name`.

### Conventions (from `AGENTS.md`, restated for this feature)

- **CLI shape:** subcommands at every level, options after the last
  subcommand, shared options through the `_add_*` helpers in `cli.py`
  (`_add_params`, `_add_verbose`, `_add_binary_output`, ...). `params fit`
  goes next to `params show/check/soundfont` (see `_run_params_soundfont`
  for a model). Its output is text (YAML) but it must still require `-o`:
  a fit takes minutes, so don't let it go to stdout by accident.
- **Exit status:** 0 success; 1 for `Midi2MkcdError` or `OSError` (`error:
  <message>` on stderr, no traceback; `-v` re-raises); 2 usage errors.
- **Logging:** library modules only `logger = logging.getLogger(__name__)`;
  the library never prints. Long work reports through a `progress(done,
  total)` callback; the CLI shows it with `cli._ProgressBar`.
- **Library does no file I/O** (except the bundled YAML) and returns
  bytes/text. Running FluidSynth needs files, so the subprocess and temp
  directory live outside the pure fit code (see section 3).
- **Optional dependencies, imported lazily:** a new `fit` extra (see below).
  Extend `tests/unit/test_import_hygiene.py`: `import midi2mkcd` and `import
  midi2mkcd.tuning` must not import numpy, cma or scipy.
- **Checks:** `uv run pytest`, `uv run ruff check .`, `uv run ruff format
  --check .`, `uv run mypy src tests` (strict), `uv run pre-commit run
  --all-files`. Tests live only in `tests/unit/` or `tests/integration/`;
  shared paths in `tests/helpers.py`.
- **Python ≥ 3.14.** Check any new dependency has 3.14 support with `uv add`.
- Update `README.md` and `AGENTS.md` (commands list, extras) when it lands.

---

## 2. Code layout

1. **Package split first (own commit, no behaviour change):** move
   `tuning.py` to `tuning/__init__.py` (keeping every current name and
   `__all__`, so `from midi2mkcd import tuning` and `tuning.report` etc. keep
   working), and add `tuning/fit.py`. `tuning/__init__.py` must **not** import
   `fit` eagerly (fit pulls in numpy); the CLI imports
   `midi2mkcd.tuning.fit` directly.
2. **Dependency:** `cma` (pure Python, on top of numpy) as a new optional
   extra in `pyproject.toml`:
   ```toml
   fit = ["numpy>=2.3.2", "cma>=…"]
   ```
   plus the dev group. Chosen over scipy's `differential_evolution` because
   it's much lighter and handles ~14 continuous dimensions well. mypy: add
   `cma.*` to the `ignore_missing_imports` override if it ships no types.
   A missing cma raises `MissingDependencyError`: `params fit needs cma: pip
   install "midi2mkcd[fit]"`.
3. **FluidSynth runner:** a small module (e.g. `tuning/fluidsynth.py`) with
   `render_with_fluidsynth(sf2_path, midi_bytes, rate, fluidsynth="fluidsynth")
   -> bytes` (WAV). It owns the temp directory and the subprocess; the pure
   fit code takes the decoded samples. Missing executable → a
   `Midi2MkcdError` saying FluidSynth is needed and how to point at it
   (`--fluidsynth PATH`). Check for it before any work.

---

## 3. Pipeline (`tuning/fit.py`)

### 3.1 Test phrase

```python
@dataclass(frozen=True)
class PhraseNote:
    midi_note: int
    velocity: int
    ticks: int            # duration in song ticks (1/48 s)

def melodic_phrase(low: int = 36, high: int = 84) -> list[PhraseNote]
def drum_phrase(note: int) -> list[PhraseNote]
def phrase_midi(phrase, *, program: int | None, drum: bool, gap_s: float) -> bytes   # mido
```

- **Melodic:** ~5 notes spread over the range (e.g. MIDI 36, 48, 60, 72, 84,
  clamped to 23–119) × 2 velocities (e.g. 100, 50) × 3 durations (short ≈ 6
  ticks, medium ≈ 24 ticks, held ≈ 96 ticks). A CLI option for the range
  (`--notes LOW-HIGH`) is cheap to add, e.g. from `tune report`'s range for a
  known song.
- **Drums:** the one note alone at 2 velocities.
- Each note separated by enough silence (≥ 2 s, or longer than any plausible
  release) that tails don't overlap. Channel 10 (index 9) for drums; a
  program change (and bank select if `--bank`) for melodic.

### 3.2 Target audio

- Render with **FluidSynth's CLI**:
  `fluidsynth -ni -R 0 -C 0 -g <gain> -r <rate> -F target.wav TARGET.sf2 test.mid`
  (reverb and chorus off). This avoids writing an SF2 player. Use a modest
  gain (e.g. 0.5) and check the target doesn't clip.
- Fit at **22050 Hz** (half the cost of 48 kHz; the timbre the loss compares
  sits well within it). Render candidates at the same rate.
- **Segment** the target into one clip per phrase note at the known onsets,
  each long enough for the note's release (cap e.g. 3 s). FluidSynth may add
  a small start offset: find each onset as the first sample above a threshold
  near the expected time and align to it. Log a WARNING if a segment is silent
  (wrong program/bank, or the SF2 lacks it) and fail if all are.
- Compute each segment's STFT magnitudes at every resolution **once** and
  cache them; only candidates are re-analysed per evaluation.

### 3.3 Candidate rendering

For a parameter vector → `Instrument` (round to integers, clamp to field
ranges, then `decode_instrument(encode_instrument(i), 0)`), for each phrase
note:

- `note_index = midi_note − 23`
- `v' = clamp(round(velocity · pitch_compensation_scale(note_index, k)), 1, 127)`
  with `k` from the base params (the fit must work with the `k` that will be
  used at conversion), `volume = 128 · v' / 128 = v'`
- `gate_ms = tick_to_ms(ticks after the gate fix for this candidate)`
- `note_instructions(instrument, note_index, gate_ms, volume)`, then
  `synthesize([(0, instructions)], rate)`, padded/cut to the segment length.

Drums: `drum_instructions(drum, velocity)` (no pitch compensation).

### 3.4 Loss

Multi-resolution log-magnitude STFT distance plus an RMS-envelope term (phase
is arbitrary, so comparing waveforms directly doesn't work):

$$\mathcal{L} = \sum_{r} \left\| \log\left(|S_r(x)| + \epsilon\right) - \log\left(|S_r(\hat{x})| + \epsilon\right) \right\|_1 + \lambda \left\| \mathrm{dB}(\mathrm{RMS}(x)) - \mathrm{dB}(\mathrm{RMS}(\hat{x})) \right\|_1$$

with $S_r$ the STFT at a few window sizes (256, 1024, 4096 samples at
22050 Hz; Hann, 75 % overlap), summed over every note of the phrase,
normalised by frame count so long notes don't dominate. Pick ε relative to
the target's level (e.g. −80 dB below its peak) so silence doesn't blow up
the log. Clip dB values to a floor (e.g. −80 dB) in the RMS term. λ is a
tunable constant; start around 1 and log both terms separately.

### 3.5 Loudness and gain

Absolute loudness between FluidSynth and Arcade is arbitrary, but the params
must stay **balanced against the other instruments in the file**. So:

- Fix one global scale `c` from the SF2 to Arcade, the same for every fitted
  program: render a **reference program** (default program 0, `--reference
  PROGRAM`) from the SF2 with the same phrase, render the **base params'**
  entry for that program with `audio.py`, and set `c` so their overall RMS
  match. Apply `c` to the target before fitting. Log `c`.
- Then fit gain as part of the instrument: the envelope amplitude (and the
  drum volumes) is in the search, or solve it analytically per candidate (in
  the log-magnitude loss, gain is a constant offset: take the median
  log-ratio of the RMS envelopes, convert to an amplitude, clamp to ≤ 1024,
  then evaluate). The analytic route shrinks the search by one dimension;
  prefer it.
- If the best amplitude wants > 1024, it's clamped; log a WARNING that the
  instrument can't get as loud as the target (the remaining mismatch stays in
  the loss).
- Velocity response: an SF2 velocity curve is usually non-linear (often
  amplitude ∝ velocity²) while Arcade's is linear. With 2 velocities the fit
  settles on a compromise; log the level error per velocity.

### 3.6 Search

- **Space (melodic, per waveform):** 14 numbers, normalised to [0, 1]:
  amp envelope A, D, S, R, amplitude; pitch envelope A, D, S, R, amplitude;
  amp LFO frequency, amplitude; pitch LFO frequency, amplitude.
  - Times: log scale, 0–2000 ms (map 0 → 0 via e.g. `exp`-scaled with an
    offset).
  - Sustain, amp amplitude, amp LFO amplitude: linear 0–1024 (amp amplitude
    dropped if gain is solved analytically).
  - Pitch envelope amplitude: Hz, **unsigned** (`encode_instrument` stores
    it with `value & 0xFFFF` and decoding reads it unsigned, so a negative
    bend wraps to a huge one: downward bends can't be expressed); bound to
    e.g. 0–200 Hz.
  - LFO frequency 0–255 Hz (practically 0–20 Hz for vibrato/tremolo; bound
    lower and log-scale). Pitch LFO amplitude 0–50 Hz.
  - Round to integers at evaluation. Pure helpers `encode_vector(instrument)
    -> list[float]` / `decode_vector(waveform, vector) -> Instrument` with
    round-trip unit tests.
- **Method:** the synth isn't differentiable (waveform choice, noise,
  integer truncation), so gradient-free. One CMA-ES run per waveform (13):
  - Start every run from the **base entry's** values (normalised), σ ≈ 0.2,
    so the search begins near the hand-tuned instrument.
  - **Two rounds:** a short budget for all 13 waveforms, then the rest on the
    best 3. Waveforms like `noise` lose quickly on a piano.
  - Parallelise across waveforms with `multiprocessing` (on Windows, spawn:
    the worker must be a top-level function and its inputs picklable; send
    the cached target spectra once per worker).
  - `--budget N` = total evaluations (default chosen after measuring the
    per-evaluation cost; aim for a few minutes).
- **Baseline:** compute and log the loss of the **existing** entry from the
  base file. That number shows whether the fit improved on the hand-tuned one.
- Log the best loss per waveform and the winner at INFO. Report progress
  through `progress(done, total)` (evaluations).
- **Drums (after melodic works):** the step list has variable length. Try 1–4
  steps (fewer than 6: more steps mostly add dimensions). Per step: target
  frequency, target volume, duration; plus start frequency and volume. Fit one
  shared waveform per run (13 × step counts), then try swapping each step's
  waveform at the best point. Prefer fewer steps unless the loss improves
  clearly (e.g. > 5 %).

### 3.7 Library entry point

```python
@dataclass(frozen=True)
class FitResult:
    params_text: str          # the full YAML to write
    loss: float
    baseline_loss: float | None
    losses_by_waveform: dict[str, float]

def fit_melodic(target: Target, program: int, base_text: str, *, budget: int,
                rate: int = 22050, workers: int | None = None,
                progress: Callable[[int, int], None] | None = None) -> FitResult
def fit_drum(target: Target, note: int, base_text: str, ...) -> FitResult
```

`Target` holds the phrase, the decoded target samples and the reference
scale; the CLI builds it from FluidSynth output. This keeps the search
testable without FluidSynth (a test can use an Arcade render as the target
and check the fit recovers it).

---

## 4. Output (YAML splice)

Write a params YAML containing the base file (`-p BASE` or the bundled
default) with only the fitted entry replaced, keeping everything else
byte-identical:

- The params files carry descriptions in `_comment` keys (file header and
  every entry), ordinary data that survives load/dump. Record the fit there:
  the entry's new `_comment` is its GM name plus provenance, e.g.
  `Acoustic Grand Piano (fitted to FluidR3_GM.sf2 program 0, loss 12.3)`.
  Use `utils/gm_names.py`.
- The files also have `#` section comments (`# Pianos`) and one-line flow
  mappings (`amp_envelope: { attack: 5, ... }`), which PyYAML's dump would
  drop or reformat. So **splice the text**: find the entry's block (from its
  `  - instrument: N` / `  - note: N` line to the line before the next `  - `
  or `  #` line at the same indent, or the next top-level key), render the
  fitted entry in the same style, and replace just those lines. Only search
  inside the right top-level section (`melodic_instruments:` vs
  `drum_instruments:`). If the entry isn't found, append it to its list and
  log it.
- Load the result back with `load_instrument_params` (with
  `allow_incomplete` if the base needed it) before returning, as a check;
  confirm the fitted entry loads to the fitted values.
- Preserve the base file's line endings.
- No new dependency (no `ruamel.yaml`).

Pure function: `splice_entry(base_text, section, key, number, block_lines) ->
str`. Unit-test it: replace in the middle, at the end of a section, before a
`#` comment, missing entry (append), CRLF input, and that everything outside
the block is byte-identical.

---

## 5. Testing

- **Unit (CI):** phrase building, vector normalisation round-trip, the gate
  fix helper (and that the converter still uses it: goldens unchanged), the
  YAML splice, the loss (zero for identical inputs, larger for a different
  waveform), and a small **self-fit**: render a known Arcade instrument as the
  target and check a short search lands on the same waveform with a lower
  loss than the start. Skip with `pytest.importorskip("numpy")` /
  `("cma")`. Keep runtime small (tiny budget, one waveform or two).
- **Integration (CI):** `params fit` CLI usage errors (`--program` and
  `--drum` together or neither; no `-o`), missing FluidSynth gives exit 1
  with a clear message (point `--fluidsynth` at a nonexistent path), missing
  cma. Don't require FluidSynth or a real SF2 in CI.
- **By hand:** fit a few programs against a GM SoundFont (e.g. FluidR3_GM or
  GeneralUser GS) with FluidSynth installed; compare the fitted loss with the
  baseline; listen with `uv run midi2mkcd tune sample song.mid --melodics N
  -p fitted.yaml --audio s.wav` and in MakeCode Arcade.

## 6. Expectations

- A single Arcade voice has no filter: its spectrum is fixed by the waveform
  and can't get darker as a note decays. Expect the optimizer to match the
  envelope, attack and loudness well and pick the closest waveform; the
  timbre stays chiptune.
- Pitch-envelope and pitch-LFO amplitudes are **Hz**, so a vibrato fitted at
  one note is deeper (in cents) on lower notes. That's why the phrase spans
  the range.
- The fitted level is relative to the reference program; `melodic_pitch_comp_k`
  stays as it is in the base file (replacing it with per-instrument fitted
  levels is a possible later change, not part of this).

## 7. Out of scope: two layers (`--layers 2`)

**Deferred**, do last and design separately. It's the only idea here that
changes the params schema and the converter's output. Notes for later:

- Stacking two voices gives a crude timbre change over time (bright square
  with fast decay over a sustaining triangle ≈ plucked note), and slightly
  different pitch LFO/offset per layer gives chorus.
- Schema: an entry may have `layers: [ {...}, {...} ]`; single-layer entries
  stay valid. `check_params`, `load_instrument_params`, the blank template,
  `recover-midi` matching and `params soundfont` would all need to understand
  layers.
- Melodic layers need one extra track per layer (an `Instrument` is per
  track): costs track count (≤ 255), note events (toward the simulator's 15k
  warning) and size. Drum layers don't: a drum note event already holds
  several drum indices, so a two-layer drum is two indices in the same event.
- Fit single-layer first; only add a second layer if the loss improvement is
  audible.

Until then `params fit` has no `--layers` flag.
