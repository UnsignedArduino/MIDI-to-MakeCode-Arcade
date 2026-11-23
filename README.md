# MIDI-to-MakeCode-Arcade

A Python tool to convert a MIDI file to a MakeCode Arcade song! (Work in
progress) Supports General MIDI 1 (all 128 melodic instruments and the standard
drum kit), the extended drum note range of General MIDI 2, and some Roland GS/
Yamaha XG SysEx commands.

## Install

1. Download and install Python (>=3.14) and the `uv` package manager.
2. Clone this repo.
3. Run `uv sync` to create a virtual environment and install all dependencies.
   Use `uv sync --extra optional` if you also want `--copy` (copy the output to
   the clipboard, which needs `pyperclip`), and `uv sync --extra audio` for
   `render` (which needs `numpy`, and `tqdm` for its progress bar). The dev
   environment `uv sync` makes already has both.

## Usage

Run `uv run midi2mkcd` at the root of the repository in the terminal (it is a
CLI app). `uv run python -m midi2mkcd` works too. Every command is a
subcommand:

* `convert`: convert a MIDI file to Arcade code.
* `recover-midi`: turn converted Arcade code back into a MIDI file (lossy; see
  [Recovering a lost MIDI file](#recovering-a-lost-midi-file)).
* `render`: play a MIDI file or converted song into a WAV file, sounding like
  the MakeCode Arcade simulator (see
  [Listening without Arcade](#listening-without-arcade)).
* `tune replace`, `tune sample`, `tune report`: tools for writing and tuning
  instrument parameter files (see
  [Defining your own instrument parameter file](#defining-your-own-instrument-parameter-file)).
* `params show`, `params check`: print or lint instrument parameter files.

Run `uv run midi2mkcd COMMAND -h` for a command's options. Options go after
the subcommand (`uv run midi2mkcd convert song.mid -v`).

~~As of the time of this writing, this code outputs velocity information properly
but the MakeCode Arcade song playback will ignore it - per chord velocity is
only available in beta.~~ Per-chord velocity is now generally available in 
MakeCode Arcade!

### Example commands

To convert the MIDI file `Never Gonna Give You Up.mid`:

```commandline
uv run midi2mkcd convert "Never Gonna Give You Up.mid"
```

This will print out the song buffer (a `` hex`...` `` literal) which you can
paste into MakeCode Arcade. Only the code goes to stdout; log messages go to
stderr. It uses
[`instrument_params.yaml`](src/midi2mkcd/resources/instrument_params.yaml)
(bundled with the package), which is an instrument parameter file that defines
what each of the general MIDI 1's 128 melodic instruments and standard drum kit
sound like. To use your own instrument parameter file, pass it with `-p`:

```commandline
uv run midi2mkcd convert "Never Gonna Give You Up.mid" -p "my_params.yaml"
```

If the output is too big to easily copy from the terminal, use `-o` to write to
a file (this will **overwrite it**), or `--copy` to also copy it to the
clipboard:

```commandline
uv run midi2mkcd convert "Never Gonna Give You Up.mid" -o "Never Gonna Give You Up.ts"
```

On Windows, prefer `-o` over redirecting with `>`: Windows PowerShell 5.1's
`>` writes UTF-16, which MakeCode Arcade won't accept as pasted text. `-o`
always writes UTF-8.

`-f` (`--format`) picks what is generated:

* `hex` (the default): just the `` hex`...` `` song buffer.
* `code`: a statement that plays the song in Arcade, ready to paste and run.
* `code_extra`: constants for the Song-Visualizer project (see below).

If you would like to visualize your song output, you can import
https://github.com/UnsignedArduino/Song-Visualizer and replace the code at the
top with the output of this command:

```commandline
uv run midi2mkcd convert "Never Gonna Give You Up.mid" -f code_extra
```

`-f code_extra` writes out extra information that the visualizer above can use
to make the visuals more accurate.
[Here](https://arcade.makecode.com/17599-66404-81028-76431) is an example. In
the visualizer, you can press B to switch
through the main interface, the track viewer (use arrow keys to highlight a
track if desired), and the lyric viewer. Any lyrics (lyric meta message only so
far) in the MIDI file will also be exported.

Add `-v` to any command for debug logging (and full tracebacks for errors).

### Recovering a lost MIDI file

If you only have the converted code, `recover-midi` turns it back into a MIDI
file (`-o` is required):

```commandline
uv run midi2mkcd recover-midi "Never Gonna Give You Up.ts" -o "recovered.mid"
```

The input is a text file holding exactly one `` hex`...` `` song, i.e. the
output of `convert` in any format (not `tune sample`, which holds several).
It is lossy: timing is quantized to the song's ticks (written at 120 BPM),
note lengths include the converter's gate fix, and velocities are approximate.
A `-f code_extra` file gives the exact instruments, drum notes and lyrics.
From plain hex, instruments and drums are found by matching each track
against the instrument parameter file (pass the one you converted with as
`-p`), and lyrics are lost. More than 15 instruments spill onto a second MIDI
port.

### Listening without Arcade

`render` synthesizes a song the way the MakeCode Arcade browser simulator
plays it (Chrome's Web Audio) and writes a mono 16-bit WAV file (`-o` is
required). It needs numpy (`uv sync --extra audio`, or
`pip install "midi2mkcd[audio]"`).

```commandline
uv run midi2mkcd render "Never Gonna Give You Up.mid" -o "never.wav"
uv run midi2mkcd render "Never Gonna Give You Up.ts" -o "never.wav"
```

The input is a MIDI file (converted first; `-p` picks the instrument
parameter file) or a text file holding one `` hex`...` `` song. The file's
content decides which, not its extension. `--volume` is the game's
`music.volume()` (0-255, default 128 like Arcade, which plays songs 18 dB below
full scale), `--no-velocity` plays every note at full velocity (as older Arcade
versions did), and `--rate` sets the sample rate (default 48000 Hz).

Like the simulator, a converted song plays about 4% fast (its ticks are 20 ms
apart instead of 20.83 ms). Checked against a recording of the simulator, the
render matches to within about 0.1 dB in level, tone and timing. The simulator
itself sometimes starts a short sound a few ms late or cuts it a few ms early,
and that random timing isn't copied. Loud passages that would clip are clipped
in the WAV with a warning. Rendering takes a while on long songs (roughly 300
sounds per second), so a progress bar is shown on stderr when it's a terminal
and `tqdm` is installed (it comes with the `audio` extra).

### Simulator crash risk (WebAudio memory leak)

Long or dense songs can crash the MakeCode Arcade **browser simulator** (the
"Aw, Snap!" page). It seems like the simulator's WebAudio playback leaks a few
KB of memory per note (pxtsim keeps every note's audio nodes alive for the
whole simulator session), and Chrome kills the renderer process when it runs
out of memory.

The limit is **not a fixed note count** — it depends on how much memory the
rest of your game uses (sprites, arrays, etc.), which browser you use, and how
many notes the simulator has already played. Measured on a modest test game
(Aug 2026): crashes started around 24,000–24,500 cumulative notes in Chrome,
but a memory-heavy game may crash earlier, while Firefox tolerates more (at the
cost of worse performance). Notes from previous plays or other songs in the
same page session all count toward the limit; **reloading the simulator resets
it**.

The converter logs a warning when a song has 15,000 or more note events.

### MIDI standards/features supported

Standards:

* [General MIDI 1](https://en.wikipedia.org/wiki/General_MIDI) melodic
  instruments (0-127) and standard drum kit notes (35-81)
* [General MIDI 2](https://en.wikipedia.org/wiki/General_MIDI_Level_2) extended
  drum note range (27-87) on the standard drum kit only
* Roland GS/Yamaha XG SysEx messages for track switching to standard drums only

Features:

* Tempo changes
* MIDI ports and channels
* Common control changes and Roland GS/Yamaha XG SysEx messages to switch a
  track to a drum track and back (will only use the standard drum kit notes)
* Per note velocity (stored per chord in the song)
* Lyric meta messages

### Defining your own instrument parameter file

If you would like to create your own instrument parameter file, start from a
copy of the bundled one, which was tuned by me, or from a blank template:

```commandline
uv run midi2mkcd params show -o "my_params.yaml"
uv run midi2mkcd params show --blank -o "my_params.yaml"
```

The bundled file is
[`instrument_params.yaml`](src/midi2mkcd/resources/instrument_params.yaml);
its header comment documents the YAML format and units. In the
[`examples`](examples) directory,
[`instrument_params_2.yaml`](examples/instrument_params_2.yaml) was generated
completely by Claude and sounds interesting.

`params check` lints a file without playing anything and prints one problem per
line (out-of-range values, invalid or duplicate entries, and General MIDI
instruments or drum notes with no entry). It exits with status 1 if it found
any:

```commandline
uv run midi2mkcd params check -p "my_params.yaml"
```

To see what a song needs, `tune report` lists the instruments and drum notes it
uses (with their General MIDI names), their note and velocity ranges, and
whether your file has an entry for each:

```commandline
uv run midi2mkcd tune report "basic_test.mid" -p "my_params.yaml"
```

```
kind     number  name                  notes  range  velocity  mapped
melodic  0       Acoustic Grand Piano  14     53-67  60-100    yes
melodic  48      String Ensemble 1     3      48-55  80-80     NO
drum     36      Bass Drum 1           4      36-36  100-100   yes
```

You can use something like
[SpessaSynth](https://spessasus.github.io/SpessaSynth/) as it is a simple web
app that you can use to reference your favorite MIDI sound font files when
defining your own file. (it is a good MIDI visualizer as well)

All `tune` commands take `--allow-incomplete`, which skips incomplete or
invalid entries in the `-p` file instead of failing, since the file you are
working on may not have every instrument defined yet. (A song that uses a
skipped instrument still fails.)

#### Defining melodic instruments

This tool supports all 128 general MIDI 1 instruments. The following commands
can be helpful as you write or tune melodic instruments, alongside
[this tool](https://arcade.makecode.com/18336-29202-55655-79439) by @riknoll.

`basic_test.mid` can be a simple song written for the piano or any other
instrument. `tune replace --melodics N` plays every melodic note with MIDI
program N (0-127) and outputs code ready to play (`-f code` is the default
here). `--copy` also copies it to the clipboard.

```commandline
uv run midi2mkcd tune replace "basic_test.mid" --melodics 48 -p "my_params.yaml" --allow-incomplete --copy
```

`tune sample --melodics` outputs one program that plays the song several
times, once per instrument, useful to compare multiple instruments in a family
together. Use commas to separate what you want (e.g. `1,2,4-6` -> [1, 2, 4, 5,
6]).

```commandline
uv run midi2mkcd tune sample "basic_test.mid" --melodics 3-6 -p "my_params.yaml" --allow-incomplete --copy
```

#### Defining drum notes

This tool only supports the standard drum kit, so define notes 27 through 87.

The drum counterparts of the commands above are `--drums`: `tune replace
--drums N` plays every drum note as drum note N, and `tune sample --drums`
plays the song once per drum note in a range. (`tune replace` accepts
`--melodics` and `--drums` together.)

```commandline
uv run midi2mkcd tune replace "basic_test_3.mid" --drums 38 -p "my_params.yaml" --allow-incomplete --copy
uv run midi2mkcd tune sample "basic_test_3.mid" --drums 35-40 -p "my_params.yaml" --allow-incomplete --copy
```

### Use as a library

`midi2mkcd` is also an importable library. `convert()` takes MIDI bytes, a
binary file object or a path, and optionally instrument parameters as YAML
text (or a mapping from `load_instrument_params()`); without them it uses the
bundled parameters.

```python
from pathlib import Path

from midi2mkcd import convert

result = convert(Path("song.mid").read_bytes())
print(result.to_typescript("code"))  # "hex" (default), "code" or "code_extra"
for warning in result.warnings:  # e.g. the simulator crash-risk warning
    print(warning)
```

Errors from bad input are subclasses of `midi2mkcd.Midi2MkcdError`:
`InvalidMidiError` (the MIDI could not be parsed), `InstrumentParamsError` (bad
YAML, invalid entries, or an instrument the song uses has no entry) and
`SongConstraintError` (the song can't fit Arcade's song format),
`InvalidSongError` (Arcade song text that can't be read back) and
`MissingDependencyError` (an optional dependency such as numpy isn't
installed). The library only logs through the standard `logging` module (the
`midi2mkcd` logger) and never prints. The tuning tools are in
`midi2mkcd.tuning`, `midi2mkcd.song_to_midi.song_to_midi(text, params=None)`
returns recovered MIDI file bytes, and `midi2mkcd.audio` renders audio:
`render_song(result.song)` gives float32 samples (numpy; pass
`progress=callback` to get `callback(done, total)` per sound) and
`to_wav_bytes(samples)` a WAV file.

### Help text

```commandline
usage: midi2mkcd [-h] [--version]
                 {convert,recover-midi,render,tune,params} ...

Convert a MIDI file to a MakeCode Arcade song.

options:
  -h, --help            show this help message and exit
  --version             show program's version number and exit

commands:
  {convert,recover-midi,render,tune,params}
    convert             Convert a MIDI file to Arcade code.
    recover-midi        Recover a MIDI file from a converted song (lossy).
    render              Render a MIDI file or converted song to WAV, as the
                        simulator plays it.
    tune                Tools for auditioning instrument parameters against a
                        song.
    params              Print or check instrument parameter files.

Run 'midi2mkcd COMMAND -h' for a command's options.
```

```commandline
usage: midi2mkcd convert [-h] [-f {hex,code,code_extra}] [-p PARAMS] [-o OUT]
                         [--copy] [-v]
                         INPUT

Convert a MIDI file to a MakeCode Arcade song.

positional arguments:
  INPUT                 MIDI file to convert.

options:
  -h, --help            show this help message and exit
  -f, --format {hex,code,code_extra}
                        hex: a hex`...` literal.
                        code: a statement that plays the song in Arcade.
                        code_extra: constants for the Song-Visualizer project.
                        Default: hex.
  -p, --params PARAMS   Instrument parameter YAML file. Default: the bundled
                        parameters (see 'midi2mkcd params show').
  -o, --output OUT      Write the output to this file (UTF-8, overwritten if
                        it exists). Default: stdout.
  --copy                Also copy the output to the clipboard (needs the
                        'optional' extra: pip install "midi2mkcd[optional]").
  -v, --verbose         Show debug logging on stderr, and tracebacks for
                        errors.
```

```commandline
usage: midi2mkcd recover-midi [-h] [-p PARAMS] -o OUT [-v] INPUT

Turn a converted song back into a MIDI file, for when the original
is lost. Lossy: timing is quantized, note lengths include the gate
fix, and velocities are approximate.

INPUT is convert's output in any format. A code_extra file gives the
exact programs, drum notes and lyrics; otherwise programs and drums
are found by matching each track's instrument against PARAMS, and
lyrics are lost.

positional arguments:
  INPUT                Text file with one hex`...` song (convert's output).

options:
  -h, --help           show this help message and exit
  -p, --params PARAMS  The instrument parameter YAML the song was converted
                       with, to match instruments and undo pitch compensation.
                       Default: the bundled parameters.
  -o, --output OUT     MIDI file to write (overwritten if it exists).
                       Required: binary output never goes to stdout.
  -v, --verbose        Show debug logging on stderr, and tracebacks for
                       errors.
```

```commandline
usage: midi2mkcd render [-h] [-p PARAMS] [--rate HZ] [--volume 0-255]
                        [--no-velocity] -o OUT [-v]
                        INPUT

Synthesize a song the way the MakeCode Arcade simulator plays it
(Chrome's Web Audio) and write a mono 16-bit WAV file. Needs numpy:
pip install "midi2mkcd[audio]".

INPUT is a MIDI file (converted first, with PARAMS) or a text file
with one hex`...` song (convert's output in any format). The type
is decided by the file's content, not its extension.

Like the simulator, ticks are 20 ms apart, so converted songs play
4% fast.

positional arguments:
  INPUT                MIDI file, or text file with one hex`...` song.

options:
  -h, --help           show this help message and exit
  -p, --params PARAMS  Instrument parameter YAML file for MIDI input. Default:
                       the bundled parameters. Not allowed for song text
                       input, whose instruments are in the song.
  --rate HZ            Sample rate. Default: 48000.
  --volume 0-255       music.volume() in the game. Default: 128, Arcade's
                       default.
  --no-velocity        Play every note at full velocity, as Arcade versions
                       that ignore the velocity section do.
  -o, --output OUT     WAV file to write (overwritten if it exists). Required:
                       binary output never goes to stdout.
  -v, --verbose        Show debug logging on stderr, and tracebacks for
                       errors.
```

```commandline
usage: midi2mkcd tune [-h] {replace,sample,report} ...

Tools for auditioning instrument parameters against a song.

options:
  -h, --help            show this help message and exit

commands:
  {replace,sample,report}
    replace             Convert the song with every melodic and/or drum
                        instrument replaced.
    sample              Play the song once per instrument in a range.
    report              List the instruments and drum notes the song uses.
```

```commandline
usage: midi2mkcd params [-h] {show,check} ...

Print or check instrument parameter files.

options:
  -h, --help    show this help message and exit

commands:
  {show,check}
    show        Print the bundled instrument parameters (or a blank template).
    check       Lint an instrument parameter file.
```

Each `tune` and `params` subcommand has its own `-h` too.
