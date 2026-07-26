from app.adapters.outbound.utils.chat import (
    build_suggestions_instruction_block,
    filter_applied_suggestions,
    normalize_suggestion,
)


def test_normalize_suggestion_collapses_whitespace_and_case():
    assert normalize_suggestion("  Add   WAF  ") == "add waf"


def test_filter_applied_suggestions_removes_matches():
    suggestions = ["Add WAF", "Enable CloudWatch alarms", "Use private subnets"]
    applied = ["add waf", "Use Private Subnets"]
    assert filter_applied_suggestions(suggestions, applied) == [
        {"text": "Enable CloudWatch alarms", "category": "general"},
    ]


def test_filter_applied_suggestions_empty_inputs():
    assert filter_applied_suggestions(None, ["Add WAF"]) == []
    assert filter_applied_suggestions(["Add WAF"], None) == [
        {"text": "Add WAF", "category": "general"},
    ]


def test_filter_preserves_categories():
    suggestions = [
        {"text": "Add NAT Gateway", "category": "network"},
        {"text": "Split Orders capability", "category": "logical"},
    ]
    out = filter_applied_suggestions(suggestions, None)
    assert out == suggestions


def test_suggestions_instruction_requires_when_enabled():
    text = build_suggestions_instruction_block(True)
    assert "REQUIRED" in text
    assert "2–3" in text
    assert "category" in text.lower()
    assert "network" in text


def test_suggestions_instruction_empty_when_disabled():
    text = build_suggestions_instruction_block(False)
    assert "empty list" in text.lower()
