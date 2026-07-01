from __future__ import annotations

from pathlib import Path

from ytdl.db import connect, migrate
from ytdl.library import (
    extract_video_id_from_filename,
    extract_video_id_from_url,
    lookup_by_video_id,
    record_downloaded,
    scan_directories,
)

# --- URL parsing ---


def test_extract_video_id_from_canonical_watch_url() -> None:
    assert (
        extract_video_id_from_url("https://www.youtube.com/watch?v=abc12345678")
        == "abc12345678"
    )


def test_extract_video_id_from_youtu_be_short_link() -> None:
    assert (
        extract_video_id_from_url("https://youtu.be/abc12345678") == "abc12345678"
    )


def test_extract_video_id_from_watch_with_extra_params() -> None:
    # `list=` and other tracking params must not break the ``?v=`` extraction —
    # this is the shape the "next-in-playlist" link produces.
    assert (
        extract_video_id_from_url(
            "https://www.youtube.com/watch?v=abc12345678&list=PLxxxx&t=42s"
        )
        == "abc12345678"
    )


def test_extract_video_id_from_shorts_url() -> None:
    assert (
        extract_video_id_from_url("https://www.youtube.com/shorts/dEf-45ghi_j")
        == "dEf-45ghi_j"
    )


def test_extract_video_id_from_embed_url() -> None:
    assert (
        extract_video_id_from_url("https://www.youtube.com/embed/abc12345678")
        == "abc12345678"
    )


def test_extract_video_id_handles_mobile_prefix() -> None:
    # m.youtube.com is a common paste from a phone.
    assert (
        extract_video_id_from_url("https://m.youtube.com/watch?v=abc12345678")
        == "abc12345678"
    )


def test_extract_video_id_returns_none_for_unrecognized_shape() -> None:
    assert extract_video_id_from_url("https://example.com/foo") is None


def test_extract_video_id_returns_none_for_short_id() -> None:
    # 10 chars is not a valid YouTube ID; better to punt to the probe
    # path than emit something that never matches the library index.
    assert extract_video_id_from_url("https://youtu.be/short") is None


def test_extract_video_id_returns_none_for_empty_or_bad() -> None:
    assert extract_video_id_from_url("") is None
    assert extract_video_id_from_url("not a url") is None


# --- filename parsing ---


def test_extract_video_id_from_filename_default_template() -> None:
    # yt-dlp's default output template: "{title} [{id}].{ext}".
    assert (
        extract_video_id_from_filename("Foo Bar Baz [abc12345678].mp4")
        == "abc12345678"
    )


def test_extract_video_id_from_filename_full_path() -> None:
    assert (
        extract_video_id_from_filename(
            "/home/user/Videos/Foo Bar [dEf-45ghi_j].mkv"
        )
        == "dEf-45ghi_j"
    )


def test_extract_video_id_from_filename_ignores_middle_brackets() -> None:
    # A bracketed tag mid-title must NOT be interpreted as an id — the
    # regex anchors the closing bracket to just before the extension.
    assert (
        extract_video_id_from_filename("Foo [tag] bar [abc12345678].mp4")
        == "abc12345678"
    )
    # And a bracketed tag with wrong-length content anywhere doesn't match.
    assert extract_video_id_from_filename("Foo [wrong-len-id].mp4") is None


def test_extract_video_id_from_filename_returns_none_when_absent() -> None:
    assert extract_video_id_from_filename("no id here.mp4") is None
    assert extract_video_id_from_filename("") is None


# --- scanning ---


def _make_conn(tmp_path: Path):
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    return conn


def test_scan_directories_indexes_matching_files(tmp_path: Path) -> None:
    lib = tmp_path / "library"
    lib.mkdir()
    (lib / "Alpha [aaa11111111].mp4").write_bytes(b"x" * 10)
    (lib / "Beta [bbb22222222].mkv").write_bytes(b"y" * 20)
    sub = lib / "Playlist"
    sub.mkdir()
    (sub / "01 - Song [ccc33333333].mp3").write_bytes(b"z" * 30)
    # Non-matching noise: no id in the filename.
    (lib / "just_a_random.txt").write_text("nope")

    conn = _make_conn(tmp_path)
    count, scanned, elapsed = scan_directories(conn, [str(lib)])
    assert count == 3
    assert str(lib.resolve()) in scanned
    assert elapsed >= 0.0

    # Each id has a row with the correct path + parsed title.
    row = conn.execute(
        "SELECT path, title, filesize_bytes FROM library_files WHERE video_id=?",
        ("aaa11111111",),
    ).fetchone()
    assert row["path"] == str(lib / "Alpha [aaa11111111].mp4")
    assert row["title"] == "Alpha"
    assert row["filesize_bytes"] == 10


def test_scan_directories_skips_missing_dirs(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    count, scanned, _ = scan_directories(
        conn, [str(tmp_path / "does_not_exist")]
    )
    assert count == 0
    assert scanned == []


def test_scan_directories_is_idempotent_on_rescan(tmp_path: Path) -> None:
    lib = tmp_path / "library"
    lib.mkdir()
    original = lib / "Foo [abc12345678].mp4"
    original.write_bytes(b"x" * 100)

    conn = _make_conn(tmp_path)
    count1, _, _ = scan_directories(conn, [str(lib)])
    count2, _, _ = scan_directories(conn, [str(lib)])
    assert count1 == 1
    assert count2 == 1  # no duplication


def test_scan_directories_updates_row_when_file_moves(tmp_path: Path) -> None:
    lib_a = tmp_path / "A"
    lib_b = tmp_path / "B"
    lib_a.mkdir()
    lib_b.mkdir()
    (lib_a / "Foo [abc12345678].mp4").write_bytes(b"x" * 10)

    conn = _make_conn(tmp_path)
    scan_directories(conn, [str(lib_a)])
    hit = lookup_by_video_id(conn, "abc12345678")
    assert hit is not None
    assert str(lib_a) in hit["path"]

    # Move the file to a different scan dir; the row's path must update
    # instead of a second row being inserted.
    (lib_a / "Foo [abc12345678].mp4").rename(lib_b / "Foo [abc12345678].mp4")
    count, _, _ = scan_directories(conn, [str(lib_b)])
    assert count == 1
    hit2 = lookup_by_video_id(conn, "abc12345678")
    assert hit2 is not None
    assert str(lib_b) in hit2["path"]


def test_scan_removes_row_when_file_deleted_from_scanned_dir(tmp_path: Path) -> None:
    """The 409 hint says 'delete the existing file' — rescan must
    honor that by removing rows whose files no longer exist under the
    scanned roots."""
    lib = tmp_path / "lib"
    lib.mkdir()
    keep = lib / "Keep [aaa11111111].mp4"
    goner = lib / "Goner [bbb22222222].mp4"
    keep.write_bytes(b"x")
    goner.write_bytes(b"x")

    conn = _make_conn(tmp_path)
    count1, _, _ = scan_directories(conn, [str(lib)])
    assert count1 == 2
    assert lookup_by_video_id(conn, "bbb22222222") is not None

    # Simulate the user deleting the duplicate file the 409 hint
    # told them to delete.
    goner.unlink()

    count2, _, _ = scan_directories(conn, [str(lib)])
    assert count2 == 1
    # Deleted file's row is GONE — /jobs won't false-positive on it now.
    assert lookup_by_video_id(conn, "bbb22222222") is None
    # The other row stays.
    assert lookup_by_video_id(conn, "aaa11111111") is not None


def test_scan_root_with_underscore_does_not_delete_unrelated_rows(tmp_path: Path) -> None:
    """SQLite LIKE treats `_` and `%` as wildcards. Without escaping,
    a scan root like `/media/lib_1` would match `/media/libX1` and
    delete rows there when it shouldn't. Locks down the ESCAPE fix."""
    root_underscore = tmp_path / "lib_1"
    root_similar = tmp_path / "libX1"
    root_underscore.mkdir()
    root_similar.mkdir()
    (root_underscore / "InUnderscore [aaa11111111].mp4").write_bytes(b"x")
    (root_similar / "InSimilar [bbb22222222].mp4").write_bytes(b"x")

    conn = _make_conn(tmp_path)
    # Index both dirs first.
    scan_directories(conn, [str(root_underscore), str(root_similar)])
    assert lookup_by_video_id(conn, "aaa11111111") is not None
    assert lookup_by_video_id(conn, "bbb22222222") is not None

    # Rescan ONLY `lib_1`. The row under `libX1` must not be deleted
    # even though `_` would wildcard-match without ESCAPE.
    scan_directories(conn, [str(root_underscore)])
    assert lookup_by_video_id(conn, "aaa11111111") is not None
    assert lookup_by_video_id(conn, "bbb22222222") is not None, (
        "row under libX1 was clobbered by unescaped LIKE on lib_1"
    )


def test_record_downloaded_stores_canonical_path(tmp_path: Path) -> None:
    """When record_downloaded receives a symlink-prefixed path, it must
    resolve to the canonical form so rescan cleanup sees a comparable
    prefix. Otherwise the row under the symlink prefix survives a
    rescan and keeps producing false-duplicate 409s."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "linked"
    try:
        link.symlink_to(real)
    except OSError:
        import pytest
        pytest.skip("filesystem doesn't support symlinks")

    conn = _make_conn(tmp_path)
    # Simulate yt-dlp writing via the symlink path.
    (real / "Foo [ccc33333333].mp4").write_bytes(b"x")
    record_downloaded(
        conn,
        video_id="ccc33333333",
        path=str(link / "Foo [ccc33333333].mp4"),
        title="Foo",
        filesize_bytes=1,
    )
    hit = lookup_by_video_id(conn, "ccc33333333")
    assert hit is not None
    # Stored path is the canonical (resolved) form, not the symlink form.
    assert str(real) in hit["path"]
    assert str(link) not in hit["path"]


def test_scan_preserves_rows_under_unscanned_roots(tmp_path: Path) -> None:
    """A rescan that only covers a subset of the config's scan_dirs must
    NOT clobber rows indexed from other roots. Otherwise a partial
    /library/rescan (e.g. an operator rescanning one specific mount)
    would wipe rows from mounts they DIDN'T rescan."""
    root_a = tmp_path / "A"
    root_b = tmp_path / "B"
    root_a.mkdir()
    root_b.mkdir()
    (root_a / "InA [aaa11111111].mp4").write_bytes(b"x")
    (root_b / "InB [bbb22222222].mp4").write_bytes(b"x")

    conn = _make_conn(tmp_path)
    scan_directories(conn, [str(root_a), str(root_b)])
    assert lookup_by_video_id(conn, "aaa11111111") is not None
    assert lookup_by_video_id(conn, "bbb22222222") is not None

    # Rescan ONLY A. B's row must stay put.
    scan_directories(conn, [str(root_a)])
    assert lookup_by_video_id(conn, "aaa11111111") is not None
    assert lookup_by_video_id(conn, "bbb22222222") is not None


def test_lookup_by_video_id_returns_none_when_absent(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    assert lookup_by_video_id(conn, "abc12345678") is None


def test_record_downloaded_roundtrips(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    record_downloaded(
        conn,
        video_id="abc12345678",
        path="/data/out/Foo [abc12345678].mp4",
        title="Foo",
        filesize_bytes=12345,
    )
    hit = lookup_by_video_id(conn, "abc12345678")
    assert hit is not None
    assert hit["path"] == "/data/out/Foo [abc12345678].mp4"
    assert hit["title"] == "Foo"
    assert hit["filesize_bytes"] == 12345


def test_record_downloaded_updates_existing_row(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    record_downloaded(conn, "abc12345678", "/old/path.mp4", "Old", 1)
    record_downloaded(conn, "abc12345678", "/new/path.mp4", "New", 2)
    hit = lookup_by_video_id(conn, "abc12345678")
    assert hit is not None
    assert hit["path"] == "/new/path.mp4"
    assert hit["title"] == "New"
    assert hit["filesize_bytes"] == 2


def test_record_downloaded_ignores_empty_video_id(tmp_path: Path) -> None:
    conn = _make_conn(tmp_path)
    record_downloaded(conn, "", "/data/x.mp4", None, None)
    row = conn.execute("SELECT COUNT(*) AS n FROM library_files").fetchone()
    assert row["n"] == 0
