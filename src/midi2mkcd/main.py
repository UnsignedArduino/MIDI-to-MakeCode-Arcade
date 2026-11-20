import logging
from pathlib import Path

from midi2mkcd import tuning
from midi2mkcd.api import OutputFormat, convert, load_instrument_params
from midi2mkcd.cli import configure_logging, generate_and_parse_args

logger = logging.getLogger(__name__)


# Old CLI, kept working on top of the new API until the CLI rewrite replaces it.
def main() -> None:
    args = generate_and_parse_args()
    configure_logging(args.debug)
    logger.debug(f"Received arguments: {args}")

    if args.test_sample_melodic_instruments and args.generate_extra_code:
        raise ValueError(
            "Melodic sample option and generating extra code are mutually exclusive!"
        )
    if args.test_generate_code and args.generate_extra_code:
        raise ValueError(
            "Generating sample code and generating extra code are mutually exclusive!"
        )

    replace_melodics: int | None = args.test_replace_all_melodics_with
    replace_drums: int | None = args.test_replace_all_drums_with
    if args.test_ask_to_replace_all_melodics_with:
        replace_melodics = int(input("Replace all melodics with MIDI instrument: "))
    if args.test_ask_to_replace_all_drums_with:
        replace_drums = int(input("Replace all drums with MIDI drum note: "))

    params_path = Path(args.input_instrument_params)
    logger.info(f"Reading instrument params from {params_path}")
    mapping = load_instrument_params(
        params_path.read_text(encoding="utf-8"),
        allow_incomplete=args.test_force_instrument_param_load,
    )

    input_path = Path(args.input)
    if args.test_sample_melodic_instruments is not None:
        final_output = tuning.sample_melodics(
            input_path, args.test_sample_melodic_instruments, mapping
        )
    else:
        fmt: OutputFormat = "hex"
        if args.test_generate_code:
            fmt = "play"
        elif args.generate_extra_code:
            fmt = "visualizer"
        if replace_melodics is not None or replace_drums is not None:
            result = tuning.convert_replaced(
                input_path, mapping, melodics=replace_melodics, drums=replace_drums
            )
        else:
            result = convert(input_path, mapping)
        final_output = result.to_typescript(fmt)

    output_path = Path(args.output) if args.output is not None else None
    if output_path is not None:
        logger.info(f"Writing result to {output_path}")
        output_path.write_text(final_output, encoding="utf-8")
    else:
        logger.info("Writing result to stdout")
        print(f"\n{final_output}\n")

    if args.test_copy_result_to_clipboard:
        try:
            import pyperclip
        except ImportError:
            raise RuntimeError(
                "pyperclip is not available (install the 'optional' extra), cannot "
                "automatically copy result to the clipboard!"
            ) from None
        pyperclip.copy(final_output)
        logger.info("Copied result to clipboard")


if __name__ == "__main__":
    main()
