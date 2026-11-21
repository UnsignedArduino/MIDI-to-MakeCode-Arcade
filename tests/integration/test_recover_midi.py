"""`song_to_midi.song_to_midi`: Arcade song text back to MIDI, and converting again."""

import io
import logging
from collections import Counter
from copy import deepcopy

import pytest
from helpers import FIXTURE_MIDI
from mido import Message, MetaMessage, MidiFile, MidiTrack

from midi2mkcd import (
    ConversionResult,
    InvalidSongError,
    convert,
    default_instrument_params_text,
    load_instrument_params,
)
from midi2mkcd.arcade.music import encode_song_to_hex
from midi2mkcd.song_to_midi import song_to_midi
from midi2mkcd.tuning import sample_melodics

ORIGINAL = convert(FIXTURE_MIDI)

# (program or -1, start tick, end tick, MIDI note or drum note, velocity)
PlayedNote = tuple[int, int, int, int, int | None]


def played_notes(result: ConversionResult) -> Counter[PlayedNote]:
    """
    Every note the song plays. Which track of a program a note lands in can change
    on a second conversion (the polyphony split depends on input order), and so can
    the order of notes in a chord, but neither changes what plays.
    """
    played: Counter[PlayedNote] = Counter()
    programs = result.track_idx_to_midi_instrument
    for track, program in zip(result.song.tracks, programs, strict=True):
        for event in track.notes:
            for note in event.notes:
                pitch = (
                    result.drum_idx_to_midi_drum[note.note]
                    if program == -1
                    else note.note
                )
                played[
                    (program, event.start_tick, event.end_tick, pitch, event.velocity)
                ] += 1
    return played


def assert_same_song(
    a: ConversionResult, b: ConversionResult, *, lyrics: bool = True
) -> None:
    """The same notes, programs, timing and velocities (and lyrics)."""
    assert sorted(b.track_idx_to_midi_instrument) == sorted(
        a.track_idx_to_midi_instrument
    )
    assert played_notes(b) == played_notes(a)
    if lyrics:
        assert (b.lyric_ticks, b.lyric_texts) == (a.lyric_ticks, a.lyric_texts)


def midi_bytes(tracks: list[list[Message | MetaMessage]]) -> bytes:
    mid = MidiFile(type=1, ticks_per_beat=480)  # 120 BPM: one song tick = 20 ticks
    for messages in tracks:
        mid.tracks.append(MidiTrack(messages))
    buf = io.BytesIO()
    mid.save(file=buf)
    return buf.getvalue()


def round_trip(midi: bytes) -> tuple[ConversionResult, bytes]:
    """Convert, recover from code_extra, and check converting again matches."""
    original = convert(midi)
    recovered = song_to_midi(original.to_typescript("code_extra"))
    assert_same_song(original, convert(recovered))
    return original, recovered


def track_ports(midi: bytes) -> list[list[int]]:
    """The midi_port values of each track that has channel messages."""
    return [
        [msg.port for msg in track if msg.type == "midi_port"]
        for track in MidiFile(file=io.BytesIO(midi)).tracks
        if any(not msg.is_meta for msg in track)
    ]


def programs_and_drums(midi: bytes) -> tuple[set[int], set[int]]:
    mid = MidiFile(file=io.BytesIO(midi))
    programs = set()
    drums = set()
    for track in mid.tracks:
        for msg in track:
            if msg.type == "program_change":
                programs.add(msg.program)
            elif msg.type == "note_on" and msg.channel == 9:
                drums.add(msg.note)
    return programs, drums


@pytest.mark.parametrize("format", ["code_extra", "hex", "code"])
def test_programs_and_drums_are_recovered(format: str) -> None:
    midi = song_to_midi(ORIGINAL.to_typescript(format))  # type: ignore[arg-type]
    assert programs_and_drums(midi) == ({0, 48}, {36, 38, 42})


@pytest.mark.parametrize("format", ["code_extra", "hex"])
def test_converting_again_gives_the_same_song(format: str) -> None:
    midi = song_to_midi(ORIGINAL.to_typescript(format))  # type: ignore[arg-type]
    assert_same_song(ORIGINAL, convert(midi), lyrics=format == "code_extra")


def test_lyrics_survive_code_extra_only() -> None:
    again = convert(song_to_midi(ORIGINAL.to_typescript("code_extra")))
    assert again.lyric_texts == ["Hel", "lo ", "café ", "world"]
    assert again.lyric_ticks == ORIGINAL.lyric_ticks
    assert convert(song_to_midi(ORIGINAL.to_typescript("hex"))).lyric_texts == []


def test_note_starts_are_on_the_song_tick_grid() -> None:
    mid = MidiFile(file=io.BytesIO(song_to_midi(ORIGINAL.to_typescript("hex"))))
    assert mid.ticks_per_beat == 480
    expected = sorted(
        e.start_tick * 20
        for track in ORIGINAL.song.tracks
        for e in track.notes
        for _ in e.notes
    )
    starts = []
    for track in mid.tracks:
        now = 0
        for msg in track:
            now += msg.time
            if msg.type == "note_on":
                starts.append(now)
    assert sorted(starts) == expected


def test_ambiguous_instrument_picks_the_lowest_program(
    caplog: pytest.LogCaptureFixture,
) -> None:
    mapping = load_instrument_params(default_instrument_params_text())
    # Program 3 now sounds exactly like program 48
    mapping.melodic_instruments[3] = deepcopy(mapping.melodic_instruments[48])
    with caplog.at_level(logging.INFO, logger="midi2mkcd"):
        midi = song_to_midi(ORIGINAL.to_typescript("hex"), mapping)
    assert programs_and_drums(midi)[0] == {0, 3}
    assert "matches programs [3, 48]; using 3" in caplog.text


def test_unmatched_instrument_becomes_program_0(
    caplog: pytest.LogCaptureFixture,
) -> None:
    mapping = load_instrument_params(default_instrument_params_text())
    del mapping.melodic_instruments[48]
    with caplog.at_level(logging.WARNING, logger="midi2mkcd"):
        midi = song_to_midi(ORIGINAL.to_typescript("hex"), mapping)
    assert programs_and_drums(midi)[0] == {0}
    assert "matches no params entry; using program 0" in caplog.text


def test_short_drum_map_falls_back_to_the_params(
    caplog: pytest.LogCaptureFixture,
) -> None:
    text = ORIGINAL.to_typescript("code_extra").replace(
        "midiDrumNoteMap: number[] = [36, 38, 42]", "midiDrumNoteMap: number[] = [36]"
    )
    with caplog.at_level(logging.INFO, logger="midi2mkcd"):
        midi = song_to_midi(text)
    assert programs_and_drums(midi)[1] == {36, 38, 42}
    assert "midiDrumNoteMap is too short" in caplog.text


def many_programs_midi(count: int, *, gap: int) -> bytes:
    """
    One 240-tick note for each of programs 0..count-1, each starting `gap` ticks
    after the last, on 15 channels per port.
    """
    channels = [c for c in range(16) if c != 9]
    tracks = []
    for program in range(count):
        port, i = divmod(program, len(channels))
        channel = channels[i]
        tracks.append(
            [
                MetaMessage("midi_port", port=port, time=0),
                Message("program_change", channel=channel, program=program),
                Message(
                    "note_on",
                    channel=channel,
                    note=60,
                    velocity=100,
                    time=program * gap,
                ),
                Message("note_off", channel=channel, note=60, time=240),
            ]
        )
    return midi_bytes(tracks)


def test_more_than_15_overlapping_programs_use_a_second_port() -> None:
    # Every note overlaps every other, so no channel can be shared
    original, midi = round_trip(many_programs_midi(17, gap=1))
    assert sorted(original.track_idx_to_midi_instrument) == list(range(17))
    ports = track_ports(midi)
    # Every track names its port: players such as SpessaSynth put a track without
    # one on the lowest named port, which made port 0 and 1 parts collide
    assert all(len(p) == 1 for p in ports)
    assert sorted(p[0] for p in ports) == [0] * 15 + [1] * 2


def test_programs_that_never_overlap_share_channels() -> None:
    # Each note starts after the last ends, so 17 programs fit on port 0
    _, midi = round_trip(many_programs_midi(17, gap=240))
    assert track_ports(midi) == [[]] * 15
    assert programs_and_drums(midi)[0] == set(range(17))


def note(channel: int, pitch: int, start: int, end: int) -> list[Message]:
    """A note on and off, with absolute ticks as `time` (fixed up by `deltas`)."""
    return [
        Message("note_on", channel=channel, note=pitch, velocity=90, time=start),
        Message("note_off", channel=channel, note=pitch, time=end),
    ]


def deltas(messages: list[Message]) -> list[Message]:
    """Turn absolute `time`s into delta times, note offs first at equal ticks."""
    ordered = sorted(messages, key=lambda m: (m.time, m.type == "note_on"))
    now = 0
    out = []
    for msg in ordered:
        out.append(msg.copy(time=msg.time - now))
        now = msg.time
    return out


def test_doubled_notes_are_kept() -> None:
    # The same notes on two channels with the same program play twice in Arcade
    tracks = []
    for channel in (0, 1):
        tracks.append(
            [
                Message("program_change", channel=channel, program=48),
                *deltas(note(channel, 60, 0, 240) + note(9, 38, 0, 40)),
            ]
        )
    original, _ = round_trip(midi_bytes(tracks))
    assert sum(played_notes(original).values()) == 4


def test_nested_same_pitch_notes_keep_their_lengths() -> None:
    # Short notes inside a long one of the same pitch: on one channel, the short
    # note's note off would end the long note
    melodic = note(0, 60, 0, 960) + note(1, 60, 200, 300) + note(1, 60, 400, 500)
    drums = note(9, 55, 0, 960) + note(10, 55, 200, 260)
    tracks = [
        [
            Message("program_change", channel=0, program=0),
            Message("program_change", channel=1, program=0),
            Message("control_change", channel=10, control=0, value=127),
            *deltas(melodic + drums),
        ]
    ]
    original, midi = round_trip(midi_bytes(tracks))
    assert sum(played_notes(original).values()) == 5
    # The second drum lane needs another port's drum channel
    assert sorted(p[0] for p in track_ports(midi)) == [0, 0, 0, 1]


def test_multi_song_text_is_rejected() -> None:
    text = sample_melodics(FIXTURE_MIDI, [0, 1])
    with pytest.raises(InvalidSongError, match="found 2"):
        song_to_midi(text)


def test_undecodable_song_is_rejected() -> None:
    # A song header claiming one track, with nothing after it
    with pytest.raises(InvalidSongError, match="Could not decode"):
        song_to_midi("hex`00780004180101`")


def test_velocity_section_missing_means_127() -> None:
    # Re-encode the fixture without velocities (all 128, which isn't written)
    song = deepcopy(ORIGINAL.song)
    for track in song.tracks:
        for event in track.notes:
            event.velocity = 128
    text = f"hex`{encode_song_to_hex(song)}`"
    mid = MidiFile(file=io.BytesIO(song_to_midi(text)))
    drum_velocities = {
        msg.velocity
        for track in mid.tracks
        for msg in track
        if msg.type == "note_on" and msg.channel == 9
    }
    assert drum_velocities == {127}
