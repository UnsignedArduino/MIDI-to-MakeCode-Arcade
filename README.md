# MIDI-to-MakeCode-Arcade

A Python tool to convert a MIDI file to a MakeCode Arcade song! (Work in
progress)

Some bug squashing may be needed but otherwise this tool is complete.

Web version will be available soon in a different repo.

## Install

1. Download and install Python.
2. Clone this repo.
3. Install all the requirements in [`requirements.txt`](requirements.txt)

> You may need to edit commands listed in this repo to use `py` or `python3` if
> `python` doesn't work.

## Usage

Run [`src/main.py`](src/main.py) at the root of the repository in the terminal.
(It is a CLI app)

### Example commands

To convert the MIDI file `Never_Gonna_Give_You_Up.mid` and print the Arcade
song to standard output with the default track "dog", no divisor, (divisor of
1), and no character break.

```commandline
python src/main.py -i "Never_Gonna_Give_You_Up.mid"
```

To convert the MIDI file at the absolute path
`E:\Arcade MIDI to Song\testing\Friend_Like_Me_Disneys_Aladdin.mid` and
write the output to `Friend_Like_Me_Disneys_Aladdin song.ts` in the current
directory with the "computer" track, a divisor of 2, a character break of 512,
and with debug messages on.

```commandline
python src/main.py -i "E:\Arcade MIDI to Song\testing\Friend_Like_Me_Disneys_Aladdin.mid" -o "Friend_Like_Me_Disneys_Aladdin song.ts" -d 2 -t computer -b 512 --debug
```

### Help text

```commandline
usage: ArcadeMIDItoSong [-h] --input INPUT [--output OUTPUT] [--track TRACK]
                        [--divisor DIVISOR] [--break CHAR_BREAK] [--debug]

A program to convert MIDI files to the Arcade song format.

options:
  -h, --help            show this help message and exit
  --input INPUT, -i INPUT
                        Input MIDI file
  --output OUTPUT, -o OUTPUT
                        Output text file path, otherwise we will output to
                        standard output.
  --track TRACK, -t TRACK
                        A track to use, which changes the instrument.
                        Available tracks include ['dog', 'duck', 'cat',
                        'fish', 'car', 'computer', 'burger', 'cherry',
                        'lemon']. (You can also use indices 0-8) Defaults to
                        'dog'.
  --divisor DIVISOR, -d DIVISOR
                        A divisor to reduce the number of measures used. A
                        higher integer means a longer song can fit in the
                        maximum of 255 measures of a song, but with less
                        precision. Must be greater than or equal to 1, and
                        defaults to 1 for no division.
  --break CHAR_BREAK, -b CHAR_BREAK
                        Break the hex string after so many characters.
                        Defaults to 0 for no breaking.
  --debug               Include debug messages. Defaults to info and greater
                        severity messages only.
```
