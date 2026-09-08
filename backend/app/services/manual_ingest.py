from __future__ import annotations

import csv
import io
import json
import re
from typing import Any


FIELD_ALIASES = {
    "company": [
        "company",
        "company_name",
        "employer",
        "org",
        "organization",
        "organization_name",
        "firm",
    ],
    "title": [
        "title",
        "job_title",
        "role",
        "position",
        "job",
        "job_name",
        "position_title",
        "role_title",
    ],
    "url": [
        "url",
        "link",
        "job_url",
        "job_link",
        "apply_url",
        "apply_link",
        "href",
        "job_page",
        "posting_url",
        "posting_link",
        "greenhouse_url",
        "ats_url",
        "application_url",
    ],
    "location": [
        "location",
        "loc",
        "locations",
        "job_location",
        "city",
        "workplace",
        "work_location",
    ],
    "external_id": [
        "id",
        "external_id",
        "job_id",
        "gh_id",
        "posting_id",
        "requisition_id",
        "req_id",
    ],
    "source": ["source", "board", "ats", "site"],
    "description": [
        "description",
        "jd",
        "job_description",
        "desc",
        "content",
        "job_desc",
        "full_description",
        "details",
    ],
    "staffing_firm": ["staffing_firm", "agency", "recruiter_firm", "staffing"],
    "work_arrangement": [
        "work_arrangement",
        "workplace_type",
        "remote_type",
        "arrangement",
    ],
    "remote_restricted_to": [
        "remote_restricted_to",
        "remote_location",
        "remote_restriction",
        "allowed_location",
    ],
    "experience_level": ["experience_level", "seniority", "years_experience", "level"],
}


def _norm_key(k: str) -> str:
    return (
        (k or "")
        .strip()
        .lower()
        .replace(" ", "_")
        .replace("-", "_")
        .replace("/", "_")
        .replace(".", "_")
    )


def _map_row(row: dict[str, Any]) -> dict[str, Any] | None:
    lowered = {_norm_key(str(k)): v for k, v in row.items() if k is not None}
    lowered.pop("", None)
    out: dict[str, Any] = {}
    for canonical, aliases in FIELD_ALIASES.items():
        for a in aliases:
            if a in lowered and lowered[a] not in (None, ""):
                out[canonical] = lowered[a]
                break
    company = str(out.get("company") or "").strip()
    title = str(out.get("title") or "").strip()
    url = str(out.get("url") or "").strip()
    if not company or not title or not url:
        return None
    for key in (
        "description",
        "external_id",
        "location",
        "work_arrangement",
        "remote_restricted_to",
        "experience_level",
        "staffing_firm",
    ):
        if key in out and out[key] is not None:
            out[key] = str(out[key]).strip()
    out["source"] = str(out.get("source") or "manual")
    return out


def parse_json_jobs(raw: str | bytes) -> list[dict[str, Any]]:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8-sig")
    data = json.loads(raw)
    if isinstance(data, dict):
        if "jobs" in data and isinstance(data["jobs"], list):
            data = data["jobs"]
        else:
            data = [data]
    if not isinstance(data, list):
        raise ValueError("JSON must be a list of jobs or {jobs: [...]}")
    results = []
    for item in data:
        if not isinstance(item, dict):
            continue
        mapped = _map_row(item)
        if mapped:
            results.append(mapped)
    return results


_WORK_ARR = re.compile(r"^(remote|hybrid|onsite|on-site|office|in.?office)", re.I)
_URL = re.compile(r"^https?://", re.I)


def _repair_wide_row(headers: list[str], values: list[str]) -> dict[str, str]:
    """Repair rows where unquoted commas split location/description."""
    if len(values) == len(headers):
        return dict(zip(headers, values))
    if len(values) < len(headers):
        # pad
        values = values + [""] * (len(headers) - len(values))
        return dict(zip(headers, values))

    hnorm = [_norm_key(h) for h in headers]
    # Prefer schema used by the job-export CSV
    try:
        loc_i = hnorm.index("location")
        wa_i = hnorm.index("work_arrangement") if "work_arrangement" in hnorm else -1
        url_i = hnorm.index("url")
        desc_i = hnorm.index("description")
    except ValueError:
        # Fallback: dump extras into last text-ish column
        extras = len(values) - len(headers)
        merged = values[: len(headers) - 1]
        # put overflow into the last field before trailing metadata if possible
        overflow = values[len(headers) - 1 :]
        merged.append(",".join(overflow))
        return dict(zip(headers, merged[: len(headers)]))

    # Rebuild from left: take fixed columns before location
    out_vals: list[str] = []
    i = 0
    for hi, h in enumerate(hnorm):
        if hi < loc_i:
            out_vals.append(values[i] if i < len(values) else "")
            i += 1
            continue
        if hi == loc_i:
            # Consume until work_arrangement-looking token or until we can align URL
            loc_parts = [values[i]] if i < len(values) else [""]
            i += 1
            while i < len(values):
                tok = values[i]
                if wa_i >= 0 and _WORK_ARR.search(tok or ""):
                    break
                # Stop if remaining length can still fill required trailing columns
                remaining_headers = len(headers) - (loc_i + 1)
                remaining_values = len(values) - i
                if remaining_values == remaining_headers:
                    break
                loc_parts.append(tok)
                i += 1
            out_vals.append(", ".join(p.strip() for p in loc_parts if p is not None))
            continue
        if hi == desc_i:
            # Everything from here until we have exactly (headers after desc) left
            after = len(headers) - desc_i - 1
            end = len(values) - after
            desc = ",".join(values[i:end])
            out_vals.append(desc)
            i = end
            continue
        out_vals.append(values[i] if i < len(values) else "")
        i += 1

    # Ensure length
    while len(out_vals) < len(headers):
        out_vals.append("")
    return dict(zip(headers, out_vals[: len(headers)]))


def parse_csv_jobs(raw: str | bytes) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8-sig")
    sample = raw[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        # The sniffer reports doublequote=False unless a "" pair happens to fall
        # inside the 8KB it looks at, and a description long enough to push the
        # first quoted quote past that mark is ordinary here. Reading a file
        # written with doubled quotes as though it had none does not fail — it
        # ends the field early and splits one job across several rows, which
        # arrives as extra listings rather than as an error. Our own exports
        # read 4061 rows instead of 2221 that way.
        #
        # Every writer that reaches this — Python's csv, Excel, the extension —
        # doubles quotes, because RFC 4180 has no other escape. So this is not a
        # guess worth taking from a sample.
        dialect.doublequote = True
    except csv.Error:
        dialect = csv.excel

    # Use reader (not DictReader) so we can repair wide rows
    reader = csv.reader(io.StringIO(raw), dialect=dialect)
    try:
        headers = next(reader)
    except StopIteration:
        return [], {"headers": [], "raw_rows": 0, "mapped_rows": 0}

    headers = [h.strip() for h in headers]
    results = []
    raw_rows = 0
    repaired = 0
    for values in reader:
        if not values or all(not (v or "").strip() for v in values):
            continue
        raw_rows += 1
        if len(values) != len(headers):
            repaired += 1
            row = _repair_wide_row(headers, values)
        else:
            row = dict(zip(headers, values))
        mapped = _map_row(row)
        if mapped:
            results.append(mapped)

    meta = {
        "headers": [_norm_key(h) for h in headers],
        "raw_rows": raw_rows,
        "mapped_rows": len(results),
        "repaired_rows": repaired,
        "delimiter": getattr(dialect, "delimiter", ","),
    }
    return results, meta


def parse_upload(filename: str, content: bytes) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    name = (filename or "").lower()
    if name.endswith(".csv") or (not name.endswith(".json") and b"," in content[:200]):
        return parse_csv_jobs(content)
    jobs = parse_json_jobs(content)
    return jobs, {"headers": [], "raw_rows": len(jobs), "mapped_rows": len(jobs)}
