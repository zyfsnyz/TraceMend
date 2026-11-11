from datetime import datetime, timedelta, timezone

import pytest

from app.serializers import iso


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (datetime(2026, 1, 2, 3, 4, 5), "2026-01-02T03:04:05Z"),
        (
            datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=8))),
            "2026-01-01T19:04:05Z",
        ),
        (None, None),
    ],
)
def test_iso_normalizes_datetimes_to_utc(value, expected):
    assert iso(value) == expected
