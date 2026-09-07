"""The CSV a fetch leaves behind.

The table only shows jobs that cleared every gate. A run that read six
hundred listings and inserted forty has still read six hundred, so the export
is the whole catch — `in_app` says which rows reached the table and why the
rest did not.
"""
from __future__ import annotations

import csv

import pytest

from app.services import fetch_export
from app.services.fetch_export import FETCH_CSV_COLUMNS, write_fetch_csv


@pytest.fixture
def out_dir(tmp_path, monkeypatch):
    target = tmp_path / "data" / "fetches"
    monkeypatch.setattr(fetch_export, "fetch_csv_dir", lambda: (target.mkdir(parents=True, exist_ok=True), target)[1])
    return target


def _listing(**over):
    row = {
        "company": "Acme Analytics",
        "title": "Senior Data Engineer",
        "url": "https://jobright.ai/jobs/info/69d5669fe63cea7a8b660fe4",
        "location": "United States (Remote)",
        "posted_at": "2026-09-05 11:20:33",
        "source": "Jobright",
        "external_id": "69d5669fe63cea7a8b660fe4",
        "domains": "DE,AI",
        "staffing_firm": None,
        "employment_type": "Contract",
        "description": "Build and own batch pipelines.",
    }
    row.update(over)
    return row


def _read(path):
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


class TestWriting:
    def test_an_empty_run_writes_no_file(self, out_dir):
        assert write_fetch_csv(site="jobright", window="24h", listings=[]) is None
        assert not list(out_dir.glob("*.csv"))

    def test_the_file_is_named_for_the_site_and_window(self, out_dir):
        path = write_fetch_csv(site="jobright", window="24h", listings=[_listing()])
        assert path.name.startswith("jobright-24h-")
        assert path.suffix == ".csv"

    def test_every_column_is_written(self, out_dir):
        path = write_fetch_csv(site="jobright", window="7d", listings=[_listing()])
        rows = _read(path)
        assert list(rows[0]) == FETCH_CSV_COLUMNS
        assert rows[0]["company"] == "Acme Analytics"
        assert rows[0]["domains"] == "DE,AI"
        assert rows[0]["description"] == "Build and own batch pipelines."

    def test_excel_can_read_the_accents(self, out_dir):
        """A plain UTF-8 file opens in Excel as the local codepage."""
        path = write_fetch_csv(
            site="jobright", window="7d", listings=[_listing(company="Beyoncé Data")]
        )
        assert path.read_bytes().startswith(b"\xef\xbb\xbf")
        assert _read(path)[0]["company"] == "Beyoncé Data"

    def test_two_runs_in_the_same_second_keep_both_files(self, out_dir):
        first = write_fetch_csv(site="jobright", window="7d", listings=[_listing()])
        second = write_fetch_csv(
            site="jobright", window="7d", listings=[_listing(company="Other Co")]
        )
        assert first != second, "the second run must not take the first one's name"
        assert first.exists() and second.exists()
        assert _read(first)[0]["company"] == "Acme Analytics"
        assert _read(second)[0]["company"] == "Other Co"


class TestOutcomes:
    def test_an_inserted_row_carries_its_seq_no(self, out_dir):
        listing = _listing()
        path = write_fetch_csv(
            site="jobright",
            window="24h",
            listings=[listing],
            outcomes=[{"url": listing["url"], "ok": True, "seq_no": 1407, "status": "NEW"}],
        )
        row = _read(path)[0]
        assert row["seq_no"] == "1407"
        assert row["in_app"] == "NEW"

    def test_a_rejected_row_says_why(self, out_dir):
        listing = _listing()
        path = write_fetch_csv(
            site="jobright",
            window="24h",
            listings=[listing],
            outcomes=[{"url": listing["url"], "ok": False, "reason": "duplicate_url"}],
        )
        assert _read(path)[0]["in_app"] == "duplicate_url"

    def test_rows_the_quota_never_reached_are_not_called_skipped(self, out_dir):
        """The insert loop stops at the window's cap; the tail was never tried."""
        listing = _listing()
        path = write_fetch_csv(site="jobright", window="all", listings=[listing])
        assert _read(path)[0]["in_app"] == "not_attempted"

    def test_a_trailing_slash_still_matches_its_outcome(self, out_dir):
        listing = _listing()
        path = write_fetch_csv(
            site="jobright",
            window="24h",
            listings=[listing],
            outcomes=[{"url": listing["url"] + "/", "ok": True, "status": "JD_READY"}],
        )
        assert _read(path)[0]["in_app"] == "JD_READY"


class TestEmploymentTypeColumn:
    """The fetcher brings back contract and part-time now; the file must say so."""

    def test_the_type_is_written(self, out_dir):
        path = write_fetch_csv(site="jobright", window="7d", listings=[_listing()])
        assert _read(path)[0]["employment_type"] == "Contract"

    def test_a_listing_without_one_leaves_it_blank(self, out_dir):
        path = write_fetch_csv(site="jobright", window="7d",
                               listings=[_listing(employment_type=None)])
        assert _read(path)[0]["employment_type"] == ""

    def test_the_column_is_in_the_header(self, out_dir):
        path = write_fetch_csv(site="jobright", window="7d", listings=[_listing()])
        assert "employment_type" in _read(path)[0]
