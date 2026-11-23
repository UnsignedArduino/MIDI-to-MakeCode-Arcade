"""Shared helpers for the tests (a plain module, since pytest won't collect it)."""

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).parent.parent / "src" / "midi2mkcd"
FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_MIDI = FIXTURES / "fixture.mid"
GOLDEN = FIXTURES / "golden"
# Inputs and real-pxt outputs for the simulator ports (make_sim_cases.py,
# make_sim_goldens.mjs)
SIM_CASES = FIXTURES / "sim_cases.json"
SIM_GOLDEN = GOLDEN / "sim_instructions.json"
# The hand-tuned params the goldens were generated with (the bundled default)
INSTRUMENT_PARAMS = PACKAGE_ROOT / "resources" / "instrument_params.yaml"


# ---------------------------------------------------------------------------
# A minimal SF2 reader, written from the SoundFont 2.01 spec independently of
# midi2mkcd.soundfont, to check its output. Also reads SF3 (as FluidSynth and
# SpessaSynth do): shdr start/end of a compressed sample are byte offsets of an
# Ogg Vorbis stream in smpl, and its loop is relative to the sample's start.
# ---------------------------------------------------------------------------

SF3_COMPRESSED = 0x10


@dataclass
class Sf2Zone:
    # Generators in file order: (operator, raw 16-bit amount)
    generators: list[tuple[int, int]]
    # (source, destination, amount, amount source, transform)
    modulators: list[tuple[int, int, int, int, int]]

    def gen(self, operator: int) -> int | None:
        for op, amount in self.generators:
            if op == operator:
                return amount
        return None

    def signed(self, operator: int) -> int | None:
        value = self.gen(operator)
        return (
            None if value is None else struct.unpack("<h", struct.pack("<H", value))[0]
        )

    def range(self, operator: int) -> tuple[int, int] | None:
        value = self.gen(operator)
        return None if value is None else (value & 0xFF, value >> 8)


@dataclass
class Sf2Header:
    name: str
    start: int
    end: int
    loop_start: int
    loop_end: int
    rate: int
    original_pitch: int
    sample_type: int


@dataclass
class Sf2File:
    info: dict[str, bytes]
    smpl: bytes
    # (name, program, bank, zones)
    presets: list[tuple[str, int, int, list[Sf2Zone]]] = field(default_factory=list)
    # (name, zones)
    instruments: list[tuple[str, list[Sf2Zone]]] = field(default_factory=list)
    samples: list[Sf2Header] = field(default_factory=list)

    def points(self, header: Sf2Header) -> list[int]:
        """The sample's 16-bit points; an SF3 sample (compressed flag 0x10) is
        an Ogg Vorbis stream at byte offsets start-end, decoded with soundfile."""
        if header.sample_type & SF3_COMPRESSED:
            import io

            import soundfile

            data, _ = soundfile.read(
                io.BytesIO(self.smpl[header.start : header.end]), dtype="int16"
            )
            return [int(x) for x in data]
        return list(
            struct.unpack(
                f"<{header.end - header.start}h",
                self.smpl[2 * header.start : 2 * header.end],
            )
        )

    def loop(self, header: Sf2Header) -> tuple[int, int]:
        """The loop relative to the sample's start, in points: absolute in SF2,
        already relative in SF3."""
        if header.sample_type & SF3_COMPRESSED:
            return header.loop_start, header.loop_end
        return header.loop_start - header.start, header.loop_end - header.start


def _chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    out = []
    pos = 0
    while pos < len(data):
        tag = data[pos : pos + 4]
        (size,) = struct.unpack("<I", data[pos + 4 : pos + 8])
        out.append((tag, data[pos + 8 : pos + 8 + size]))
        pos += 8 + size + size % 2
    assert pos == len(data), "chunk sizes don't add up"
    return out


def _records(data: bytes, fmt: str) -> list[tuple[Any, ...]]:
    size = struct.calcsize(fmt)
    assert len(data) % size == 0, f"{len(data)} bytes isn't a whole number of {fmt}"
    return list(struct.iter_unpack(fmt, data))


def _str(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("ascii")


def _zones(
    bags: list[tuple[Any, ...]],
    mods: list[tuple[Any, ...]],
    gens: list[tuple[Any, ...]],
    first: int,
    last: int,
) -> list[Sf2Zone]:
    out = []
    for i in range(first, last):
        g0, m0 = bags[i]
        g1, m1 = bags[i + 1]
        out.append(
            Sf2Zone(
                [(op, amount) for op, amount in gens[g0:g1]],
                [(s, d, a, src, t) for s, d, a, src, t in mods[m0:m1]],
            )
        )
    return out


def read_sf2(data: bytes) -> Sf2File:
    """Parse an SF2 file, asserting the structure the spec requires."""
    ((riff, body),) = _chunks(data)
    assert riff == b"RIFF" and body[:4] == b"sfbk"
    lists = _chunks(body[4:])
    assert [(tag, sub[:4]) for tag, sub in lists] == [
        (b"LIST", b"INFO"),
        (b"LIST", b"sdta"),
        (b"LIST", b"pdta"),
    ]
    info = {tag.decode(): value for tag, value in _chunks(lists[0][1][4:])}
    ((smpl_tag, smpl),) = _chunks(lists[1][1][4:])
    assert smpl_tag == b"smpl"
    pdta = _chunks(lists[2][1][4:])
    assert [tag for tag, _ in pdta] == [
        b"phdr", b"pbag", b"pmod", b"pgen", b"inst", b"ibag", b"imod", b"igen",
        b"shdr",
    ]  # fmt: skip
    p = dict(pdta)

    phdr = _records(p[b"phdr"], "<20sHHHIII")
    pbag = _records(p[b"pbag"], "<HH")
    pmod = _records(p[b"pmod"], "<HHhHH")
    pgen = _records(p[b"pgen"], "<HH")
    inst = _records(p[b"inst"], "<20sH")
    ibag = _records(p[b"ibag"], "<HH")
    imod = _records(p[b"imod"], "<HHhHH")
    igen = _records(p[b"igen"], "<HH")
    shdr = _records(p[b"shdr"], "<20sIIIIIBbHH")
    assert _str(phdr[-1][0]) == "EOP"
    assert _str(inst[-1][0]) == "EOI"
    assert _str(shdr[-1][0]) == "EOS"
    assert pmod[-1] == (0, 0, 0, 0, 0) and imod[-1] == (0, 0, 0, 0, 0)
    assert pgen[-1] == (0, 0) and igen[-1] == (0, 0)

    sf = Sf2File(info=info, smpl=smpl)
    for i, (name, program, bank, bag, *_rest) in enumerate(phdr[:-1]):
        zones = _zones(pbag, pmod, pgen, bag, phdr[i + 1][3])
        sf.presets.append((_str(name), program, bank, zones))
    for i, (name, bag) in enumerate(inst[:-1]):
        sf.instruments.append(
            (_str(name), _zones(ibag, imod, igen, bag, inst[i + 1][1]))
        )
    for name, start, end, ls, le, rate, pitch, _corr, _link, kind in shdr[:-1]:
        sf.samples.append(Sf2Header(_str(name), start, end, ls, le, rate, pitch, kind))
    return sf
