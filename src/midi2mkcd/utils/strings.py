import argparse


# Thanks Gemini
def parse_range(value: str) -> list[int]:
    """
    Parses a string of numbers, ranges, or a mix (e.g., '0-30' or '0,2,4,10-30')
    and returns a sorted list of unique integers.
    """
    result: set[int] = set()

    # Split by commas to handle individual numbers or sub-ranges
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue

        # Check if it's a range (e.g., 10-30)
        if "-" in part:
            try:
                start, end = map(int, part.split("-"))
                if start > end:
                    raise argparse.ArgumentTypeError(
                        f"Invalid range: {part} (start cannot be greater than end)"
                    )
                # +1 to make the end inclusive, which is standard for CLI ranges
                result.update(range(start, end + 1))
            except ValueError:
                raise argparse.ArgumentTypeError(
                    f"Invalid range format: '{part}'. Expected 'start-end'."
                )
        else:
            # It's a single number
            try:
                result.add(int(part))
            except ValueError:
                raise argparse.ArgumentTypeError(f"Invalid integer: '{part}'")

    return sorted(result)
