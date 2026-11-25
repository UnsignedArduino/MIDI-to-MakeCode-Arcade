from midi2mkcd.cli import main

# Guarded so worker processes (spawned on Windows/macOS) that re-import the main
# script, as when it's run by path (`python src/midi2mkcd/__main__.py`), don't
# run the CLI again
if __name__ == "__main__":
    raise SystemExit(main())
