"""One-off: tailor Brian Gou resume for LLM Data Engineer (healthcare AWS) and save PDF locally."""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.folder_writer import ensure_job_folder
from app.services.pdf_export import export_pdf, markdown_to_html
from app.services.persona import load_persona
from app.services.tailor import _inject_css, _load_persona_css, generate_html_bundle

JD = """
Role: LLM Data Engineer
Location: Remote
Duration: 12-18+ months

Must have:
- AWS, Data Sets, data sources, data services within AWS
- Strong AI & LLM
- Recent healthcare industry exp (HIPAA, HL7, etc)
- LinkedIn Page
- Strong communication

Context: Generalist Data Engineer for a client building a healthcare-focused AI benchmark and evaluation suite. Initial target is clinical prediction tasks including sepsis onset, days-to-death, and lab value trend forecasting, evaluated across multiple frontier and vertical-specific models.

Role Summary: Responsible for everything that happens to data before a model sees it and after a model responds. Build the ingestion, normalization, and packaging layer that turns raw healthcare data into standardized benchmark inputs, and the storage and query layer that makes evaluation results analyzable.

What You will Own:
- Build ingestion and normalization pipelines for three modality families: DICOM radiology studies, whole-slide pathology images, and tabular EHR extracts (labs, vitals, encounters, medication administration records).
- Design the canonical benchmark record format (intermediate representation every task configuration and model adapter reads from).
- Solve modality packaging: gigapixel pathology slides and multiseries radiology studies reduced to payloads fitting third-party API limits (48 images, 20 MB) with tiling, region selection, downsampling, compression, and provenance metadata.
- Construct cohort and label pipelines: time-windowed feature assembly for sepsis onset, survival horizon for days-to-death, longitudinal series for lab value trends.
- Build results store and analysis layer for per-run, per-model, per-task scored outputs.
- Enforce PHI handling end to end: encryption at rest/in transit, least-privilege access, audit logging, deidentification, VPC boundaries for third-party API payloads.

Required Skills:
- Data engineering: production-grade pipeline code, deterministic/idempotent/re-runnable jobs, strong testing.
- AWS data stack: S3 (layout, lifecycle, storage classes at image scale), AWS Glue and/or Spark on EMR, Athena, Step Functions, Lambda, AWS Batch.
- SQL and data modeling: complex temporal joins, point-in-time correctness, avoid label leakage in time-series clinical data.
- Data quality and lineage: validation frameworks, schema enforcement, versioned datasets.
- AWS security and governance: IAM, KMS, VPC endpoints / PrivateLink, HIPAA-eligible architecture with BAA.

Desirable:
- Healthcare formats: DICOM, FHIR, HL7v2, OMOP CDM; ICD, LOINC, RxNorm, SNOMED.
- Medical imaging: pydicom, OpenSlide, WSI pyramid/tiling.
- Terraform, Docker, ECR, CI/CD.
- LLM APIs and multimodal payload construction.

Nice to Have:
- Clinical prediction models / healthcare ML datasets (MIMIC, eICU).
- AWS HealthImaging or HealthLake.
- De-identification tooling and re-identification risk assessment.
"""


async def main() -> int:
    persona = load_persona("brian_gou")
    job = SimpleNamespace(
        title="LLM Data Engineer",
        company="Healthcare AI Benchmark Client",
        location="Remote",
        url="",
        description=JD.strip(),
        seq_no=9001,
        id=0,
    )

    print("Generating Brian Gou HTML resume bundle via OpenAI…")
    resume_html, cover = await generate_html_bundle(persona, job)
    css = _load_persona_css(persona)
    if css:
        resume_html = _inject_css(resume_html, css)

    folder = ensure_job_folder("brian_gou", 9001, "Healthcare-AI-Benchmark")
    display = persona.display_name
    html_path = folder / f"{display}.html"
    pdf_path = folder / f"{display}.pdf"
    md_path = folder / f"{display}.md"
    cover_txt = folder / f"{display} - Cover Letter.txt"
    cover_html = folder / f"{display} - Cover Letter.html"
    jd_path = folder / "JD.txt"
    meta_path = folder / "meta.json"

    html_path.write_text(resume_html, encoding="utf-8")
    md_path.write_text(f"(HTML resume — see {display}.html)\n", encoding="utf-8")
    jd_path.write_text(JD.strip(), encoding="utf-8")
    cover_txt.write_text(cover.strip() + "\n", encoding="utf-8")
    cover_html.write_text(
        markdown_to_html(cover, title=f"{display} — Cover Letter"),
        encoding="utf-8",
    )
    meta_path.write_text(
        json.dumps(
            {
                "persona_id": "brian_gou",
                "title": job.title,
                "company": job.company,
                "location": job.location,
                "seq_no": 9001,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"Exporting PDF -> {pdf_path}")
    await export_pdf(resume_html, pdf_path)
    await export_pdf(
        markdown_to_html(cover, title=f"{display} — Cover Letter"),
        folder / f"{display} - Cover Letter.pdf",
    )

    downloads = Path.home() / "Downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    out_name = "Brian_Gou_LLM_Data_Engineer_Healthcare_AWS.pdf"
    dest = downloads / out_name
    shutil.copy2(pdf_path, dest)

    # Also copy HTML for easy preview
    shutil.copy2(html_path, downloads / out_name.replace(".pdf", ".html"))

    print("DONE")
    print(f"Package: {folder}")
    print(f"Downloads PDF: {dest}")
    print(f"Downloads HTML: {downloads / out_name.replace('.pdf', '.html')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
