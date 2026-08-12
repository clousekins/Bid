from __future__ import annotations

from app.services.manual_ingest import parse_csv_jobs, parse_json_jobs


def test_parse_json_aliases():
    raw = """
    [
      {
        "company_name": "Acme",
        "job_title": "Senior Software Engineer",
        "job_url": "https://boards.greenhouse.io/acme/jobs/1",
        "loc": "Remote, US",
        "jd": "Build systems"
      }
    ]
    """
    jobs = parse_json_jobs(raw)
    assert len(jobs) == 1
    assert jobs[0]["company"] == "Acme"
    assert jobs[0]["title"] == "Senior Software Engineer"
    assert jobs[0]["url"].startswith("https://")
    assert jobs[0]["description"] == "Build systems"


def test_parse_csv():
    raw = "company,title,url,location\nBeta,Backend Engineer,https://example.com/j/2,Remote US\n"
    jobs, meta = parse_csv_jobs(raw)
    assert len(jobs) == 1
    assert jobs[0]["company"] == "Beta"
    assert meta["mapped_rows"] == 1


def test_parse_csv_job_link_alias():
    raw = "Company Name,Job Title,Job Link,Location\nAltCo,ML Engineer,https://example.com/j/2,Remote US\n"
    jobs, meta = parse_csv_jobs(raw)
    assert len(jobs) == 1
    assert jobs[0]["url"] == "https://example.com/j/2"
    assert "job_link" in meta["headers"] or "job_link" in str(meta)
