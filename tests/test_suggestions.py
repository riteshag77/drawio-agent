from app.utils.chat import filter_applied_suggestions, normalize_suggestion


def test_normalize_suggestion_collapses_whitespace_and_case():
    assert normalize_suggestion("  Add   WAF  ") == "add waf"


def test_filter_applied_suggestions_removes_matches():
    suggestions = ["Add WAF", "Enable CloudWatch alarms", "Use private subnets"]
    applied = ["add waf", "Use Private Subnets"]
    assert filter_applied_suggestions(suggestions, applied) == ["Enable CloudWatch alarms"]


def test_filter_applied_suggestions_empty_inputs():
    assert filter_applied_suggestions(None, ["Add WAF"]) == []
    assert filter_applied_suggestions(["Add WAF"], None) == ["Add WAF"]
