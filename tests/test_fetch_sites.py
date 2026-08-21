from app.services.fetch_sites import list_fetch_sites, resolve_site, resolve_window


def test_fetch_sites_catalog_has_core_ats():
    data = list_fetch_sites()
    ids = {s["id"] for s in data["sites"]}
    assert {"greenhouse", "ashby", "lever", "workable", "remoteok", "hn_whoishiring"} <= ids
    assert data["max_jobs"] == 100
    wins = {w["id"] for w in data["windows"]}
    assert wins == {"24h", "7d", "all"}


def test_resolve_window_and_site():
    assert resolve_window("24h")["days"] == 1
    assert resolve_window("7d")["days"] == 7
    assert resolve_window("all")["require_posted_at"] is False
    assert resolve_site("ashby")["kind"] == "ats"
    assert resolve_site("nope") is None
