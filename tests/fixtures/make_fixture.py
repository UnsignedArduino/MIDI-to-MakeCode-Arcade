"""
Regenerates fixture.mid, the small MIDI file the golden tests convert.

    uv run python tests/fixtures/make_fixture.py

It covers two melodic channels with different programs, chords, drums on channel 9
(MIDI channel 10), a tempo change, and lyrics, one of them non-ASCII. mido writes
lyrics as latin-1, so the non-ASCII one sticks to latin-1 characters.

If you change this, regenerate the files in golden/ too, and check the new output by
ear in MakeCode Arcade, since they are the regression baseline.
"""

from pathlib import Path

from mido import Message, MetaMessage, MidiFile, MidiTrack, bpm2tempo

TICKS_PER_BEAT = 480
BEAT = TICKS_PER_BEAT
OUTPUT = Path(__file__).parent / "fixture.mid"

PIANO_CHANNEL = 0
PIANO_PROGRAM = 0  # Acoustic Grand Piano
STRINGS_CHANNEL = 1
STRINGS_PROGRAM = 48  # String Ensemble 1
DRUM_CHANNEL = 9

KICK = 36
SNARE = 38
CLOSED_HAT = 42


def track_from_events(
    name: str, events: list[tuple[int, Message | MetaMessage]]
) -> MidiTrack:
    """
    Build a track from (absolute tick, message) pairs, converting to delta times.

    Events at the same tick keep their listed order, so list note offs before note ons.
    """
    track = MidiTrack()
    track.append(MetaMessage("track_name", name=name, time=0))
    now = 0
    for tick, msg in sorted(events, key=lambda e: e[0]):
        track.append(msg.copy(time=tick - now))
        now = tick
    track.append(MetaMessage("end_of_track", time=0))
    return track


def note(
    channel: int, pitch: int, start: int, length: int, velocity: int = 100
) -> list[tuple[int, Message]]:
    return [
        (start, Message("note_on", channel=channel, note=pitch, velocity=velocity)),
        (
            start + length,
            Message("note_off", channel=channel, note=pitch, velocity=0),
        ),
    ]


def conductor_track() -> MidiTrack:
    """Tempo changes and lyrics."""
    events: list[tuple[int, Message | MetaMessage]] = [
        (0, MetaMessage("time_signature", numerator=4, denominator=4)),
        (0, MetaMessage("set_tempo", tempo=bpm2tempo(120))),
        (0, MetaMessage("lyrics", text="Hel")),
        (BEAT, MetaMessage("lyrics", text="lo ")),
        (2 * BEAT, MetaMessage("lyrics", text="café ")),
        # Second bar is faster
        (4 * BEAT, MetaMessage("set_tempo", tempo=bpm2tempo(150))),
        (4 * BEAT, MetaMessage("lyrics", text="world")),
    ]
    return track_from_events("Conductor", events)


def piano_track() -> MidiTrack:
    """A melody, then chords (some notes starting and ending together)."""
    events: list[tuple[int, Message | MetaMessage]] = [
        (0, Message("program_change", channel=PIANO_CHANNEL, program=PIANO_PROGRAM)),
    ]
    melody = [60, 62, 64, 65]
    for i, pitch in enumerate(melody):
        events += note(PIANO_CHANNEL, pitch, i * BEAT, BEAT // 2)
    # C major then G major chords in bar 2, with a quieter F major after
    for pitch in (60, 64, 67):
        events += note(PIANO_CHANNEL, pitch, 4 * BEAT, BEAT)
    for pitch in (55, 59, 62, 67):
        events += note(PIANO_CHANNEL, pitch, 5 * BEAT, BEAT)
    for pitch in (53, 57, 60):
        events += note(PIANO_CHANNEL, pitch, 6 * BEAT, 2 * BEAT, velocity=60)
    return track_from_events("Piano", events)


def strings_track() -> MidiTrack:
    """Held notes that overlap each other and the piano."""
    events: list[tuple[int, Message | MetaMessage]] = [
        (
            0,
            Message("program_change", channel=STRINGS_CHANNEL, program=STRINGS_PROGRAM),
        ),
    ]
    events += note(STRINGS_CHANNEL, 48, 0, 4 * BEAT, velocity=80)
    # Starts before the first note ends, so they overlap
    events += note(STRINGS_CHANNEL, 52, 2 * BEAT, 4 * BEAT, velocity=80)
    events += note(STRINGS_CHANNEL, 55, 4 * BEAT, 4 * BEAT, velocity=80)
    return track_from_events("Strings", events)


def drum_track() -> MidiTrack:
    """Kick and snare with hi-hats on the eighth notes, over both bars."""
    events: list[tuple[int, Message | MetaMessage]] = []
    for beat in range(8):
        start = beat * BEAT
        events += note(DRUM_CHANNEL, KICK if beat % 2 == 0 else SNARE, start, BEAT // 4)
        for eighth in range(2):
            events += note(
                DRUM_CHANNEL,
                CLOSED_HAT,
                start + eighth * BEAT // 2,
                BEAT // 4,
                velocity=70,
            )
    return track_from_events("Drums", events)


def make_fixture() -> MidiFile:
    mid = MidiFile(type=1, ticks_per_beat=TICKS_PER_BEAT)
    mid.tracks.extend([conductor_track(), piano_track(), strings_track(), drum_track()])
    return mid


if __name__ == "__main__":
    make_fixture().save(OUTPUT)
    print(f"Wrote {OUTPUT}")
