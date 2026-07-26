# MIDI-to-MakeCode-Arcade

A Python tool to convert a MIDI file to a MakeCode Arcade song! (Work in
progress) Supports General MIDI 1 (all 128 melodic instruments and the standard
drum kit), the extended drum note range of General MIDI 2, and some Roland GS/
Yamaha XG SysEx commands.

## Install

1. Download and install Python (>=3.14) and the `uv` package manager.
2. Clone this repo.
3. Run `uv sync` to create a virtual environment and install all dependencies.

## Usage

Run `uv run python -m midi2mkcd` at the root of the repository in the terminal
(it is a CLI app).

As of the time of this writing, this code outputs velocity information properly
but the MakeCode Arcade song playback will ignore it - per chord velocity is
only available in beta.

### Example commands

To convert the MIDI file `Never Gonna Give You Up.mid`:

```commandline
uv run python -m midi2mkcd -i "Never Gonna Give You Up.mid" -p "example/instrument_params.yaml"
```

This will print out the song buffer which you can paste into MakeCode Arcade.
It will use [
`example/instrument_params.yaml`](examples/instrument_params.yaml),
which is an instrument parameter file that defines what each of the general
MIDI 1's 128 melodic instruments and standard drum kit sound like. An
instrument parameter file is always required.

If the output is too big to easily copy from the terminal, use `-o` to write to
a file: (this will **overwrite it**)

```commandline
uv run python -m midi2mkcd -i "Never Gonna Give You Up.mid" -p "example/instrument_params.yaml" -o "Never Gonna Give You Up.ts"
```

If you would like to visualize your song output, you can import
https://github.com/UnsignedArduino/Song-Visualizer and replace the code at the
top with the output of this command:

```commandline
uv run python -m midi2mkcd -i "Never Gonna Give You Up.mid" -p "example/instrument_params.yaml" --generate-extra-code
```

The `--generate-extra-code` flag will write out extra information that the
visualizer above can use to make the visuals more accurate.
[Here](https://makecode.com/_E75AehCdch5c) is an example (once again, at the
time of writing, per-chord velocity is in beta, so import into the beta editor
if desired). In the visualizer, you can press B to open the track list and use
arrow keys to highlight a particular track if desired. Any lyrics (lyric meta
message only so far) in the MIDI file will also be exported.

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
* Per note velocity (MakeCode Arcade will ignore this for now, but it is
  song's output - MakeCode Arcade beta at the time of writing supports it)
* Lyric meta messages

### Defining your own instrument parameter file

If you would like to create your own instrument parameter file, you can look at
[`instrument_params.yaml`](examples/instrument_params.yaml) in the
[`example`](examples) directory, which was tuned by me, to see what the YAML
format looks like.
[`instrument_params_2.yaml`](examples/instrument_params_2.yaml) was generated
completely by Claude and sounds interesting.

You can use something like
[SpessaSynth](https://spessasus.github.io/SpessaSynth/) as it is a simple web
app that you can use to reference your favorite MIDI sound font files when
defining your own file. (it is a good MIDI visualizer as well)

#### Defining melodic instruments

This tool supports all 128 general MIDI 1 instruments. The following commands
can be helpful as you write or tune melodic instruments, alongside
[this tool](https://arcade.makecode.com/18336-29202-55655-79439) by @riknoll.

`basic_test.mid` can be a simple song written for the piano or any other
instrument. `--test-ask-to-replace-all-melodics-with` will prompt you to
replace it with a melodic MIDI instrument (0-127). `--test-generate-code` will
output code ready to play, and `--test-copy-result-to-clipboard` will work if
`pyperclip` is installed (use `uv pip install pyperclip`). And obviously,
substitute `-p` with the file you are working on.
`--test-force-instrument-param-load` is used as the file you are working on may
not have all instruments defined yet.

```commandline
uv run python -m midi2mkcd -i "basic_test.mid" -p "example/instrument_params.yaml" --test-ask-to-replace-all-melodics-with --test-generate-code --test-force-instrument-param-load --test-copy-result-to-clipboard --debug
```

`--test-sample-melodic-instruments` will replace the melodic instruments in the
MIDI file multiple times and generate a script, useful to compare multiple
instruments in a family together. Use commas to separate what you want (e.g.
`1,2,4-6` -> [1, 2, 4, 5, 6]).

```commandline
uv run python -m midi2mkcd -i "basic_test.mid" -p "example/instrument_params.yaml" --test-sample-melodic-instruments "3-6" --test-force-instrument-param-load --debug
```

#### Defining drum notes

This tool only supports the standard drum kit, so define notes 27 through 87.

Similar to the first command above, `--test-ask-to-replace-all-drums-with` will
prompt you to replace all the drum instruments in a MIDI file with a drum note
(27-87).

```commandline
uv run python -m midi2mkcd -i "testing/basic_test_3/basic_test_3.mid" -p "example/instrument_params.yaml" --test-ask-to-replace-all-drums-with --test-generate-code --test-force-instrument-param-load --test-copy-result-to-clipboard --debug
```

### Help text

```commandline
usage: main.py [-h] --input INPUT
               --input-instrument-params INPUT_INSTRUMENT_PARAMS
               [--output OUTPUT] [--generate-extra-code] [--debug]
               [--test-replace-all-melodics-with TEST_REPLACE_ALL_MELODICS_WITH]
               [--test-ask-to-replace-all-melodics-with]
               [--test-sample-melodic-instruments TEST_SAMPLE_MELODIC_INSTRUMENTS]
               [--test-replace-all-drums-with TEST_REPLACE_ALL_DRUMS_WITH]
               [--test-ask-to-replace-all-drums-with] [--test-generate-code]
               [--test-force-instrument-param-load]
               [--test-copy-result-to-clipboard]

Convert a MIDI file to a MakeCode Arcade song.

options:
  -h, --help            show this help message and exit
  --input, -i INPUT     Input MIDI file.
  --input-instrument-params, -p INPUT_INSTRUMENT_PARAMS
                        Input instrument parameter mapping file.
  --output, -o OUTPUT   Output TypeScript file path, otherwise will write to
                        stdout. If specified, this will overwrite the file if
                        it already exists, and the parent directories must
                        exist.
  --generate-extra-code
                        Generate extra code to help song visualizers display
                        properly. The output will be valid TypeScript code.
                        Incompatible with test options that sample melodic
                        instruments.
  --debug               Include debug messages. Defaults to info and greater
                        severity messages only.

Testing options:
  --test-replace-all-melodics-with TEST_REPLACE_ALL_MELODICS_WITH
                        Replace all melodic tracks in a song with the specific
                        MIDI instrument.
  --test-ask-to-replace-all-melodics-with
                        Prompt the user to replace all melodic tracks in a
                        song with a specific MIDI instrument.
  --test-sample-melodic-instruments TEST_SAMPLE_MELODIC_INSTRUMENTS
                        Pass in a range of MIDI instruments to sample, such as
                        "0,2,4-10" to replicate the song several times and
                        replace all melodic tracks in the song with the
                        specific MIDI instrument. Basically does what `--test-
                        replace-all-melodics-with` and `--test-generate-code`
                        but with a bunch of specified instruments.
  --test-replace-all-drums-with TEST_REPLACE_ALL_DRUMS_WITH
                        Replace all drum notes in a song with the specific
                        drum note.
  --test-ask-to-replace-all-drums-with
                        Prompt the user to replace all drum notes in a song
                        with a specific drum note.
  --test-generate-code  Generate the MakeCode Arcade code to play the song.
  --test-force-instrument-param-load
                        Forcibly load the instrument parameter mapping file,
                        even if it would normally cause errors.
  --test-copy-result-to-clipboard
                        If pyperclip (uv pip install pyperclip) is available and
                        this option is specified, the output will also be
                        copied to the clipboard.
```
