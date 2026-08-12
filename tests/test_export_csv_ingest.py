from __future__ import annotations

from app.services.manual_ingest import parse_csv_jobs
from app.validators.remote_us import check_remote_us


SAMPLE = """id,title,organization,location,work_arrangement,remote_restricted_to,experience_level,employment_type,salary_min,salary_max,salary_currency,salary_unit,key_skills,source,date_posted,url,description,first_seen_run_id,last_seen_run_id,first_seen_at,last_seen_at
2288561303,Senior Data Engineer,Gifthealth Inc,Columbus, Ohio, United States,Remote Solely,Columbus, Ohio, United States,5-10,FULL_TIME,142000,177000,USD,YEAR,SQL; Python,paylocity,2026-07-31T17:06:20,https://recruiting.paylocity.com/Recruiting/Jobs/Details/4379947,Description Position Summary The Sr. Data Engineer is responsible for leading the design and architecture of Gifthealth remote data pipelines for patients in the United States. Expert SQL Python dbt.,24,24,2026-07-31T15:19:12.116Z,2026-07-31T15:19:12.116Z
2288702063,Principal Data Engineer,Beacon Biosignals,"Boston, Massachusetts, United States",Remote OK,United States,10+,FULL_TIME,190000,210000,USD,YEAR,Data Engineering,greenhouse,2026-07-31T13:36:41,https://job-boards.greenhouse.io/beaconbiosignals/jobs/4341499009,Beacon Biosignals is transforming precision medicine. This role is fully remote working anywhere in the US. 10+ years backend data engineering.,24,24,2026-07-31T15:19:12.116Z,2026-07-31T15:19:12.116Z
"""


def test_parse_export_csv_organization_and_description():
    jobs, meta = parse_csv_jobs(SAMPLE)
    assert meta["mapped_rows"] == 2
    assert jobs[0]["company"] == "Gifthealth Inc"
    assert jobs[0]["external_id"] == "2288561303"
    assert "Ohio" in (jobs[0]["location"] or "")
    assert jobs[0]["work_arrangement"] == "Remote Solely"
    assert "Sr. Data Engineer" in jobs[0]["description"]
    assert jobs[1]["company"] == "Beacon Biosignals"
    assert jobs[1]["work_arrangement"] == "Remote OK"


def test_remote_gate_uses_work_arrangement():
    r = check_remote_us(
        "Columbus, Ohio, United States",
        None,
        work_arrangement="Remote Solely",
        remote_restricted_to="Columbus, Ohio, United States",
    )
    assert r.ok

    r2 = check_remote_us(
        "Boston, MA",
        "Fully remote working anywhere in the US",
        work_arrangement="Remote OK",
        remote_restricted_to="United States",
    )
    assert r2.ok

    r3 = check_remote_us(
        "New York, NY",
        "Must be in office daily",
        work_arrangement="Onsite",
        remote_restricted_to=None,
    )
    assert not r3.ok
