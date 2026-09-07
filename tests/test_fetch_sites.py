from app.services.fetch_sites import (
    MAX_JOBS_CAP,
    list_fetch_sites,
    resolve_site,
    resolve_window,
    window_cap,
)


def test_fetch_sites_catalog_has_core_ats():
    data = list_fetch_sites()
    ids = {s["id"] for s in data["sites"]}
    assert {"greenhouse", "ashby", "lever", "workable", "remoteok", "hn_whoishiring"} <= ids
    assert data["max_jobs"] == MAX_JOBS_CAP
    wins = {w["id"] for w in data["windows"]}
    assert wins == {"24h", "7d", "all"}


def test_resolve_window_and_site():
    assert resolve_window("24h")["days"] == 1
    assert resolve_window("7d")["days"] == 7
    assert resolve_window("all")["require_posted_at"] is False
    assert resolve_site("ashby")["kind"] == "ats"
    assert resolve_site("nope") is None


class TestWindowCaps:
    """How many jobs a window may return.

    Every window used to stop at 100. A 24h fetch that found 275 jobs posted
    that day threw 175 of them away after fetching, filtering and scoring
    them — the user had asked for today's jobs and got a truncated page.
    A narrow window is bounded by its own date filter, so it needs no second
    limit; the open window still does.
    """

    def test_a_day_is_not_truncated(self):
        assert window_cap("24h") >= 1000

    def test_a_week_allows_more_than_a_hundred(self):
        assert window_cap("7d") > 100

    def test_all_time_keeps_a_real_ceiling(self):
        """Without a window to bound it, this would try to insert everything."""
        assert window_cap("all") == 100

    def test_no_window_exceeds_the_absolute_ceiling(self):
        for window in ("24h", "7d", "all"):
            assert 1 <= window_cap(window) <= MAX_JOBS_CAP

    def test_an_unknown_window_falls_back_to_the_default(self):
        assert window_cap("nonsense") == window_cap("7d")

    def test_every_window_declares_its_cap(self):
        for row in list_fetch_sites()["windows"]:
            assert row.get("max_jobs"), row["id"]


class TestTheWindowSetsTheLimit:
    def test_a_window_raises_the_limit_by_itself(self):
        from app.services.discover import load_discovery_settings

        assert load_discovery_settings(window="24h")["insert_limit"] == window_cap("24h")
        assert load_discovery_settings(window="all")["insert_limit"] == window_cap("all")

    def test_an_explicit_request_can_still_ask_for_fewer(self):
        from app.services.discover import load_discovery_settings

        assert load_discovery_settings(window="24h", max_jobs=25)["insert_limit"] == 25

    def test_an_explicit_request_cannot_exceed_the_window(self):
        from app.services.discover import load_discovery_settings

        cfg = load_discovery_settings(window="all", max_jobs=99_999)
        assert cfg["insert_limit"] == window_cap("all")

    def test_a_day_window_still_requires_a_posting_date(self):
        """Undated jobs must not slip into "today"."""
        from app.services.discover import load_discovery_settings

        cfg = load_discovery_settings(window="24h")
        assert cfg["require_posted_at"] is True
        assert cfg["posted_within_days"] == 1

    def test_modes_without_a_window_are_unchanged(self):
        from app.services.discover import load_discovery_settings

        assert load_discovery_settings()["insert_limit"] <= 100
