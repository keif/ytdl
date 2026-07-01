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
