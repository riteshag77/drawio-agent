"""Intent classifier unit tests."""

from app.core.services.intent import classify_intent, wants_three_d


def test_explicit_mode_wins():
    assert classify_intent("flowchart please", diagram_mode="cloud") == "cloud"
    assert classify_intent("aws vpc", diagram_mode="simple") == "simple"


def test_cloud_keywords():
    assert classify_intent("Design an AWS three-tier VPC with ALB and RDS") == "cloud"
    assert classify_intent("GCP GKE microservices behind Cloud SQL") == "cloud"


def test_simple_keywords():
    assert classify_intent("Draw a flowchart for user onboarding") == "simple"
    assert classify_intent("sequence diagram of login", platform="aws") == "simple"


def test_platform_defaults_to_cloud_when_vague():
    assert classify_intent("show me a high level overview", platform="aws") == "cloud"


def test_mixed_prefers_stronger_signal():
    # Cloud terms outweigh a single mermaid mention.
    assert classify_intent("aws vpc with alb rds and lambda — also show mermaid legend") == "cloud"
    # Strong simple signal without cloud services.
    assert classify_intent("mermaid flowchart for ticket triage") == "simple"


def test_wants_three_d_detects_keyword_variants():
    assert wants_three_d("show me a 3D diagram of this VPC")
    assert wants_three_d("isometric AWS architecture")
    assert wants_three_d("three-dimensional view please")
    assert not wants_three_d("aws vpc with alb and rds")
    assert not wants_three_d("")


def test_wants_three_d_does_not_false_positive_on_substrings():
    # "3d" as a whole token only — must not match inside e.g. "l33d" or "aw3d".
    assert not wants_three_d("service named aw3dprint")
