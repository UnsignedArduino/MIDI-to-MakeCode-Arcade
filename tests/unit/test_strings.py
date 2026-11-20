import pytest

from midi2mkcd.utils.strings import format_ranges, parse_range


def test_parse_range() -> None:
    assert parse_range("0,2,4-6, 2") == [0, 2, 4, 5, 6]


@pytest.mark.parametrize("value", ["x", "5-2", "1-x", "1-2-3"])
def test_parse_range_raises_value_error(value: str) -> None:
    with pytest.raises(ValueError):
        parse_range(value)


@pytest.mark.parametrize(
    ("numbers", "expected"),
    [([], ""), ([5], "5"), ([3, 7, 8, 9], "3, 7-9"), ([9, 8, 8, 1, 2], "1-2, 8-9")],
)
def test_format_ranges(numbers: list[int], expected: str) -> None:
    assert format_ranges(numbers) == expected


def test_format_ranges_round_trips() -> None:
    numbers = [0, 1, 2, 5, 9, 10]
    assert parse_range(format_ranges(numbers).replace(" ", "")) == numbers
