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
