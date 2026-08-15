from apptrack.llm import _parse
from apptrack.models import Status


def test_parse_clean_json():
    v = _parse('{"is_job_related": true, "status": "REJECTED", "company": "Notion", "role": "SWE", "confidence": 0.95}')
    assert v.is_job_related and v.status == Status.REJECTED and v.company == "Notion"


def test_parse_json_with_prose_wrapper():
    v = _parse('Sure! Here is the JSON:\n{"is_job_related": true, "status": "APPLIED", "company": "Ramp", "role": "", "confidence": 0.8}\nDone.')
    assert v.status == Status.APPLIED and v.company == "Ramp"


def test_parse_not_job_related():
    v = _parse('{"is_job_related": false, "status": null, "company": "", "role": "", "confidence": 0.9}')
    assert not v.is_job_related


def test_parse_missing_company_needs_review():
    v = _parse('{"is_job_related": true, "status": "INTERVIEW", "company": "", "role": "", "confidence": 0.6}')
    assert v.status == Status.NEEDS_REVIEW


def test_parse_invalid_status_needs_review():
    v = _parse('{"is_job_related": true, "status": "MAYBE", "company": "Acme", "role": "", "confidence": 0.4}')
    assert v.status == Status.NEEDS_REVIEW


def test_parse_garbage_returns_none():
    assert _parse("I cannot classify this email.") is None
    assert _parse("") is None
    assert _parse("{broken json") is None


def test_verbalized_absence_normalized_to_empty():
    v = _parse('{"is_job_related": true, "status": "APPLIED", "company": "Flip", "role": "role title not specified", "confidence": 0.7}')
    assert v.role == ""
    assert v.company == "Flip"
    v2 = _parse('{"is_job_related": true, "status": "APPLIED", "company": "Unknown", "role": "N/A", "confidence": 0.7}')
    assert v2.company == "" and v2.role == ""
