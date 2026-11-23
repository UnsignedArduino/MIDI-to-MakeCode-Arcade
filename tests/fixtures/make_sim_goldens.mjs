/**
 * Regenerates golden/sim_instructions.json: what the real MakeCode simulator
 * code (pxtsim/sound/song.ts and sequencer.ts) produces for every case in
 * sim_cases.json. tests/unit/test_sim_goldens.py checks the Python ports
 * (arcade/sim_song.py, arcade/sim_sequencer.py) against it byte for byte.
 *
 *     uv run python tests/fixtures/make_sim_cases.py      # only if the cases changed
 *     node tests/fixtures/make_sim_goldens.mjs <path to a microsoft/pxt clone>
 *
 * Needs Node >= 22.13 (for module.stripTypeScriptTypes) and git; no npm
 * packages. A blobless clone is enough:
 *
 *     git clone --filter=blob:none https://github.com/microsoft/pxt.git
 *
 * The files are read with `git show PXT_COMMIT:<path>`, so whatever the clone
 * has checked out doesn't matter. When re-porting from a newer pxt, update
 * PXT_COMMIT here and in the Python ports' header comments, then regenerate.
 *
 * How the TypeScript is loaded: Node's type stripping can't handle
 * `namespace` blocks, so the script removes the `namespace pxsim.music { ... }`
 * wrapper and the `export` keywords itself (failing if the source doesn't
 * look as expected), strips the types, and runs both files in one function
 * scope. pxsim.AudioContextManager.playInstructionsAsync is stubbed to record
 * each instruction buffer, and the real Sequencer is driven tick by tick with
 * a dummy metronome.
 */

import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import module from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const PXT_COMMIT = "19bd2103fc7fae218b75975891c921d2b5b1a419";
const SOURCES = ["pxtsim/sound/song.ts", "pxtsim/sound/sequencer.ts"];

const HERE = path.dirname(fileURLToPath(import.meta.url));
const CASES = path.join(HERE, "sim_cases.json");
const OUTPUT = path.join(HERE, "golden", "sim_instructions.json");

function fail(message) {
    console.error(`error: ${message}`);
    process.exit(1);
}

function gitShow(pxt, file) {
    try {
        return execFileSync("git", ["-C", pxt, "show", `${PXT_COMMIT}:${file}`], {
            encoding: "utf8",
            maxBuffer: 16 * 1024 * 1024,
        });
    } catch (e) {
        fail(`can't read ${file} at ${PXT_COMMIT} from ${pxt} (is it a pxt clone with that commit?)\n${e.message}`);
    }
}

/** Remove the `namespace pxsim.music { ... }` wrapper and `export` keywords. */
function unwrapNamespace(source, file) {
    const lines = source.replace(/\r\n/g, "\n").split("\n");
    const open = lines.flatMap((l, i) => (/^namespace pxsim\.music \{\s*$/.test(l) ? [i] : []));
    if (open.length !== 1) fail(`${file}: expected one "namespace pxsim.music {" line, found ${open.length}`);
    let close = lines.length - 1;
    while (close > open[0] && lines[close].trim() === "") close--;
    if (lines[close] !== "}") fail(`${file}: expected the namespace's closing "}" on the last line`);
    // Blank them out instead of deleting, so stack traces keep the line numbers
    lines[open[0]] = "";
    lines[close] = "";
    let exports = 0;
    const body = lines.map((l) =>
        l.replace(/^(    )export (?=(async function|function|class|const|let|type|interface) )/, (_, indent) => {
            exports++;
            return indent;
        }),
    );
    if (exports === 0) fail(`${file}: no exports found inside the namespace`);
    if (body.some((l) => /^\s*export /.test(l))) fail(`${file}: an export this script doesn't handle`);
    return body.join("\n");
}

function loadPxt(pxt) {
    const { emitWarning } = process;
    process.emitWarning = (warning, ...rest) => {
        if (String(warning).includes("stripTypeScriptTypes")) return;
        emitWarning.call(process, warning, ...rest);
    };
    const code = SOURCES.map((file) => module.stripTypeScriptTypes(unwrapNamespace(gitShow(pxt, file), file))).join("\n");
    process.emitWarning = emitWarning;

    let recorder = null;
    const pxsim = {
        music: {},
        AudioContextManager: {
            playInstructionsAsync(buffer) {
                if (!recorder) throw new Error("playInstructionsAsync called outside a song");
                recorder(buffer);
                return Promise.resolve();
            },
        },
    };
    const lib = new Function(
        "pxsim",
        `${code}\nreturn { renderInstrument, renderDrumInstrument, decodeSong, Sequencer, frequencies, tickToMs };`,
    )(pxsim);
    Object.assign(pxsim.music, lib);
    return { lib, setRecorder: (r) => (recorder = r) };
}

const toHex = (buffer) => Buffer.from(buffer).toString("hex");

async function main() {
    const pxt = process.argv[2];
    if (!pxt) fail("usage: node tests/fixtures/make_sim_goldens.mjs <path to a microsoft/pxt clone>");

    const { lib, setRecorder } = loadPxt(pxt);
    const cases = JSON.parse(readFileSync(CASES, "utf8"));

    // Failed async play calls (e.g. a missing drum) are silent in the
    // simulator; record them per song so the golden shows they happened.
    let rejections = [];
    process.on("unhandledRejection", (e) => rejections.push(String(e)));

    const notes = cases.notes.map((c) => [
        c.id,
        toHex(lib.renderInstrument(c.instrument, c.frequency ?? undefined, c.gate, c.volume)),
    ]);
    const drums = cases.drums.map((c) => [c.id, toHex(lib.renderDrumInstrument(c.drum, c.volume))]);

    const songs = [];
    for (const c of cases.songs) {
        const song = lib.decodeSong(Uint8Array.from(Buffer.from(c.hex, "hex")));
        const seq = new lib.Sequencer();
        seq.metronome = { start() {}, stop() {}, setInterval() {}, dispose() {} };
        seq.setVolume(c.volume);
        const played = [];
        setRecorder((buffer) => played.push([seq.currentTick(), toHex(buffer)]));
        rejections = [];
        seq.start(song, false);
        for (let guard = 0; seq.state() !== "stop"; guard++) {
            if (guard > 10_000_000) fail(`${c.id}: the sequencer never stopped`);
            seq.onTick();
        }
        setRecorder(null);
        await new Promise((resolve) => setTimeout(resolve, 0)); // let rejections arrive
        songs.push([c.id, { played, silent: rejections }]);
    }

    // One case per line, like sim_cases.json
    const section = (key, entries) =>
        ` ${JSON.stringify(key)}: {\n${entries.map(([id, v]) => `  ${JSON.stringify(id)}: ${JSON.stringify(v)}`).join(",\n")}\n }`;
    const text = [
        "{",
        ` "pxt_commit": ${JSON.stringify(PXT_COMMIT)},`,
        ` "frequencies": ${JSON.stringify(lib.frequencies)},`,
        [section("notes", notes), section("drums", drums), section("songs", songs)].join(",\n"),
        "}",
        "",
    ].join("\n");
    writeFileSync(OUTPUT, text);
    console.log(
        `Wrote ${path.relative(process.cwd(), OUTPUT)}: ${notes.length} notes, ${drums.length} drums, ${songs.length} songs`,
    );
}

await main();
