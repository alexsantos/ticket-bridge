"""
test_status_mapper.py
----------------------
Pure unit tests (no database) for status vocabulary translation. Starting
point for the test suite - run with:

    pytest tests/test_status_mapper.py -v
"""
from app.services.status_mapper import external_to_internal, internal_to_external

SAMPLE_MAPPING = {
    "new": "Open",
    "in_progress": "Under Way",
    "resolved": "Resolved",
}


def test_internal_to_external_known():
    assert internal_to_external(SAMPLE_MAPPING, "new") == "Open"


def test_internal_to_external_unknown_returns_literal():
    assert internal_to_external(SAMPLE_MAPPING, "unmapped_status") == "unmapped_status"


def test_external_to_internal_known():
    assert external_to_internal(SAMPLE_MAPPING, "Under Way") == "in_progress"


def test_external_to_internal_unknown_returns_lowercase():
    assert external_to_internal(SAMPLE_MAPPING, "SomeStatus") == "somestatus"
