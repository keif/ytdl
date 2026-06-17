from __future__ import annotations

from ytdl.ulid import new_ulid


def test_ulid_length_and_charset() -> None:
    ulid = new_ulid()
    assert len(ulid) == 26
    assert set(ulid) <= set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")


def test_ulids_are_sortable_in_creation_order() -> None:
    ulids = [new_ulid() for _ in range(1000)]
    assert ulids == sorted(ulids), "ULIDs should sort in creation order"


def test_ulids_are_unique() -> None:
    ulids = {new_ulid() for _ in range(10_000)}
    assert len(ulids) == 10_000


def test_clock_going_backwards_does_not_break_monotonicity(monkeypatch) -> None:
    """If wall-clock time moves backwards (e.g., NTP slew), new_ulid must still
    produce ULIDs that sort after prior ones.
    """
    import time as time_module

    import ytdl.ulid as ulid_mod

    fake_now = [2_000.0]

    def fake_time() -> float:
        return fake_now[0]

    monkeypatch.setattr(time_module, "time", fake_time)
    # Reset module state so the test starts from a known baseline.
    monkeypatch.setattr(ulid_mod, "_last_ms", 0)
    monkeypatch.setattr(ulid_mod, "_last_rand", 0)

    first = ulid_mod.new_ulid()
    fake_now[0] = 1_000.0  # clock goes backwards by 1 second
    second = ulid_mod.new_ulid()
    assert second > first, "ULID after clock-rollback must still sort later"
