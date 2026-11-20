# Thanks Gemini
def parse_range(value: str) -> list[int]:
    """
    Parses a string of numbers, ranges, or a mix (e.g., '0-30' or '0,2,4,10-30')
    and returns a sorted list of unique integers.

    :raises ValueError: If a part isn't an integer or a valid 'start-end' range.
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
            except ValueError:
                raise ValueError(
                    f"Invalid range format: '{part}'. Expected 'start-end'."
                ) from None
            if start > end:
                raise ValueError(
                    f"Invalid range: {part} (start cannot be greater than end)"
                )
            # +1 to make the end inclusive, which is standard for CLI ranges
            result.update(range(start, end + 1))
        else:
            # It's a single number
            try:
                result.add(int(part))
            except ValueError:
                raise ValueError(f"Invalid integer: '{part}'") from None

    return sorted(result)


def format_ranges(numbers: list[int]) -> str:
    """
    The inverse of `parse_range`: format integers compactly, e.g. [3, 7, 8, 9] as
    "3, 7-9".

    :param numbers: The integers, in any order (duplicates are ignored).
    :return: Comma-separated numbers and inclusive ranges, in ascending order.
    """
    parts: list[str] = []
    ordered = sorted(set(numbers))
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1] == ordered[j] + 1:
            j += 1
        parts.append(str(ordered[i]) if i == j else f"{ordered[i]}-{ordered[j]}")
        i = j + 1
    return ", ".join(parts)


# Thanks Claude
def decode_lyric_text(msg: str) -> str:
    """
    MIDI encodes all text as latin-1, undo and try UTF-8

    :param msg: MIDI lyric text
    :return: Lyric text but in UTF-8
    """
    raw = msg.encode("latin-1")
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]  # strip BOM
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")  # fall back to what mido already assumed
