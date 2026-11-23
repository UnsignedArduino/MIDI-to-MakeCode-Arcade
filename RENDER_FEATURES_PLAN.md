# Feature plan: audio rendering, MIDI recovery, SoundFont export and instrument fitting

This file is self-contained. It is written for a fresh Claude Code session in
the `midi2mkcd` repo (branch `render-features`, cut from `main` after the
`cli-refactor` branch was merged) that has **only this file** as design
context, plus the repo itself and its `CLAUDE.md`.

It covers four new commands and one new flag:

| Command / flag              | What it does                                                        |
| --------------------------- | ------------------------------------------------------------------- |
| `midi2mkcd recover-midi`    | Arcade song (hex or `code_extra` file) → MIDI file. Lossy.          |
| `midi2mkcd render`          | MIDI file or Arcade song → WAV, synthesized like the Arcade simulator. |
| `tune replace/sample --wav` | Write the tuning output as a WAV instead of pasting into Arcade.     |
| `midi2mkcd params soundfont`| Instrument params YAML → SF2 SoundFont, to use in a MIDI editor.     |
| `midi2mkcd params fit`      | Optimize params so an instrument sounds like a real SoundFont instrument (stretch goal). |

Suggested order: `recover-midi`, then the synthesizer (with its validation
harness), then `render` and `tune ... --wav`, then `params soundfont`, then
`params fit`, and **last of all** two-layer instruments (`params fit --layers 2`,
section 6), an even-more-stretch goal to be designed in detail only when
everything else is done. Each is its own commit (or several). Apart from the
`midiDrumNoteMap` fix (section 3), the range-splitting fix (open question 8),
both already done, and two-layer instruments, none of them changes the
conversion algorithm or the existing golden outputs.

### Status (update this table as each feature lands)

| Feature                         | Status | Notes |
| ------------------------------- | ------ | ----- |
| `recover-midi` (section 3)      | **Done** (2026-11-21, branch `render-features`) | See "Status: done" at the top of section 3. |
| Synthesizer Stage 1 pxt ports + goldens (section 4) | **Done** (2026-11-23, branch `render-features`) | `arcade/sim_song.py`, `arcade/sim_sequencer.py`, goldens from the real pxt source. See "Stage 1 status" in section 4. |
| Synthesizer Stage 1 `audio.py` wrappers | **Done** (2026-11-23, branch `render-features`) | `note_instructions` / `drum_instructions` / `song_events` over the ports, no numpy. See "Stage 1 status" in section 4. |
| Synthesizer Stage 2, `render`, `tune ... --wav` | **Next** | Start with the numpy `audio` extra (open question 6), then `synthesize`. Read section 2 "Stage 2" (incl. the stop-at-end and rest notes) and section 4. The clean reference recording is `testing/calibration/calibration_recording_3_clean.mkv`; the comparison script is still to write (section 4, Validation 2). |
| `params soundfont` (section 5)  | Not started | |
| `params fit` (section 6)        | Not started | |
| Two-layer instruments           | Deferred (last) | |

Facts marked **(verified)** were read from the upstream source at the commits
listed in "Upstream source"; everything else is design. Items under
"Open questions" must be checked before relying on them.

---

## 1. Project context (what already exists)

Read `CLAUDE.md` first; this section only summarises what these features
depend on.

### Library layout (as of `main` @ `2e58978`, the `cli-refactor` merge)

```
src/midi2mkcd/
  __init__.py        # public re-exports, __all__, __version__ (single source of the version)
  api.py             # convert(midi, params=None) -> ConversionResult; private _load_midi,
                     #   _resolve_params, _convert(midi_file, mapping, *, replace_melodics, replace_drums)
  tuning.py          # convert_replaced, sample_melodics, sample_drums, report, check_params,
                     #   InstrumentUsage; NOT imported by the package root
  render.py          # ALL text output: to_typescript, song_hex, sampler, usage_report
  song_to_midi/      # __init__.py: song_to_midi (added by recover-midi, see section 3)
  errors.py          # Midi2MkcdError > InvalidMidiError, InstrumentParamsError, SongConstraintError,
                     #   InvalidSongError (added by recover-midi)
  cli.py             # argparse subcommands; file I/O; logging setup; clipboard
  __main__.py
  resources/instrument_params.yaml        # bundled default params (hand-tuned)
  resources/blank_instrument_params.yaml  # template
  midi_to_song/      # the conversion pipeline (internals)
    __init__.py      # convert_midi_to_song: timeline stages, pitch shift, final mapping
    instruments.py   # load_instrument_params, WaveForm, waveform_from_str, InstrumentParameterMapping
    timeline/parser.py, processor.py, validation.py
  arcade/
    music.py         # encode/decode of the song buffer (port of pxt pxtlib/music.ts)
    music_types.py   # Song, Track, Instrument, Envelope, LFO, NoteEvent, Note, DrumInstrument,
                     #   DrumSoundStep; byte layout documented at the top
    sim_song.py      # port of pxtsim/sound/song.ts: render_instrument, render_drum_instrument
                     #   (Stage 1, added 2026-11-23)
    sim_sequencer.py # port of pxtsim/sound/sequencer.ts: FREQUENCIES, tick_to_ms,
                     #   metronome_interval_ms, volume formulas, sequence_song (Stage 0/1)
  utils/strings.py (parse_range, format_ranges), format.py, resources.py, gm_names.py,
        song_text.py (parse_song_text, added by recover-midi)
```

Name clash to avoid: `render.py` already means **text** rendering. The audio
code goes in a new `audio.py`, never in `render.py`.

### `ConversionResult` (in `api.py`)

```python
@dataclass(frozen=True)
class ConversionResult:
    song: Song
    drum_idx_to_midi_drum: list[int]          # index i -> MIDI drum note for drum index i
    track_idx_to_midi_instrument: list[int]   # track i -> MIDI program, -1 = standard drum kit
    lyric_ticks: list[int]
    lyric_texts: list[str]
    note_event_count: int
    warnings: list[str]
    comments: tuple[str, ...] = ()
    def to_hex(self) -> str: ...
    def to_typescript(self, format: OutputFormat = "hex") -> str: ...
```

`OutputFormat = Literal["hex", "code", "code_extra"]`. `code_extra` output is:

```ts
// Generated by https://github.com/UnsignedArduino/MIDI-to-MakeCode-Arcade
const songHex = hex`...`;
const midiDrumNoteMap: number[] = [36, 38, 42];
const trackInstrumentMap: number[] = [0, 48, -1];
const lyricTicks: number[] = [...];
const lyricTexts: string[] = [...];
```

### What the converter does to a song (needed to undo or reproduce it)

- Song header is fixed: 120 BPM, 4 beats/measure, **24 ticks/beat** (1 tick =
  1/48 s). MIDI tempo is converted to absolute time, then quantized to this
  grid; tempo is not carried into the song.
- **Pitch shift (verified, now explained).** Melodic notes get `note -= 23`
  (`midi_to_song/__init__.py`: `-= 11` then `-= 12`, with a comment saying
  the second octave is empirical). The reason: the simulator's frequency table
  (`pxtsim/sound/sequencer.ts`, `frequencies`, 97 entries) starts at B0
  (31 Hz, MIDI 23) and index 37 is 262 Hz (MIDI 60). So **Arcade note index
  = MIDI note − 23**, valid for indices 0–96 (MIDI 23–119). Update the
  comment in `__init__.py` when convenient.
- **Pitch compensation** scales **velocity**, not pitch. In shifted note
  space, with `ref = 60 - 11 - 12 = 37` (i.e. MIDI 60):

  $$v' = \operatorname{clamp}\left(\operatorname{round}\left(v \cdot 2^{-\frac{(n - 37)}{12} k}\right), 1, 127\right)$$

  where `k = melodic_pitch_comp_k` from the params file (0 disables it).
  Drums are not compensated.
- **Gate fix.** `timeline_fix_gate_lens` lengthens melodic notes so that
  `attack < gate + release`, avoiding a playback bug
  (https://github.com/microsoft/pxt/pull/11352).
- **Track splitting.** A melodic track holds a 64-note window (`(octave - 2) * 12`
  to `+ 63`, octave offset 0–9, stored in the instrument's byte 27). Wider
  tracks split; tracks also split when encoded note bytes overflow 16 bits.
  Chords are notes with identical start/end/velocity.
- **Drum tracks.** Each drum track carries its own list of `DrumInstrument`s;
  a drum note event's notes are **indices into that track's list**, one byte
  each (so ≤ 256 drums per track; the drum section length is 16-bit). The
  "61 samples max" comment in `processor.py` is only the GM drum range
  27–87, not a format limit. The drum track's `instrument` is a dummy
  (square_10, octave 4).
- **Velocity** is stored in an optional trailing per-track section (one byte
  per note event), written only when some event has velocity < 128. Since
  MIDI velocities top out at 127, in practice it is always written for
  converted songs. Stable MakeCode Arcade now reads it (per-chord velocity is
  generally available) **in the simulator**. The hardware sequencer ignores
  it (see "Simulator vs hardware" in section 2). Missing velocity means 128.

### Song buffer layout (from `arcade/music_types.py`, port of `pxtmusic.d.ts`)

```
song:   0 version | 1 bpm (16-bit) | 3 beats/measure | 4 ticks/beat | 5 measures | 6 #tracks
        ...tracks ...track velocities
track:  0 id | 1 flags (1 = drum) | 2 instruments byte length (16-bit) | 4 instrument or drums
        | notes byte length (16-bit) | note events
instrument (28 bytes): 0 waveform | 1 amp A | 3 amp D | 5 amp S | 7 amp R | 9 amp amp
        | 11 pitch A | 13 pitch D | 15 pitch S | 17 pitch R | 19 pitch amp
        | 21 amp LFO freq (8-bit) | 22 amp LFO amp | 24 pitch LFO freq (8-bit) | 25 pitch LFO amp | 27 octave
drum:   0 #steps | 1 start freq | 3 start amp | 5... steps (7 bytes: waveform, freq, volume, duration)
note event: 0 start tick | 2 end tick | 4 polyphony | 5... notes (1 byte each)
melodic note byte: low 6 bits = note - (octave - 2) * 12; high 2 bits = enharmonic spelling
```

`arcade/music.py` already has `decode_song_from_hex`, `decode_song`,
`decode_note` etc. Keep that module faithful to pxt.

### Params file semantics (`instrument_params.yaml`)

- Units: attack/decay/release/durations in **ms**; sustain and amplitudes
  **0–1024**; LFO frequency in **Hz** (one byte, 0–255).
- **Sustain is a fraction of the envelope amplitude**: sustain level =
  `sustain / 1024 * amplitude` (verified in `envelopeValueAtTime`). So
  sustain > amplitude is not an error.
- **Pitch envelope and pitch LFO amplitudes are in Hz, added to the note
  frequency** (verified in `instrumentPitchAtTime`), not cents. The same
  setting is a much deeper bend on low notes than on high notes.
- Waveforms (13 values): triangle 1, sawtooth 2, sine 3, noise_tunable 4,
  noise 5, square_10..square_50 = 11..15, cycle_16/32/64 = 16..18
  (`instruments.py::WaveForm`).

### Conventions every new feature must follow

- **CLI shape.** Subcommands at every level, options after the last
  subcommand, shared options added by the `_add_*` helpers in `cli.py`
  (not parent parsers). `-f` stays **text formats only**. Binary outputs
  (WAV, MIDI, SF2) always require `-o` and are never written to stdout; never
  infer a format from the `-o` extension. `_emit` stays text-only; binary
  output uses `_add_binary_output` and `_emit_bytes(data, output)`, which
  `recover-midi` added. Keep
  `INPUT` handling per subcommand (not every command takes MIDI).
- **Exit status.** 0 success; 1 for `Midi2MkcdError` or `OSError` (`error:
  <message>` on stderr, no traceback; `-v` re-raises); 2 usage errors;
  traceback for anything else (internal invariants raise `RuntimeError`
  "(please report)").
- **Logging.** Library modules only do `logger = logging.getLogger(__name__)`.
  Only `cli.configure_logging` adds a handler (stderr). stdout carries only
  command output.
- **Library returns bytes** for binary outputs (`to_wav_bytes`,
  `song_to_midi`, SF2 writer). No file I/O in the library except the bundled
  YAML. This keeps the door open for a possible future Pyodide web page that
  wraps them in a `Blob`; that page is **out of scope** for this plan.
- **Optional dependencies, imported lazily.** numpy goes in an `audio` extra,
  the optimizer (`cma` and/or scipy) in a `fit` extra. A missing extra gives
  a clear error naming the extra, like `--copy` does for pyperclip:
  `error: render needs numpy: pip install "midi2mkcd[audio]"`, exit 1. Extend
  `tests/unit/test_import_hygiene.py`: `import midi2mkcd` must not import
  numpy, scipy or cma.
- **Checks.** `uv run pytest`, `uv run ruff check .`, `uv run ruff format
  --check .`, `uv run mypy src tests` (strict) and `uv run pre-commit run
  --all-files` must pass (CI runs the ruff checks on `.`). This file is
  already in `[tool.ruff] extend-exclude` in `pyproject.toml`, since its
  Python blocks are sketches.
- **Tests** live only in `tests/unit/` or `tests/integration/` (the conftest
  rejects anything else and marks tiers by directory). Shared paths are in
  `tests/helpers.py`. Golden files are LF-only (`.gitattributes`); never
  rewrite them with `write_text` on Windows without `newline="\n"`. Binary
  fixtures must be marked binary in `.gitattributes`.
- **Python ≥ 3.14**, mypy strict, ruff target py314. Check that every new
  dependency has 3.14 wheels before adding it.

---

## 2. How MakeCode Arcade plays a song (the reference to port)

### Upstream source

Read these before writing synthesis code. Clone with `--depth 1 --filter=blob:none --sparse`
and `git sparse-checkout set pxtsim` / `libs/mixer`.

| Repo / commit                                   | Path                                  | What it is |
| ----------------------------------------------- | ------------------------------------- | ---------- |
| `microsoft/pxt` @ `19bd210` (2026-11-19)        | `pxtsim/sound/sequencer.ts`           | **Simulator sequencer.** Frequency table, tick loop, velocity/volume math, `playNoteAsync`, `playDrumAsync`. |
|                                                 | `pxtsim/sound/song.ts`                | **Simulator** `renderInstrument`, `renderDrumInstrument`, envelope/LFO math, `addNote`, `decodeSong`. Port **this** file. |
|                                                 | `pxtsim/sound/playInstructions.ts`    | **Simulator synth.** `PlayInstructionsSource.playInstructionsAsync`: turns instruction buffers into Web Audio nodes. |
|                                                 | `pxtsim/sound/audioContextManager.ts` | `playInstructionsAsync` (line ~187) creates one `PlayInstructionsSource` per call. |
|                                                 | `pxtsim/sound/metronome.ts`           | Worker-based tick timer (real-time jitter; irrelevant offline). |
|                                                 | `pxtsim/sound/worklet.ts`, `audioWorkletSource.ts` | A JS port of `melody.cpp`, used **only** by `spatialAudioPlayer.ts`. **Not** the normal song path. |
| `microsoft/pxt-common-packages` @ `d514894`     | `libs/mixer/instrument.ts`            | Device-side TS `renderInstrument` (~line 537) / `renderDrumInstrument` (~728); same algorithm as `song.ts` with comments. Useful for reading. |
|                                                 | `libs/mixer/melody.cpp`               | **Hardware** synth (`fillSamples`): fixed-point naive waveforms, xorshift noise. Not the reference. |

**Reference choice: the browser simulator**, because that is what people hear
while editing. Target perceptual equality with the simulator, not
sample-exact output, for stage 2 below. Stage 1 can and should be
byte-exact.

**What is the source of truth for what:**

- **pxt source code** for everything pxt computes itself: instruction
  bytes, envelope/LFO math, tick timing, gain formulas. This is the same in
  every browser (the 20 ms tick, too: `setInterval`'s delay is a WebIDL
  `long` everywhere).
- **Chrome's Web Audio** for what the browser decides: oscillator
  band-limiting, looped-buffer interpolation, `setTargetAtTime` curves.
  Firefox and Safari may differ slightly there; Chrome is the target since
  most users run it.
- **Recordings only validate** the model, and only for what survives the
  capture chain. They are never the spec.

Call path for one song note in the simulator **(verified)**:
`Sequencer.onTick` → `playNoteAsync(note, instrument, gateMs, volume)` →
`renderInstrument(instrument, frequencies[note], gateMs, volume)` →
`AudioContextManager.playInstructionsAsync(buffer)` →
`new PlayInstructionsSource(context, destination).playInstructionsAsync(buffer)`.

### Stage 0: sequencer (`pxtsim/sound/sequencer.ts`) (verified)

- `tickToMs(bpm, tpb, ticks) = (60000 / bpm) / tpb * ticks` (= 20.833 ms per
  tick at 120 BPM, 24 ticks/beat). **But ticks actually fire every 20 ms:**
  the metronome worker calls `setInterval(..., tickToMs(1))`, and
  `setInterval` converts its delay to an integer number of ms (truncating),
  so 20.833 becomes 20 (`metronome.ts`, verified). **Measured** in a
  recording of the simulator (Chrome, 2026-11-20): note starts 48 song
  ticks apart are exactly 0.96 s apart, and 600 ticks are 12.00 s, so
  converted songs play **4% fast** (125 instead of 120 BPM). Gate lengths
  still use the unrounded `tickToMs` (500 ms for 24 ticks). So in `render`:
  note start = `tick * trunc(tickToMs(1))` ms, gate = `tickToMs(endTick -
  startTick)`. (Worker timer jitter averages out offline.) `render` copies
  this on purpose so it sounds like the editor. Whether the converter should
  compensate (a 125 BPM header makes a tick exactly 20 ms) depends on how
  hardware times songs; that is tracked in
  https://github.com/UnsignedArduino/MIDI-to-MakeCode-Arcade/issues/11 and is
  out of scope here. Song length =
  `measures * beatsPerMeasure * ticksPerBeat` ticks. On the last tick it stops with `stop(true)`, which lets sounding
  notes finish (so render the release tails after the last tick).
- On each tick, for each track, for each note event whose `startTick` equals
  the current tick, for **each note in the event separately**:
  - `velocity = noteEvent.velocity ?? 128`
  - melodic: `volume = globalVolume * (trackVolume / 1024) * (velocity / 128)`.
    **`globalVolume` is `music.volume()`, which defaults to 128** (it is a
    0–255 value, `CFG_SPEAKER_VOLUME` default 128 in
    `pxt-common-packages/libs/mixer/melody.ts`; `_SimulatorSequencer` passes
    it straight to `setVolume`, which clamps to 0–1024). `trackVolume` is
    1024. So by default **`volume = velocity`** (127 for velocity 127), not
    `8 * velocity`: songs play 18 dB below full scale unless the game calls
    `music.setVolume` (max 255, still 12 dB down). Measured to 0.01 dB
    (calibration recording 3). `render` needs a `--volume` (default 128,
    0–255) and must use it here; the coarse integer rounding in
    `renderInstrument` at these small volumes matters;
    `gate = tickToMs(endTick - startTick)`; frequency = `frequencies[note]`
    where `note` is the decoded note index (after the octave offset).
  - drum: same volume times `drumVolume / 1024` (default 1024), and
    `renderDrumInstrument(track.drums[noteIndex], volume)`.
- `frequencies` is a table of **integer Hz**, 97 entries, index 0 = B0
  (31 Hz) … index 96 = B8 (7902 Hz); index i ≈ 440·2^((i + 23 − 69)/12).
  Use this table, not an equal-temperament formula. An index outside 0–96
  gives `undefined`, so the pitch becomes NaN and `| 0` turns it into 0 Hz.
  What that sounds like was **measured** (open question 1): silent for sine
  and sawtooth, a DC offset shaped by the amp envelope for square (a thump
  or click, no pitch), and unchanged noise for waveform 5.

### Stage 1: note → instruction buffer (`pxtsim/sound/song.ts`) (verified)

An instruction is 12 bytes, little-endian 16-bit fields:
`[0] waveform, [1] flags (0), [2] start Hz, [4] duration ms, [6] start volume,
[8] end volume, [10] end Hz`. Each is one linear ramp. The player reads to
the end of the buffer; an instruction with waveform 0 is a **silent rest** of
its duration (it advances time and keeps going), not a terminator
(`playInstructions.ts`, verified).

`renderInstrument(instrument, noteFrequency, gateLength, volume)`:

- `totalDuration = gateLength + ampEnvelope.release`.
- Builds a sorted list of time points: amp-envelope stage boundaries (end of
  attack, end of decay if before the gate, the gate, the end of release),
  pitch-envelope boundaries (only if its amplitude ≠ 0), and LFO points every
  `max(500 / lfo.frequency, 50)` ms (only if that LFO's amplitude ≠ 0). Port
  the loop exactly; it is order-sensitive.
- For each consecutive pair of points **at least 5 ms apart** (shorter gaps are
  skipped), emits one instruction from the previous point's
  (amp, pitch) to this point's.
- Amp at time t: `env = envelopeValueAtTime(ampEnvelope)` (if its amplitude ≠ 0)
  `+ lfoValueAtTime(ampLFO)` (if ≠ 0), clamped to `[0, ampEnvelope.amplitude]`,
  then `(clamped / 1024) * volume`, truncated `| 0`.
- Pitch at time t: `noteFrequency + pitchEnv(t) + pitchLFO(t)` (each only if
  its amplitude ≠ 0), `max(…, 0)`, truncated `| 0`.
- `envelopeValueAtTime(env, t, gate)`: after the gate, a **linear** ramp from
  the value at the gate to 0 over `release` ms; before that, linear attack
  0 → amplitude, linear decay amplitude → `sustain / 1024 * amplitude`, then
  hold. **Gates shorter than the attack (settled by the port):** the time
  points don't include the gate there, only the end of the attack and gate +
  release. So the instructions ramp from 0 up to the release curve's value at
  the end of the attack (11/1024 of full for attack 200, gate 21, release
  200), then down to 0 at gate + release. That is the quiet "rises for the
  whole attack, gone by ~220 ms" shape calibration recording 3 measured.
- `lfoValueAtTime(lfo, t) = cos(2π · lfo.frequency · t / 1000) · lfo.amplitude`
  (a cosine sampled at the LFO points, so effectively a triangle-ish ramp
  chain at ≥ 50 ms resolution).
- If the last amp is non-zero, appends a 10 ms ramp to 0.
- `addNote` stores volumes as `(v * 255) >> 6` (so 0–1024 becomes 0–4080) and
  frequencies via `value | 0` then uint16 wrap.

`renderDrumInstrument(drum, volume)`: one instruction per step, from the
previous (volume, frequency) to the step's, using the step's waveform and
duration, with volumes scaled by `v / 1024 * volume`; then a 10 ms ramp to 0
with the last step's waveform.

JS semantics to reproduce exactly: `x | 0` is int32 truncation toward zero
(NaN → 0, ±Infinity → 0); `>>` converts to int32 first; division by zero
gives ±Infinity/NaN (e.g. attack 0 or release 0); `set16BitNumber` wraps
modulo 65536.

### Stage 2: instruction buffer → sound (`pxtsim/sound/playInstructions.ts`) (verified)

Each `playInstructionsAsync` call (i.e. each note) gets its own
`PlayInstructionsSource`: `gain (GainNode) → analyser → vca (gain 1) →
destination (gain 1, used for mute) → AudioContext.destination`. Everything is
summed as float by Web Audio; **nothing clips until the device output**.

Per instruction:

- Volume: `gain = (v / 1024) / 4`, and **halved again for square waves (11–15)**.
  Combined with `addNote`'s `(v * 255) >> 6`, the effective peak amplitude is
  ≈ `env_level / 1024 · volume / 1024` (× 0.5 for squares), with
  oscillators at ±1, and `volume = music.volume() · velocity / 128` (about
  `velocity / 1024` by default, see Stage 0). **Measured** (calibration
  recording 3): a flat sine at velocity 127 is −21.18 dBFS, exactly
  `(127·255 >> 6) / 1024 / 4 / √2`; velocity is exactly linear
  (−5.95 dB at 64, −12.0 dB at 32 for every waveform); squares are −3.0 dB
  against a sine (the × 0.5 on an rms-1 wave), for all five duties.
- `gain.setValueAtTime(start, t)` then `linearRampToValueAtTime(end, t + d)`.
- One generator node per **waveform** per note, created on first use, started
  immediately, and reused. When the waveform changes (only drums do this),
  the previous waveform's gain fades with `setTargetAtTime(0, t, 0.015)`.
  At the end, the source's gain fades with `setTargetAtTime(0, end, 0.015)`.
  **But the note is stopped at its end** (checked in the source,
  `playInstructions.ts` lines 177–280): `playInstructionsAsync` awaits
  `U.delay(totalDuration * 1000)` (the sum of all instruction durations,
  rests included) and then `stop()`s and disconnects every generator. So
  that last 15 ms fade barely runs (only for the timer's lateness, a few
  ms). Since every buffer ends with a 10 ms ramp to 0 when the amp is open
  (`renderInstrument`'s roll-off, `renderDrumInstrument`'s final step), the
  rule for `synthesize` is: **output stops at the end of the instructions**.
  The fade of a *previous* waveform at a waveform change does run (it happens
  mid-note).
- **Waveform-0 rests** (`wave === 0`) only advance `currentTime`. They don't
  touch any gain, so the current waveform keeps its last level through the
  rest. pxt's buffers only contain zero-duration rests (the unused slots at
  the end, a drum step of duration 0), so this rarely matters; don't add a
  fade there.
- Each instruction's duration is an integer number of ms (`/ 1000` s), and
  all automation is scheduled at absolute context times (`currentTime +=
  duration`).
- Generators:
  - **triangle 1, sawtooth 2, sine 3**: native `OscillatorNode` (band-limited),
    `frequency.setValueAtTime(start, t)` +
    `linearRampToValueAtTime(end, t + d)`.
  - **square 11–15**: a looped 1024-sample buffer with duty `(w - 10) * 10`%
    (+1 for the first `duty%` of samples, −1 after),
    `playbackRate = hz / (sampleRate / 1024)`. Resampled by Web Audio
    (not band-limited; aliases at high notes).
  - **noise_tunable 4**: a looped buffer of a square wave filtered by a
    pseudorandom bit, **4 samples per cycle**, `playbackRate = hz / (sampleRate / 4)`.
  - **cycle_16/32/64 (16–18)**: same 4-samples-per-cycle scheme from fixed
    short bit patterns (`getCycleNoiseBuffer`), same playback rate.
  - **noise 5**: a 100000-sample xorshift white-noise buffer (seed
    `0xf01ba80`, `x ^= x << 13; x ^= x >> 17; x ^= x << 5`, sample =
    `(x & 1023) / 512 - 1`), looped, playback rate 1 (ignores frequency).
  - **Buffer sources (4, 5, 11–18) set `playbackRate` only once**, from the
    start frequency of the first instruction that creates that waveform's
    generator (`getGenerator`). After that each instruction only calls
    `linearRampToValueAtTime(end)`, with no `setValueAtTime(start)`, so the
    ramp starts from wherever that generator last was. Oscillators (1–3) do
    call `setValueAtTime(start)` each time. Melodic notes are unaffected
    (each instruction starts where the previous one ended). For drums that
    switch waveforms and come back, the returning waveform ramps from its
    own previous end, across the time it was silent. So `synthesize` must
    keep the playback rate (and phase) per waveform per note (verified).
  - Copy the exact buffer-generation code for 4, 5 and 16–18 from
    `playInstructions.ts`; the RNG and bit patterns must match.
- Sample rate is the `AudioContext`'s (usually 44100 or 48000). All buffers
  (including the 100000-sample noise buffer, played at rate 1) are created at
  that rate, so noise sounds slightly different at each. See open question 7
  for `render`'s default.

The hardware (`melody.cpp`) differs: naive fixed-point waveforms, no `/ 4` or
square halving that I verified, its own mixer and clipping. Do not mix the two
references.

### Simulator vs hardware: two different sequencers (verified)

`music.Song.play` in `pxt-common-packages/libs/mixer/instrument.ts` checks
`control.deviceDalVersion() === "sim"`:

- **Simulator:** `_SimulatorSequencer` hands the whole buffer to pxtsim's
  `sequencer.ts` (Stage 0 above). This is the reference for `render`.
- **Hardware:** a TypeScript `music.sequencer.Sequencer`
  (`libs/mixer/sequencer.ts`) runs in the game. Per tick it calls
  `renderInstrument`/`renderDrumInstrument` with `music.volume()` only, so
  it **ignores the per-chord velocity section**. It then waits
  `pause(tickToMs(1))` and plays through `melody.cpp`. Its timing therefore
  depends on `pause` granularity and loop overhead (relevant to issue #11).

**Polyphony limits:**

- **Hardware:** `melody.h` has `#define MAX_SOUNDS 8` (it was 5 around
  2020). When all 8 slots are busy, `WSynthesizer::updateQueues` takes over
  one slot. Its comment says "expel the oldest sound", but the loop as
  written appears to pick the slot with the **latest** `startSampleNo`.
  Not verified on a device.
- **Simulator, old:** `pxtsim/simlib.ts` (2024, commit `942d381`) kept at
  most about 21 channels: once more than 20 were playing, a new sound
  removed the oldest (`channels[0].remove()`). The forum's "20 in the
  browser" (staff reply, June 2024) refers to this.
- **Simulator, current:** the sound code moved to `pxtsim/sound/` in
  February 2026 (pxt #11072, #11123). `playInstructions.ts` and
  `audioContextManager.ts` at `v13.2.13` (the pxt-core version pxt-arcade
  4.2.1 uses) have **no cap**: every note gets its own
  `PlayInstructionsSource`. The calibration song's chords of up to 32 notes
  check this. If the recording shows no cap, `render` has none either; if
  it shows one, model it.
- `render` targets the simulator, so the hardware limit only matters for a
  possible later `--hardware` mode, or for a converter warning when a song
  has more than 8 overlapping sounds.

---

## 3. `recover-midi`: Arcade song → MIDI

```
midi2mkcd recover-midi INPUT [-p PARAMS] -o OUT.mid [-v]
```

### Status: done (2026-11-21)

Implemented as designed below; don't redo it. What exists now:

- `src/midi2mkcd/utils/song_text.py`: `parse_song_text(text) -> SongText`
  (hex digits with whitespace stripped, `is_code_extra`, and the four
  `code_extra` arrays parsed with `json.loads`). **`render` must reuse it** for
  its text-input branch.
- `src/midi2mkcd/song_to_midi/__init__.py` (a package, the mirror of
  `midi_to_song/`): `song_to_midi(text, params=None) -> bytes`, imported as
  `from midi2mkcd.song_to_midi import song_to_midi`. Not re-exported from the
  package root.
- `errors.py`: new `InvalidSongError(Midi2MkcdError)` (exported from the root)
  for no/several literals, bad hex, an undecodable buffer, bad `code_extra`
  arrays, and (CLI) a non-UTF-8 input file.
- `processor.py`: `pitch_compensation_scale(arcade_note, k)` and
  `PITCH_COMP_REF_NOTE` factored out of `timeline_apply_pitch_compensation`
  (no output change). `params soundfont` should use this function too.
- `cli.py`: `recover-midi` subcommand, `_add_binary_output` (`-o` required)
  and `_emit_bytes(data, output)`, the pattern for `render` and
  `params soundfont`. `_read_song_text` reads UTF-8 (BOM allowed).
- Tests: `tests/unit/test_song_text.py`, `tests/integration/test_recover_midi.py`
  and new cases in `test_cli.py`. Goldens are unchanged.
- README ("Recovering a lost MIDI file", help text) and CLAUDE.md updated.

Decisions made during implementation (beyond the design below):

- A `code_extra` file's `trackInstrumentMap` is used only if its length and
  its −1 entries line up with the song's tracks; otherwise it logs a WARNING
  and falls back to matching against the params.
- Matching compares encoded bytes (`encode_instrument(...)[:27]`, i.e. without
  the octave byte; `encode_drum_instrument(...)` for drums), so the drum
  `name` field and 16-bit wrapping don't matter.
- A drum that matches no params entry has its hits **dropped** with a WARNING
  (there is no sensible default drum note). Melodic notes outside MIDI 0–127
  after `+ 23` are also dropped with a WARNING.
- **Duplicates are kept**, not dropped as the design below says. Real songs
  double parts (the same notes on two channels with one program), and Arcade
  plays both, so dropping them lost notes. Revisit only when layered tracks
  (section 6) exist; then drop duplicates between layer tracks only.
- **Lanes.** The converter (like most players) ends the earliest-started
  sounding note of a pitch at each note off (FIFO). So a same-pitch note that
  ends before an earlier-started one (a short hit inside a long note) can't
  share its channel: `_split_lanes` puts it in another lane, which gets its own
  channel (for drums, channel 10 of the next port).
- **Channels.** `_assign_slots` gives each (program, lane) part its own channel
  while port 0 has free ones. After that, parts share a channel with parts
  that ended before they start (a program change in between), and only then
  spill onto further ports. Each slot is one MIDI track named after its
  programs' GM names (`Viola / Clarinet`).
- **Ports.** When more than one port is used, **every** note track starts with
  a `midi_port` event, port 0 included. SpessaSynth puts a track without one on
  the lowest port any track names. When only port-1 tracks had one, every
  port-0 track moved to port 1 and collided with it. That swapped instruments
  in "All I Want For Christmas Is You" (17 programs).
- The MIDI is type 1: a conductor track (120 BPM tempo, 4/4, lyrics), then the
  slot tracks. Lyrics are written UTF-8-encoded inside mido's latin-1 text,
  which the converter's `decode_lyric_text` reads back (`café` round-trips).
- **Verified round trips** (convert → `code_extra` → `song_to_midi` → convert,
  comparing every played note as (program, start, end, pitch, velocity)):
  exact on the fixture, on `testing/Rick Astleys Album ... Never Gonna Give You
  Up.mid` (4439 notes) and on `testing/All_I_Want_For_Christmas_Is_You_–_Mariah_Carey.mid`
  (9989 notes, 17 programs, 2 ports). The hex is not byte-identical: chord
  note order and which track of a program a note lands in (the polyphony
  split depends on input order) can change, which doesn't change what plays.
  The tests compare played notes the same way.

### Design

An escape hatch for when the original MIDI is lost. Explicitly lossy; not
meant for round trips (hence the name rather than `to-midi`). Dependencies:
mido only (already required).

### Input

A helper (shared with `render`) reads a `.ts`/`.txt` file and extracts the
single ``hex`...` `` literal. If the file has none or more than one (e.g. a
`tune sample` output), raise a `Midi2MkcdError` saying how many it found.
No `--index` for now. A **`code_extra`** file is recognised by its `songHex` /
`trackInstrumentMap` consts and parsed for the extra arrays (simple regex /
`json.loads` on the array literals; lyric texts are JS string literals, so
handle escapes and non-ASCII).

### What can be recovered

| Information      | Plain hex + params file                                         | `code_extra` file |
| ---------------- | ---------------------------------------------------------------- | ----------------- |
| Melodic program  | Match each track's embedded `Instrument` (ignoring `octave`) against the params entries. Identical entries are ambiguous: pick the lowest program and log it at INFO. No match: program 0 and a WARNING. | `trackInstrumentMap[track]` (exact; −1 = drum track) |
| Drum notes       | Match each embedded `DrumInstrument` against the params' drum entries (same ambiguity rule). | `midiDrumNoteMap[drumIdx]`, but see the caveat below |
| Lyrics           | Lost                                                              | `lyricTicks` / `lyricTexts` → lyric meta events |
| Velocity         | Undo pitch compensation using the params' `melodic_pitch_comp_k`  | Same; needs k (see below) |

- **Pitch:** decoded note index (after the octave offset) + 23 = MIDI note.
  Needs no params.
- **Velocity:** from the per-track velocity section when present, else 127
  (Arcade's 128 clamped). Undo pitch compensation by dividing by
  `2 ** (-(n - 37) / 12 * k)` (n = Arcade note index), round, clamp 1–127.
  k comes from `-p`, or the bundled default's k if `-p` is omitted (log which).
  Approximate either way because of the original rounding and clamping.
- **Drum map (fixed).** The converter used to number each drum track's drums
  from 0 while building one shared `midiDrumNoteMap`, so the map was wrong
  once a song had two drum tracks. All drum notes go into one drum track,
  which only splits at the 16-bit note-byte limit, so it took a long drum
  part to hit this. Drum indices are now numbered once for the whole song
  (sorted MIDI drum notes), and each drum track carries the song-wide list
  up to the highest index it uses. So `midiDrumNoteMap[i]` is right for
  every drum track and `recover-midi` can trust it. Songs with one drum track
  encode exactly as before. The map stays a flat array, which is what
  Song-Visualizer (`prepareDrumTracks`) expects, so it needs no change.
  `code_extra` files from older converter versions with several drum tracks
  can still have a wrong map. If such a map doesn't fit (an index past its
  end), fall back to matching each track's drums against the params and log
  it.
- **Merging:** tracks with the same program (range splits, note-byte splits,
  and in future layered tracks; see `params fit`) merge back into one MIDI
  channel. ~~When merging, drop exact duplicate notes (same start, end,
  pitch), which is what layering produces.~~ **Superseded:** duplicates are
  kept, and same-pitch notes that can't share a channel go to extra lanes (see
  "Status: done" above).
- **Timing:** write one tempo event (120 BPM) at 480 ticks/beat; song tick t
  → MIDI tick `t * 480 // 24`. Note lengths are the quantized, gate-fixed ones.
- **Channels:** drums on channel 10 (index 9). Melodic programs on the other
  15 channels of port 0. If there are more than 15 programs, the extra
  programs go on a **second MIDI port** (a separate MIDI track with a
  `midi_port` meta event, port 1, same channel layout, drums only on port 0),
  and so on for every further 15; log it at INFO. (**As built:** programs that
  don't overlap in time share channels first, extra drum lanes use channel 10
  of further ports, and every note track names its port once there are
  several; see "Status: done".) Check that the converter's
  parser reads `midi_port` back (it resolves per-port/channel state), so the
  recovered file converts again.
- Library: `song_to_midi/__init__.py::song_to_midi(text: str, params: ParamsInput = None) -> bytes`
  plus the text-extraction helper (maybe `utils/song_text.py`, shared with
  `render`). Not re-exported from the package root unless it proves generally
  useful.
- **Tests:** `song_to_midi(convert(fixture).to_typescript("code_extra"))`
  recovers programs 0 and 48, drum notes 36/38/42 and the lyrics (including
  `café`); the plain-hex path recovers the same programs and drums with the
  bundled params; every note start matches the fixture within one song tick;
  converting the recovered MIDI again gives a song with the **same notes and
  timing** (same tracks, note events, pitches, start/end ticks) and every
  velocity within ±N of the original (pick N from the observed rounding error,
  e.g. 2). Exact hex equality is not expected: undoing pitch compensation
  rounds and hits the 1–127 clamp, and re-applying the gate fix may not be
  idempotent. A `tune sample` output is rejected as multi-song; a file with no
  literal is rejected. (**As built:** the tests compare every played note as
  (program, start, end, pitch, velocity) and require an exact match, since
  the observed velocity error was 0. Track assignment and chord order may
  change.)

---

## 4. Synthesizer (`audio.py`) and `render` / `tune ... --wav`

```
midi2mkcd render INPUT [-p PARAMS] -o OUT.wav [--rate 48000] [--volume 128] [--no-velocity] [-v]
    # --volume: music.volume(), 0-255, default 128 (song_events' global_volume)
    # INPUT: a .mid file (converted first, with -p), or a .ts/.txt file with exactly one hex`...`
midi2mkcd tune replace INPUT.mid ... [--wav OUT.wav]
midi2mkcd tune sample  INPUT.mid ... [--wav OUT.wav]
```

### Library (`audio.py`, numpy, `audio` extra)

```python
def note_instructions(instrument: Instrument, note_index: int, gate_ms: float, volume: float) -> bytes
def drum_instructions(drum: DrumInstrument, volume: float) -> bytes
def song_events(song: Song, *, use_velocity: bool = True) -> list[tuple[float, bytes]]   # (start ms, instructions)
def synthesize(events: Iterable[tuple[float, bytes]], rate: int = 48000) -> np.ndarray      # float32 mono
def render_song(song: Song, rate: int = 48000, *, use_velocity: bool = True) -> np.ndarray
def to_wav_bytes(samples: np.ndarray, rate: int) -> bytes                                   # stdlib `wave`
```

Stage 1 (`note_instructions`, `drum_instructions`, `song_events`) is pure
Python with no numpy, so it can be tested and reused without the extra. It
returns the **same bytes** as `song.ts`. Stage 2 (`synthesize`) needs numpy.

### Stage 1 status (2026-11-23)

The line-by-line pxt ports are done and live in `arcade/`, next to the other
pxt ports (`music.py`, `music_types.py`), not in `audio.py`. `audio.py` keeps
the names above as thin wrappers over them, and must import numpy lazily
(inside `synthesize` and below) so Stage 1 works without the `audio` extra.

- `arcade/sim_song.py`: `render_instrument(instrument, note_frequency,
  gate_length, volume)` and `render_drum_instrument(drum, volume)`, with
  private helpers that copy JS number semantics (`_js_to_int32` for `| 0`,
  NaN-propagating `Math.max`/`Math.min`, division by zero). The returned
  buffers keep `song.ts`'s unused zero-filled slots at the end. A drum with no
  steps raises `ValueError` (it throws in `song.ts`).
- `arcade/sim_sequencer.py`: `FREQUENCIES`, `note_frequency` (NaN past the
  table, including negative indices), `tick_to_ms`, `metronome_interval_ms`
  (the truncated `setInterval` delay, 20 ms for converted songs),
  `melodic_track_volume` / `drum_track_volume`, `max_tick`, and
  `sequence_song(song, global_volume=128) -> list[SequencedSound(tick,
  track_index, instructions)]`.
- `onTick` behaviour that `sequence_song` reproduces, found while porting:
  the per-track scan **stops at the first event starting after the current
  tick**, so an event listed after a later-starting one never plays (encoded
  songs are sorted, so only hand-made buffers hit this); tick 0 plays even
  with 0 measures; an empty drums list still makes a drum track (an empty JS
  array is truthy); a drum index past the list, or a drum with no steps,
  throws inside the async play call and is silent (skipped, DEBUG log).
- `Sequencer.globalVolume` starts at 1024, but games set it to
  `music.volume()` (default 128), hence `sequence_song`'s default.
- **`audio.py` Stage 1 is written** (2026-11-23), not re-exported from the
  package root:
  - `note_instructions(instrument, note_index, gate_ms, volume)` is
    `render_instrument` with `note_frequency(note_index)`.
  - `drum_instructions(drum, volume)` is `render_drum_instrument` (raises
    `ValueError` for a drum with no steps).
  - `song_events(song, *, use_velocity=True, global_volume=128) ->
    list[(start_ms, instructions)]`: `sequence_song`, with start = tick ×
    `metronome_interval_ms` (20 ms) counted from the first tick.
    `use_velocity=False` copies the song with every velocity cleared (the
    input isn't changed). It logs at INFO how many melodic notes are outside
    the frequency table (they play at 0 Hz). `global_volume` is what
    `render --volume` will pass.
  - Tests: `tests/unit/test_audio.py`; `test_import_hygiene.py` now also
    checks that `import midi2mkcd` doesn't import `midi2mkcd.audio`, and that
    importing `midi2mkcd.audio` doesn't import numpy, scipy or cma.
  - Stage 2 goes in the same module: `synthesize` takes `song_events`'s
    output. Import numpy inside the Stage 2 functions only, and give a
    missing numpy the plan's "needs numpy: pip install ..." error.

Stage 2 implementation notes:

- Per instruction: build per-sample frequency and gain as linear ramps;
  phase = running `cumsum(freq / rate)` (cycles), carried across instructions
  of the same waveform within a note (one generator per waveform per note, as
  in the simulator). **Place instructions at absolute sample positions**:
  keep a running total of ms from the note's start (and the note's start ms
  from `song_events`) and convert each boundary with `round(total_ms * rate
  / 1000)`, as Web Audio schedules at absolute times. Don't round each
  instruction's length separately: at 44.1 kHz (44.1 samples per ms) that
  drifts.
- Mix each note into one preallocated output buffer as it is synthesized
  (the length is known up front: the latest note start + its instructions'
  total duration). Don't keep per-note arrays: a 3-minute song is ~35 MB of
  float32 at 48 kHz, plus ~17 MB of 16-bit WAV bytes. (`song_events` itself
  is small: ~230 bytes per sound, ~3 MB for a 14k-event song, measured.)
- Triangle/saw/sine: Web Audio oscillators are band-limited. Pure sine is
  exact; for triangle and saw use band-limited synthesis (additive up to
  Nyquist, or PolyBLEP) to match the simulator's smoothness. Squares and the
  noise buffers are **not** band-limited in the simulator: implement them as
  looped-buffer playback with linear interpolation at
  `playbackRate · (buffer rate)`, as Web Audio does (Chrome interpolates
  linearly).
- Gains: `(v / 1024) / 4`, × 0.5 for squares; reproduce the 15 ms
  `setTargetAtTime` fades (exponential with time constant 0.015 s) at waveform
  changes and at the end.
- Mix all notes as float32 (measured: chord rms grows +3.0 dB per doubling
  up to 32 notes, a plain sum with no cap or ducking). Write WAV as 16-bit
  PCM with hard clipping at ±1 (what the output device does); log a WARNING
  with the peak and the number of clipped samples if any. At the default
  volume a full-level sine peaks at ~0.12, so clipping needs roughly 8 or
  more loud notes at once. Consider a `--normalize` flag later; not by
  default.
- Chrome's built-in `sawtooth` is normalised so its band-limited overshoot
  (peak ≈ 1.18) fits in ±1: measured 1.5 dB below an ideal ±1 saw
  (20·log₁₀ 1.18 = 1.4). Scale the band-limited saw the same way. Triangle
  has no overshoot and measured exactly as modelled.
- Render until every note's instructions have finished (`stop(true)` keeps
  sounding notes, so the song's last tick doesn't cut them), and stop each
  note at the end of its own instructions (see "But the note is stopped at
  its end" in section 2, Stage 2).
- Triangle and sawtooth: additive synthesis up to Nyquist is enough (the
  measured levels match ideal band-limited waves; Chrome's exact wavetable
  partial counts per octave don't need copying).
- Velocity is honoured by default, since stable Arcade reads the velocity
  section. `--no-velocity` (`use_velocity=False`) treats every event as
  velocity 128, to hear the song as older Arcade versions (which ignore the
  section) play it.

### CLI behaviour

- **Input type is decided by content, never the extension:**
  1. Starts with `MThd`: a MIDI file. Run `convert()` (honouring `-p`) and
     render `result.song`.
  2. Starts with `RIFF` and has form type `RMID` at bytes 8–11: a RIFF MIDI
     (`.rmi`). Fail with a `Midi2MkcdError` saying RIFF MIDI isn't supported
     (and that the inner MIDI can be extracted with another tool).
  3. Otherwise decode as UTF-8 and extract the single ``hex`...` `` literal
     (shared helper from `recover-midi`). A decode failure or no literal is a
     `Midi2MkcdError` saying the input is neither MIDI nor a song file.
  - **`-p` with a text input fails** (`Midi2MkcdError`, exit 1): the
    instruments are already in the song, so `-p` would silently do nothing.
    It can't be an argparse usage error, since the type is only known after
    reading the file.
  - Test each branch: MIDI, `.rmi`, text with one literal, text with none,
    binary garbage, text input plus `-p`.
- `tune replace --wav OUT.wav` renders the replaced song instead of (not as
  well as) the text output. `--wav` is **mutually exclusive** with `-o`,
  `--copy` and `-f` (argparse mutually exclusive group, so it is a usage
  error, exit 2); test it. Same for `tune sample --wav`. `tune sample --wav` renders the
  sampler's songs one after another with ~1 s of silence between them and
  logs which instrument starts at which time (e.g. `0:00 program 40`,
  `0:12 program 41`). The sampler renders its `ConversionResult`s directly,
  so the single-song rule for text input doesn't apply.
- This is probably the biggest single win: the tuning loop no longer needs a
  paste into Arcade.

### Validation (the real cost)

1. **Stage 1, exact. Done (2026-11-23).** It lives next to `make_fixture.py`
   in `tests/fixtures/`, not under `tools/`:
   - `make_sim_cases.py` (Python) writes `sim_cases.json`: 591 note cases
     (every bundled instrument at 4 note/gate/volume combinations, every
     waveform, envelope edge cases including 0 and 65535, gates of 0, one
     tick, 21 ms and negative, pitch envelopes, each LFO at frequencies 0–255,
     an undefined frequency, fractional volumes), 188 drum cases (every
     bundled drum at 3 volumes plus edge cases), and 5 song cases (the
     converted fixture at volumes 128/255/2000, an edge-case song, a
     0-measure song). Inputs use pxt's camelCase JSON shapes; songs are
     encoded hex.
   - `make_sim_goldens.mjs` (Node ≥ 22.13, no npm packages; not run in CI)
     reads `song.ts` and `sequencer.ts` with `git show <commit>:<path>` from a
     pxt clone given on the command line, removes the `namespace` wrapper
     and `export`s (Node's type stripping can't do namespaces; it fails if
     the source doesn't look as expected), strips types, and runs them. Songs
     are decoded with pxt's own `decodeSong` and played by the real
     `Sequencer` (dummy metronome, stubbed `playInstructionsAsync`). Writes
     `golden/sim_instructions.json` (the commit, the frequency table, every
     output as hex, and each song's silent failures).
   - `tests/unit/test_sim_goldens.py` checks the ports against it: all 784
     cases match byte for byte (Python's `math.cos` and V8's agree on every
     LFO case). A 0.1% envelope perturbation fails 131 of them.
   - The pxt clone used: `C:\Users\ckyiu\Documents\Projects\pxt` (blobless).
     To re-port from a newer pxt, update `PXT_COMMIT` in the script, the test
     and the ports' header comments, then regenerate.
   - The `audio.py` wrappers, once written, only need small tests of their
     own (`song_events` timing and `use_velocity`).
2. **Stage 2, perceptual.** Build a few test songs (one per waveform at low,
   middle and high notes; envelope and LFO cases; each drum type), record them
   from the simulator by hand into `testing/` (gitignored), and compare
   spectrograms and RMS envelopes with a script. A screen recording works:
   `ffmpeg -i rec.mkv -map 0:a:0 -ac 1 -ar 48000 -c:a pcm_f32le rec.wav`,
   then analyse with numpy/scipy via `uv run --with scipy`. Put a few marker
   notes before the song so its start is easy to find, and remember note
   starts are 20 ms per tick (Stage 0).
   **Calibration song:** `testing/calibration/make_calibration.py` (local,
   gitignored) writes `calibration.ts`, `calibration_params.yaml`,
   `calibration.mid` and `manifest.json` (every event's section, label and
   start/end song tick). It plays 4 marker beats, then: levels (all 13
   waveforms with a flat envelope at velocity 127/64/32, each group after a
   reference sine), polyphony (sine chords of 1–32 notes), every bundled drum
   once, and envelope/LFO cases (amp LFO 4 Hz and 20 Hz, pitch LFO at a low
   and a high note, a pitch envelope, attack 0, release 0, a 1-tick gate
   shorter than the attack). About 98 s in the simulator. Its recording is
   the reference to compare `render` against.
   **First calibration recording (2026-11-20, Chrome, OBS, 48 kHz):**
   - **Unusable for levels.** Something in the capture chain (a Windows
     audio enhancement such as Loudness Equalization, a device effect, or an
     OBS filter; pxtsim itself has no compressor) rode the gain: identical
     reference sines measured −4.3/−8.4/−5.8 dB, notes after a gap started
     loud and sagged, and velocity 32 came out as loud as 127. So waveform
     levels, the `/4` and square halving, velocity linearity and polyphony
     loudness are **still unverified**. Re-record with enhancements and OBS
     filters off (or OBS "Application Audio Capture" of Chrome, which takes
     the app's stream before device effects).
   - **Timing:** after the first note, every onset is 141 ms earlier than
     "first onset + 20 ms × tick", with no drift over 100 s. So 20 ms per
     tick holds, and the **very first note of a song starts ~140 ms late**
     (audio start-up latency; `render` can ignore it).
   - **Polyphony:** all 32 notes of the 32-note chord sound at once (each
     note's spectral peak present): **no voice cap** in the live simulator.
   - **Amp LFO 4 Hz:** 4.00 Hz modulation, deep. **Amp LFO 20 Hz:** no 20
     Hz modulation (1.2 dB ripple), as predicted: the 50 ms LFO points land
     on whole cycles, `cos(2π·20·0.05k) = 1`, so the LFO is a constant.
   - **Pitch LFO 6 Hz ±10 Hz:** 6.03 Hz vibrato of ±10 Hz at both MIDI 36
     (55–75 Hz) and MIDI 84 (1038–1057 Hz), confirming Hz, not cents.
   - **Pitch envelope +200 Hz (A200 D300 S0):** 262 → 460 Hz at 200 ms,
     back to 262 Hz by ~500 ms, as modelled.
   - **Attack 0 / release 0:** both play normally (no silence or NaN
     glitch); release 0 ends ~75 ms after the gate (10 ms ramp plus the
     15 ms-time-constant fade).
   - **1-tick gate shorter than a 200 ms attack:** sounds for ~330 ms and
     peaks ~150 ms in, where the model predicts a peak at the gate (~21 ms)
     and silence by ~290 ms. Inconclusive because of the gain riding; the
     Stage 1 goldens from the real `song.ts` will settle it. (Settled: see
     the short-gate note in section 2, Stage 1.)
   **Second calibration recording (same song, OBS "Application Audio
   Capture" of Chrome):** the same gain riding, nearly to the dB (reference
   sines −4.5/−8.4/−6.4 dB). pxt has no compressor in the song path: the
   sequencer volume is `global × track/1024 × velocity/128`, songs only use
   `playInstructions.ts` (the spatial/worklet path needs
   `setSpatialAudioPlayer`, which only the spatial API calls), and no
   `DynamicsCompressor` exists in pxt, pxt-common-packages or pxt-arcade. So
   it is probably in Chrome's output or Windows before OBS sees it.
   `testing/calibration/make_chain_test.py` writes `chain_test.wav`: fixed-level
   262 Hz bursts (−3 to −21 dB and back), a 6 s steady tone and loud-after-quiet
   pairs, with no MakeCode involved. Recording it played in Chrome the same
   way shows whether the chain changes levels. The first note started 230 ms
   late this time (141 ms before), so that start-up delay varies.
   **Chain test result (Chrome incognito tab, same OBS app capture):** the
   capture chain compresses heavily, so the **simulator is not the cause**.
   File levels −6 to −24 dB (18 dB range) recorded as −1 to −10 dB (about
   9 dB): quiet bursts boosted ~15 dB, loud ones ~5 dB, peaks up to 1.4
   (past full scale). It behaves the same in both directions and on
   loud-after-quiet pairs, and the steady tone drifts up ~1 dB over 6 s.
   Chrome doesn't compress its output, so suspect a Windows per-app or
   device effect (Audio enhancements / Loudness Equalization, a vendor
   suite such as Realtek Audio Console, Dolby, Nahimic, Sonic Studio or
   MaxxAudio) or an OBS filter. **Rule: re-run the chain test first; only
   trust level measurements from a setup where "recorded − file" is
   constant to within ~0.5 dB.**
   **Fixed:** the culprits were Windows "Audio enhancements" and "Dolby
   Atmos for Headphones". With both off, every burst, the steady tone and the
   loud-after-quiet pairs record at the file level **+3.00 dB (±0.01)**. The
   constant +3 dB is from mono→stereo playback and the stereo→mono mixdown
   in analysis. Ratios inside one recording are exact; for absolute
   simulator levels, subtract 3.01 dB, but confirm that Web Audio output
   gets the same +3 dB as an `<audio>` element (for example, check that a
   flat sine at velocity 127 measures ≈ −3.1 dBFS + 3.01 dB). The
   calibration song must be re-recorded with this setup. (Done: recording 3
   below, kept as `testing/calibration/calibration_recording_3_clean.mkv`.)
   **Third calibration recording (clean chain; the reference for Stage 2):**
   - The +3.01 dB applies to Web Audio too: the model plus 3.01 dB matched
     the reference sine (−18.17 dB) and the single chord note (−36.26 vs
     −36.3 dB).
   - **Levels at velocity 127, relative to a sine (measured / modelled):**
     triangle −1.76/−1.76, sawtooth −3.29/−1.79 (Chrome's normalisation,
     see Stage 2 notes), noise_tunable −1.07/−1.34, noise −2.84/−1.78,
     squares −3.03 to −3.10/−3.02, cycle_16 −0.58/−0.60, cycle_32
     −1.13/−1.03, cycle_64 −1.39/−1.48. The modelled values come from
     simulating the exact pxtsim buffers with linear interpolation (and
     ideal band-limited triangle/saw). Noise's extra −1 dB is the AAC
     encoder's ~19 kHz low-pass removing part of white noise's energy, not
     the simulator; compare noise against an uncompressed render.
   - **Global volume:** music.volume() 128 makes everything 18 dB quieter
     than full scale (Stage 0).
   - **Velocity:** exactly linear for all 13 waveforms.
   - **Polyphony:** a plain sum (+3.0 dB per doubling), no cap.
   - **Short gate (1 tick, attack 200 ms, release 200 ms):** the amplitude
     keeps rising linearly for the **whole 200 ms attack**, well past the
     21 ms gate, then falls to silence by ~220 ms (gate + release). So the
     "after the gate, ramp from the gate's value to 0" reading in Stage 1 is
     wrong for gates shorter than the attack. Port `song.ts` exactly and let
     the Stage 1 goldens include this case. The note is also very quiet.
     (Done: the port reproduces exactly this shape; see section 2, Stage 1,
     and the `env_a200_d0_r200_gate21` golden case.)
   - **Attack 0** works (instant start, ends at gate + release). **Release 0**
     ends ~8 ms after the gate (the 10 ms ramp to 0).
   - **LFOs and pitch envelope** as in recording 1 (4.00 Hz amp LFO; no
     modulation at 20 Hz; 6.03 Hz ±10 Hz vibrato; 262→457→262 Hz bend).
   - **Drums** last their step total: median 0 ms, 80% within ±12 ms.
   - Ticks: 20 ms, drift 3 ms per 1000 ticks; the first note was ~55 ms
     late this time (so 55–230 ms, varies).
   **What is on disk now (2026-11-23):** `testing/calibration/` (gitignored)
   holds the generators (`make_calibration.py`, `make_chain_test.py`), the
   song files (`calibration.ts`, `calibration.mid`,
   `calibration_params.yaml`, `manifest.json`), `chain_test.wav`,
   `chain_test.txt`, and **`calibration_recording_3_clean.mkv`**: the third
   calibration recording above (clean chain, OBS, 104.7 s, 48 kHz stereo),
   copied from the user's OBS folder. **It is the Stage 2 reference; no new
   recording is needed.** Extract its audio with
   `ffmpeg -i calibration_recording_3_clean.mkv -map 0:a:0 -ac 1 -ar 48000
   -c:a pcm_f32le calibration_recording_3_clean.wav`, and remember the
   capture chain's constant +3.01 dB (subtract it for absolute levels;
   ratios within the recording are exact) and that its first note started
   ~55 ms late (align on the marker beats, not on time 0).
   The other OBS recordings from 2026-11-20 are still only in the user's
   `OneDrive\Videos` folder, named `OLD_2026-11-20 HH-MM-SS.mkv`. Going by
   time and length (not checked by ear): 22-13-24 (55.5 s) the range test,
   23-05-25 and 23-15-33 (103.3 s) calibration recordings 1 and 2 (levels
   unusable), 23-34-09 (30.0 s) the compressed chain test, 23-43-09 (24.7 s)
   the clean chain test, 23-50-41 recording 3 (the one copied here).
   **Not written yet:** the comparison script. Put it under
   `testing/calibration/`: align onsets from the marker beats and
   `manifest.json`, then compare each section's RMS envelope and spectrum
   with `render`'s output of `calibration.ts`.
   Expect several "slightly off"
   rounds. Optionally automate recording later (Playwright + a `MediaRecorder`
   on the page's audio), but don't block on it.
3. Tests in CI: stage-1 goldens; stage 2 sanity checks (a sine note's peak
   frequency and level, silence before the first note, total length, a drum
   is shorter than a held note, clipping warning fires on a loud chord,
   WAV header fields).

---

## 5. `params soundfont`: params → SF2

```
midi2mkcd params soundfont [-p PARAMS] -o OUT.sf2 [--notes RANGE] [--step N] [--rate N]
                           [--velocity-layers N] [--allow-incomplete] [-v]
```

Lets the user hear Arcade-ish instruments in a MIDI editor while writing a
song. Lives under `params` because it exports a params file, not a song.

### Approach

- Bank 0 presets 0–127 for melodic programs; bank 128 preset 0 for the drum
  kit (one zone per drum note 27–87, one-shot samples).
- Render each sample with stage 1 + stage 2 of `audio.py` directly
  (`note_instructions` with the Arcade note index `midi - 23` and
  `volume = 1024`, i.e. velocity 128 at full global volume, for the best
  resolution; then scale the sample down to the simulator's default level,
  `music.volume()` 128, i.e. × 1/8, so the SF2 sounds as loud as the editor,
  or leave the headroom to the MIDI editor and document it), not by pushing a one-note MIDI file
  through `convert()`. That is much faster for ~12k samples and avoids the
  velocity clamp. Apply pitch compensation as a per-sample gain
  `2 ** (-(midi - 60) / 12 * k)` (factor the scaler out of
  `timeline_apply_pitch_compensation` so both use one function). Known
  difference: Arcade clamps compensated velocities at 127, so loud low notes
  are louder in the SF2 than in Arcade; velocity layers can model that if
  needed.
- **Default note range MIDI 23–119** (the simulator's frequency table).
  Outside it the simulator gives no pitch (open question 1, measured), so
  leave those keys unmapped in the SF2.
- `--step N` (**default 1**: every note rendered) renders every Nth note and lets the SF2 pitch-shift the rest
  (keyRange + overridingRootKey). Against the simulator this is acceptable:
  triangle/saw/sine are band-limited there anyway, so per-note rendering only
  buys aliasing accuracy for squares and noise. Noise (waveform 5) ignores
  pitch in Arcade, so for noise instruments use `rootKey` with
  `scaleTuning = 0` (generator 56) instead of pitch-shifting.

### Note length: sustain loops

A sample does not know when the key is released. For each melodic sample:

- Render attack + decay, then a **sustain section to loop**. Render the note
  with a long gate (attack + decay + loop length + margin) and cut the loop
  from the steady part.
- With no LFOs and a periodic waveform, the sustain is periodic and loops
  cleanly. With an amp or pitch LFO, make the loop a whole number of LFO
  periods. Loop points rarely land on exact sample boundaries for both the
  waveform and the LFO, so **always crossfade the loop** (e.g. 10–30 ms
  equal-power crossfade baked into the loop end). Noise loops of ≥ 0.5 s are
  fine for preview.
- `sampleModes = 1` (loop continuously). Sustain 0 instruments (plucks that
  decay to silence) can be one-shots (`sampleModes = 0`).
- **Release:** set the SF2 volume-envelope release (`releaseVolEnv`, generator
  38, timecents: `1200 · log2(seconds)`). SF2 releases are linear in **dB**
  (exponential in amplitude), measured to full attenuation (~100 dB), while
  Arcade's release is linear in **amplitude** over R ms. An SF2 release of R
  sounds much shorter than Arcade's R. Start with T ≈ 2·R and tune the factor
  by measuring with FluidSynth (match the time at which each reaches −30 dB,
  say). Set the other volume-envelope generators so the SF2 envelope is
  otherwise flat (attack/decay/hold minimal, sustain 0 cB), since the sample
  already contains attack and decay.

### Velocity

The simulator's velocity is **linear amplitude**, `velocity / 128`
(verified). SF2's default modulator (note-on velocity → initial attenuation,
negative unipolar concave, 960 cB) gives roughly `40·log10(v/127)` dB, i.e.
amplitude ∝ velocity². To get amplitude ∝ velocity, override that default in
each instrument with an identical modulator whose amount is **480 cB**
(an `imod` with the same source, destination, amount source and transform
replaces the default). Verify the result with FluidSynth: players differ
(FluidSynth has an EMU-compatibility attenuation factor), so measure rather
than trust the arithmetic. `--velocity-layers N` (default 1) renders N
velocity layers instead, for exactness at N× the size.

### Size

Mono 16-bit, `--rate 22050` default: ~44 KB per second of sample. With ~1 s
per sample (attack + decay + loop): 128 programs × 97 notes ≈ 12.4k samples
≈ 550 MB at the default `--step 1`; `--step 3` ≈ 180 MB; drums are small.
Instruments with long attacks or decays need longer samples, so the real
size can be larger. The smpl chunk must stay under 4 GB (32-bit RIFF sizes);
fail before rendering if the estimate exceeds that. Log the expected size
at INFO before rendering, and mention `--step` in that message.

### Writing the file

There is no maintained Python SF2 writer worth depending on; write the RIFF by
hand (`soundfont.py`, a few hundred lines, stdlib `struct`, returns bytes):

- `RIFF sfbk` → `LIST INFO` (`ifil` 2.01, `isng` "EMU8000", `INAM`, optional
  `ICMT` with the generator URL and params file name), `LIST sdta` (`smpl`:
  all samples, 16-bit mono, each followed by ≥ 46 zero samples),
  `LIST pdta` (`phdr`, `pbag`, `pmod`, `pgen`, `inst`, `ibag`, `imod`, `igen`,
  `shdr`, each ending with its terminal record).
- Names are 20 bytes, NUL-padded. Use GM names from `utils/gm_names.py`.
- Instrument zones: `keyRange` (43), `velRange` (44) if layered,
  `overridingRootKey` (58), `sampleModes` (54), `releaseVolEnv` (38),
  `sampleID` (53, last). Preset zones: `instrument` (41, last).
- Test by parsing the output back (a small reader in the test, or `sf2utils`
  as a dev-only dependency if it supports 3.14) and, if FluidSynth is
  available locally, rendering a note with it (skip the test otherwise).

---

## 6. `params fit`: optimize params toward a real instrument (stretch goal)

```
midi2mkcd params fit TARGET.sf2 (--program N | --drum NOTE) [-p BASE] -o OUT.yaml
                     [--layers 1|2] [--budget N] [-v]
```

This is a known problem ("synthesizer sound matching" / inverse synthesis).
The parameter space is small, which makes it tractable.

### Target audio

- Render the target with **FluidSynth's CLI** (a system dependency, checked
  at run time with a clear error): generate a test MIDI file with mido, then
  `fluidsynth -ni -R 0 -C 0 -g <gain> -r <rate> -F target.wav TARGET.sf2 test.mid`
  (reverb and chorus off). This avoids writing an SF2 player.
- Test phrase: notes across the instrument's useful range (e.g. 4–6 notes;
  for a known song, the range `tune report` shows), 2–3 velocities, 2–3
  durations (short, medium, held), each note separated by silence so tails
  don't overlap. Render the same phrase through `audio.py` for each candidate.
- For drums: each drum note alone at 2 velocities.

### Search

- Search space per melodic layer: waveform (13 choices, categorical) and 14
  numbers: amp envelope A, D, S, R, amplitude; pitch envelope A, D, S, R,
  amplitude; amp LFO frequency, amplitude; pitch LFO frequency, amplitude.
  Normalise each to [0, 1] with sensible bounds (e.g. times on a log scale up
  to 2000 ms) and round to integers at evaluation.
- Loop over the 13 waveforms; for each, run CMA-ES (`cma`, pure Python) or
  scipy `differential_evolution` over the numbers. Keep the best. The synth
  isn't differentiable (waveform choice, noise, integer truncation), so use
  gradient-free methods. Evaluations are short numpy renders, so thousands
  are fine; parallelise across waveforms with `multiprocessing`.
- Fit overall gain analytically (or as part of the loss) so the instrument's
  loudness matches the target; this also gives a principled per-instrument
  level instead of the global `melodic_pitch_comp_k` hack.
- Drums: the step list has variable length. Try 1–6 steps; per step the
  waveform, target frequency, target volume and duration; plus start
  frequency and volume.

### Loss

Multi-resolution log-magnitude STFT distance plus an RMS-envelope term (phase
is arbitrary, so comparing waveforms directly doesn't work):

$$\mathcal{L} = \sum_{r} \left\| \log\left(|S_r(x)| + \epsilon\right) - \log\left(|S_r(\hat{x})| + \epsilon\right) \right\|_1 + \lambda \left\| \mathrm{dB}(\mathrm{RMS}(x)) - \mathrm{dB}(\mathrm{RMS}(\hat{x})) \right\|_1$$

with $S_r$ the STFT at a few window sizes (e.g. 256, 1024, 4096 samples),
summed over every note of the test phrase.

### Expectations

- A single Arcade voice has no filter: its spectrum is fixed by the waveform
  and cannot get darker as a note decays. Expect the optimizer to match the
  envelope, attack and loudness well and pick the closest waveform; the
  timbre stays chiptune.
- Remember pitch-envelope and pitch-LFO amplitudes are **Hz**, so a vibrato
  fitted at one note is deeper (in cents) on lower notes. Fit across the range.

### Two layers (`--layers 2`): do this last, after everything else

**Deferred.** This is the only part of the plan that changes the params
schema and the converter's output, so it is left until every other feature
(including single-layer `params fit`) is done, and gets its own design pass
then. The notes below are a starting point, not a settled design. Until then,
`params fit` supports only `--layers 1` (or has no `--layers` flag).

Stacking two voices gives a crude timbre change over time (e.g. a bright
square with a fast decay over a sustaining triangle ≈ a plucked or struck
note), and a slightly different pitch LFO or pitch-envelope offset per layer
gives chorus (a constant Hz offset gives a constant beat rate across notes).

- **Params schema change:** an entry may have `layers: [ {...}, {...} ]`
  instead of the single-layer fields. Keep single-layer entries valid
  (backwards compatible). `check_params`, `load_instrument_params`, the
  blank template, `recover-midi` matching and `params soundfont` all need to
  understand layers.
- **Melodic layers need extra tracks:** an `Instrument` is per track, so the
  converter emits one track per layer with the same notes. Costs: track count
  (≤ 255, hard limit), `note_event_count` (toward the 15k simulator warning),
  and song size. `trackInstrumentMap` lists the program for each layer track.
- **Drum layers need no extra tracks:** drum note events already hold several
  notes, so a two-layer drum is two drum indices in the **same** event on the
  same track. No extra tracks or note events; it costs one note byte per hit
  and the extra `DrumInstrument` bytes (drum section length is 16-bit, ≤ 256
  drums per track).
- Clipping: layers add up; the fitted gains account for it, but a loud chord
  of layered notes can still clip (stage 2's clipping warning will show it).
- Fit single-layer first; only add the second layer if the loss improvement
  is audible.

### Testing

**By hand only for now.** `params fit` needs FluidSynth and a real SF2,
which CI doesn't have, so it has no automated tests in this plan. Pure
helpers it uses (the YAML splice, parameter normalisation) may still get
unit tests if they're cheap to write.

### Output

Write a params YAML containing the base file (`-p BASE` or the bundled
default) with only the fitted entry replaced, keeping everything else
byte-identical:

- The params files carry their descriptions in `_comment` keys (the file
  header and every entry), which are ordinary data and survive a load/dump.
  Record the fit there: the entry's new `_comment` is its GM name plus the
  provenance, e.g. `Acoustic Grand Piano (fitted to FluidR3_GM.sf2 program
  0, loss 12.3)`.
- But the files also have `#` section comments (`# Pianos`) and one-line
  flow mappings (`amp_envelope: { attack: 5, ... }`), which PyYAML's dump
  would drop or reformat. So don't dump the whole file: **splice the text**.
  Find the entry's block (from its `  - instrument: N` / `  - note: N` line
  to the line before the next `  - ` or `  #` line at the same indent),
  render the fitted entry in the same style, and replace just those lines.
  If the entry isn't found, append it to its list and log it.
- Load the result back with `load_instrument_params` before writing, as a
  check.
- No new dependency (no `ruamel.yaml`).

Log the loss per waveform and the winner at INFO. Library: `tuning/fit.py` (turn `tuning.py` into a
package, keeping `from midi2mkcd import tuning` and its current names
working).

---

## 7. Open questions (check before relying on them)

1. ~~**Notes outside MIDI 23–119.**~~ **Measured** (2026-11-20) from a
   screen recording of the simulator playing `testing/range_test/range_test.mid`
   (gitignored, built by `make_range_test.py` there): sine (program 4),
   square_50 (16), sawtooth (20) and noise (121) each playing MIDI 60, 21,
   22, 23, 24, 60, 117–121, 127. Results:
   - MIDI 23–119 play at the table frequencies (31, 33, 262, 7040, 7459,
     7902 Hz measured) for every waveform.
   - MIDI 120, 121, 127 (indices 97–104): **sine and sawtooth digitally
     silent**; **square** outputs a DC offset (+0.07 to +0.13) scaled by the
     amp envelope, heard as a thump or click with no pitch; **noise** plays
     exactly as in range.
   - MIDI 21 and 22 never reached Arcade: **the converter dropped them**
     (open question 8, now fixed; they would now play at no pitch, like
     120–127).
   So `render` must reproduce this: 0 Hz for out-of-range indices, which
   the oscillator/buffer model then gives naturally (silent sine/saw/
   triangle, DC for squares at playback rate 0, noise unaffected); log a
   count of out-of-range notes. A converter warning on `result.warnings`
   for out-of-range melodic notes (except noise instruments) is worth
   adding.
2. ~~**Does stable Arcade read the velocity section?**~~ Answered: yes,
   per-chord velocity is now generally available in stable Arcade (README
   updated). `render` honours velocity by default.
3. ~~**Buffer-source frequency ramps.**~~ Answered from `playInstructions.ts`:
   buffer sources set `playbackRate` once at creation and afterwards only
   ramp from the previous value (see Stage 2, Generators).
4. ~~**Multi-drum-track `midiDrumNoteMap`**~~ Confirmed and fixed (see
   `recover-midi`). `tests/integration/test_api.py::test_drum_map_is_right_for_every_drum_track`
   covers it.
5. **SF2 velocity modulator override** and the **release-time factor**:
   measure with FluidSynth and at least one MIDI editor the user actually
   uses.
6. **Dependency wheels for Python 3.14** (numpy, scipy, cma, any SF2 test
   reader). numpy and scipy are expected to have cp314 wheels and cma is
   pure Python; confirm with `uv add` when each is added. (The Pyodide half
   of this question is moot while the web page is out of scope.) **Still
   open; the first Stage 2 step** is adding numpy as the `audio` extra
   (`[project.optional-dependencies]`), and to the dev group so tests and
   mypy see it, then checking that `uv sync` gets a cp314 wheel. Stage 2
   tests that need numpy should `pytest.importorskip("numpy")`.
7. ~~**`render`'s default sample rate.**~~ Answered: the user's simulator
   runs at **48000 Hz**, so `render --rate` defaults to 48000 (the
   recording above was also 48 kHz). Others can check theirs with
   `new AudioContext().sampleRate` in the browser console.
8. ~~**Converter bug: range splitting drops and duplicates notes.**~~ Fixed.
   `timeline_split_tracks_for_ranges` used to cut a too-wide track into
   `[0, 64)`, `[60, 124)` and `[124, 128)`, but in **shifted** note numbers
   (MIDI − 23), so notes below MIDI 23 were dropped and MIDI 83–86 played
   twice. It now covers the notes greedily with the real octave windows
   (`processor.py::_split_by_octave_windows`): every note lands in exactly
   one track, with the fewest tracks. Covered by
   `test_api.py::test_range_splitting_keeps_every_note_once`. Goldens are
   unchanged (the fixture doesn't split by range).

## 8. Corrections to earlier notes (so they are not reintroduced)

- The simulator does **not** synthesize songs with its audio worklet; the
  worklet is only used by the spatial audio player. Songs go through Web
  Audio oscillators and looped buffers (`playInstructions.ts`). So
  triangle/saw/sine are band-limited in the simulator, and the simulator, not
  `melody.cpp`, is the reference.
- The noise generator is xorshift, not an LFSR.
- There are 13 waveforms, not ~20.
- Two-layer drums don't need an extra track (see section 6).
- The "61 samples max" in `processor.py` is the GM drum range, not a format
  limit.
- The "empirical extra octave down" in the pitch shift is explained by the
  frequency table starting at B0 (MIDI 23).
- Audio code goes in `audio.py`, not `render.py` (which is text output).
- A waveform-0 instruction is a silent rest, not the end of the buffer.
- Song ticks are 20 ms in the simulator, not 20.833 ms (setInterval
  truncation), so songs play 4% fast.
