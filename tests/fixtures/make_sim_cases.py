"""
Regenerates sim_cases.json, the inputs for the simulator-port goldens.

    uv run python tests/fixtures/make_sim_cases.py

Then regenerate the expected outputs from the real pxt source (see
make_sim_goldens.mjs):

    node tests/fixtures/make_sim_goldens.mjs <path to a microsoft/pxt clone>

tests/unit/test_sim_goldens.py runs the same cases through the Python ports
(arcade/sim_song.py, arcade/sim_sequencer.py) and compares byte for byte.

The cases use pxt's own JSON shapes (camelCase instruments and drums, songs as
encoded hex), so the Node script can pass them straight to song.ts. There are
three kinds:

- notes: renderInstrument(instrument, frequency, gate, volume) for every
  bundled melodic instrument plus hand-made envelope/LFO edge cases. A null
  frequency stands for `frequencies[note]` past the table (undefined in JS).
- drums: renderDrumInstrument(drum, volume) for every bundled drum plus edge
  cases.
- songs: everything Sequencer.onTick plays for an encoded song, for the
  converted fixture and hand-made edge cases.
"""

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from midi2mkcd import convert
from midi2mkcd.arcade.music import encode_song
from midi2mkcd.arcade.music_types import (
    LFO,
    DrumInstrument,
    DrumSoundStep,
    EnharmonicSpelling,
    Envelope,
    Instrument,
    Note,
    NoteEvent,
    Song,
    Track,
)
from midi2mkcd.arcade.sim_sequencer import FREQUENCIES, tick_to_ms
from midi2mkcd.midi_to_song.instruments import load_instrument_params
from midi2mkcd.utils.resources import default_instrument_params_text

HERE = Path(__file__).parent
OUTPUT = HERE / "sim_cases.json"
FIXTURE_MIDI = HERE / "fixture.mid"

TICK = tick_to_ms(120, 24, 1)  # 20.833... ms, the converter's song tick
WAVEFORMS = (1, 2, 3, 4, 5, 11, 12, 13, 14, 15, 16, 17, 18)


def envelope_json(e: Envelope) -> dict[str, int]:
    return asdict(e)


def instrument_json(i: Instrument) -> dict[str, Any]:
    res: dict[str, Any] = {
        "waveform": i.waveform,
        "octave": i.octave,
        "ampEnvelope": envelope_json(i.amp_envelope),
    }
    if i.pitch_envelope is not None:
        res["pitchEnvelope"] = envelope_json(i.pitch_envelope)
    if i.amp_lfo is not None:
        res["ampLFO"] = asdict(i.amp_lfo)
    if i.pitch_lfo is not None:
        res["pitchLFO"] = asdict(i.pitch_lfo)
    return res


def drum_json(d: DrumInstrument) -> dict[str, Any]:
    return {
        "startFrequency": d.start_frequency,
        "startVolume": d.start_volume,
        "steps": [asdict(s) for s in d.steps],
    }


def env(a: int, d: int, s: int, r: int, amp: int = 1024) -> Envelope:
    return Envelope(attack=a, decay=d, sustain=s, release=r, amplitude=amp)


def note_cases() -> list[dict[str, Any]]:
    params = load_instrument_params(default_instrument_params_text())
    cases: list[dict[str, Any]] = []

    def add(
        case_id: str,
        instrument: Instrument,
        frequency: float | None,
        gate: float,
        volume: float,
    ) -> None:
        cases.append(
            {
                "id": case_id,
                "instrument": instrument_json(instrument),
                "frequency": frequency,
                "gate": gate,
                "volume": volume,
            }
        )

    # Every bundled instrument: a quarter note at velocity 127 and the default
    # music.volume() (volume 127), a 1-tick note, a long quiet low note, and a
    # high note at full volume.
    for program, instrument in sorted(params.melodic_instruments.items()):
        add(f"program{program}_c4_quarter", instrument, 262, 24 * TICK, 127)
        add(f"program{program}_c4_tick", instrument, 262, TICK, 127)
        add(f"program{program}_low_long", instrument, FREQUENCIES[0], 96 * TICK, 64)
        add(f"program{program}_high_full", instrument, FREQUENCIES[96], 24 * TICK, 1024)

    flat = env(0, 0, 1024, 0)
    for w in WAVEFORMS:
        add(f"flat_wave{w}", Instrument(w, 4, flat), 262, 500, 1024)

    sine = 3
    envelopes = {
        "a0_d0_r0": env(0, 0, 1024, 0),
        "a10_d100_s512_r100": env(10, 100, 512, 100),
        "a200_d0_r200": env(200, 0, 1024, 200),
        "a0_d300_s0_r0": env(0, 300, 0, 0),
        "a3_d3_s700_r3": env(3, 3, 700, 3),  # stage points under 5 ms apart
        "sustain_over_amp": env(20, 50, 2048, 50, 512),
        "amp0": env(10, 10, 1024, 10, 0),
        "max": env(65535, 65535, 65535, 65535, 65535),
    }
    gates = {"gate0": 0.0, "gate1tick": TICK, "gate21": 21.0, "gate500": 500.0}
    for env_name, e in envelopes.items():
        if env_name == "max":
            add("env_max_gate500", Instrument(sine, 4, e), 262, 500, 1024)
            continue
        for gate_name, gate in gates.items():
            add(f"env_{env_name}_{gate_name}", Instrument(sine, 4, e), 262, gate, 1024)
    add("negative_gate", Instrument(sine, 4, env(10, 10, 512, 50)), 262, -TICK, 1024)

    pitch_envs = {
        "up200": env(200, 300, 0, 0, 200),
        "sustained": env(50, 50, 512, 400, 100),
        "release_past_amp": env(0, 0, 1024, 1000, 300),
        "wrap": env(10, 0, 1024, 10, 65535),
    }
    for name, pe in pitch_envs.items():
        inst = Instrument(sine, 4, env(10, 100, 700, 200), pitch_envelope=pe)
        add(f"pitchenv_{name}", inst, 262, 500, 1024)
        add(f"pitchenv_{name}_short", inst, 262, TICK, 1024)

    lfo_freqs = (0, 1, 4, 6, 10, 20, 255)
    for f in lfo_freqs:
        amp_lfo = Instrument(sine, 4, env(10, 0, 1024, 100), amp_lfo=LFO(f, 300))
        add(f"amplfo_f{f}", amp_lfo, 262, 1000, 1024)
        pitch_lfo = Instrument(sine, 4, env(10, 0, 1024, 100), pitch_lfo=LFO(f, 10))
        add(f"pitchlfo_f{f}", pitch_lfo, 55, 1000, 1024)
    add(
        "amplfo_over_env",
        Instrument(sine, 4, env(0, 0, 512, 0), amp_lfo=LFO(4, 2000)),
        262,
        1000,
        1024,
    )
    add(
        "amplfo_env_off",
        Instrument(sine, 4, env(0, 0, 1024, 0, 0), amp_lfo=LFO(4, 300)),
        262,
        1000,
        1024,
    )
    add(
        "lfo_amp0",
        Instrument(sine, 4, flat, amp_lfo=LFO(4, 0), pitch_lfo=LFO(6, 0)),
        262,
        500,
        1024,
    )
    add(
        "everything",
        Instrument(
            2,
            4,
            env(30, 200, 600, 300),
            pitch_envelope=env(20, 80, 300, 500, 50),
            amp_lfo=LFO(5, 150),
            pitch_lfo=LFO(7, 8),
        ),
        440,
        24 * TICK,
        100,
    )

    note_in_sine = Instrument(sine, 4, env(10, 100, 512, 100))
    add("frequency_undefined", note_in_sine, None, 500, 1024)
    add("frequency_undefined_square", Instrument(15, 4, flat), None, 500, 1024)
    add(
        "frequency_undefined_pitchenv",
        Instrument(sine, 4, flat, pitch_envelope=env(10, 10, 1024, 10, 100)),
        None,
        500,
        1024,
    )
    for vol in (0, 1, 4.5, 127, 156.25, 1024, 2000):
        add(f"volume_{vol}", note_in_sine, 262, 500, vol)
    return cases


def drum_cases() -> list[dict[str, Any]]:
    params = load_instrument_params(default_instrument_params_text())
    cases: list[dict[str, Any]] = []

    def add(case_id: str, drum: DrumInstrument, volume: float) -> None:
        cases.append({"id": case_id, "drum": drum_json(drum), "volume": volume})

    for note, drum in sorted(params.drum_instruments.items()):
        for vol in (127, 1024, 64.5):
            add(f"drum{note}_v{vol}", drum, vol)

    def step(w: int, f: int, v: int, d: int) -> DrumSoundStep:
        return DrumSoundStep(waveform=w, frequency=f, volume=v, duration=d)

    add("one_step", DrumInstrument(100, 1024, [step(5, 200, 512, 30)]), 1024)
    add(
        "zero_duration_step",
        DrumInstrument(100, 1024, [step(1, 150, 1024, 0), step(5, 200, 0, 20)]),
        1024,
    )
    add(
        "waveform_changes",
        DrumInstrument(
            400,
            0,
            [step(3, 300, 1024, 10), step(5, 1, 800, 40), step(3, 80, 0, 100)],
        ),
        200,
    )
    add(
        "max_values", DrumInstrument(65535, 65535, [step(1, 65535, 65535, 65535)]), 1024
    )
    add("volume_0", DrumInstrument(100, 1024, [step(5, 200, 512, 30)]), 0)
    return cases


def song_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []

    def add(case_id: str, song: Song, volume: float) -> None:
        cases.append(
            {"id": case_id, "hex": bytes(encode_song(song)).hex(), "volume": volume}
        )

    fixture = convert(FIXTURE_MIDI.read_bytes()).song
    add("fixture_volume128", fixture, 128)
    add("fixture_volume255", fixture, 255)
    add("fixture_volume2000", fixture, 2000)  # setVolume clamps to 1024

    def notes(*indices: int) -> list[Note]:
        return [Note(i, EnharmonicSpelling.NORMAL) for i in indices]

    def song(tracks: list[Track], measures: int = 1) -> Song:
        return Song(
            measures=measures,
            beats_per_measure=4,
            beats_per_minute=120,
            ticks_per_beat=24,
            tracks=tracks,
        )

    inst = Instrument(3, 4, env(10, 100, 512, 100))
    drum = DrumInstrument(100, 1024, [DrumSoundStep(5, 200, 0, 30)])
    no_steps = DrumInstrument(100, 1024, [])
    edge = song(
        [
            Track(
                id=0,
                instrument=inst,
                notes=[
                    NoteEvent(notes(24, 28, 31), 0, 24, velocity=100),
                    NoteEvent(notes(24), 10, 11),  # plays: 10 >= 0
                    NoteEvent(notes(40), 30, 40, velocity=0),
                    NoteEvent(notes(41), 20, 30),  # never plays: after tick 30
                    NoteEvent(notes(63), 50, 60, velocity=127),
                    NoteEvent(notes(36), 95, 100),  # last tick of the song
                    NoteEvent(notes(36), 96, 100),  # at maxTick: never plays
                ],
            ),
            Track(
                id=1,
                instrument=Instrument(
                    15, 9, env(0, 0, 1024, 50)
                ),  # octave 9: past the table
                notes=[NoteEvent(notes(0, 20, 40), 0, 12, velocity=90)],
            ),
            Track(
                id=2,
                instrument=Instrument(1, 4, env(0, 0, 1024, 0)),
                notes=[
                    NoteEvent(notes(0, 1, 2, 0), 0, 1, velocity=64),
                    NoteEvent(notes(0), 48, 49),
                ],
                drums=[drum, no_steps],  # index 1 has no steps, 2 is past the list
            ),
        ]
    )
    add("edge_cases", edge, 128)
    add(
        "zero_measures",
        song(
            [Track(0, inst, [NoteEvent(notes(24), 0, 24), NoteEvent(notes(24), 1, 2)])],
            measures=0,
        ),
        128,
    )
    return cases


def main() -> None:
    data = {"notes": note_cases(), "drums": drum_cases(), "songs": song_cases()}
    # One compact case per line, so diffs show which cases changed
    sections = []
    for key, cases in data.items():
        lines = ",\n".join(f"  {json.dumps(c, separators=(',', ':'))}" for c in cases)
        sections.append(f' "{key}": [\n{lines}\n ]')
    with OUTPUT.open("w", encoding="utf-8", newline="\n") as f:
        f.write("{\n" + ",\n".join(sections) + "\n}\n")
    print(
        f"Wrote {OUTPUT.name}: {len(data['notes'])} notes, "
        f"{len(data['drums'])} drums, {len(data['songs'])} songs"
    )


if __name__ == "__main__":
    main()
