"""Unit tests for job domain classification."""
from app.services.job_domains import (
    classify_domains,
    domains_storage,
    matches_allowed,
    normalize_domain_ids,
)


def test_classify_data_engineer():
    ids = classify_domains("Senior Data Engineer", "Spark, Airflow, Snowflake pipelines")
    assert "de" in ids


def test_classify_ai_ml_overlap():
    ids = classify_domains("ML Engineer", "LLM fine-tuning and RAG with Python")
    assert "ai" in ids


def test_classify_salesforce():
    ids = classify_domains("Senior Salesforce Developer", "Apex, LWC, Service Cloud")
    assert "sf" in ids


def test_classify_fullstack():
    ids = classify_domains("Full Stack Engineer", "React and Node APIs")
    assert "fs" in ids


def test_normalize_and_match():
    assert normalize_domain_ids(["DE", "ai", "nope"]) == ["de", "ai"]
    assert matches_allowed(["de", "ai"], ["ai"])
    assert not matches_allowed(["de"], ["sf"])
    assert matches_allowed(["de"], None)
    assert domains_storage(["de", "ai"]) == "DE,AI"
