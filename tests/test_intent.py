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


def test_chart_and_diagram_terms_route_away_from_cloud():
    # None of these had a term at all, so they scored 0-0 and took the cloud
    # tie-break: asking for a bar graph returned an AWS architecture diagram.
    assert classify_intent("draw a bar graph of monthly sales") == "simple"
    assert classify_intent("pie chart of market share") == "simple"
    assert classify_intent("gantt chart for the project") == "simple"
    assert classify_intent("ER diagram for orders") == "simple"
    assert classify_intent("mindmap of product ideas") == "simple"


def test_chart_forms_without_a_renderer_still_route_to_the_chart_path():
    """The simple path serves these far better than the cloud one even without
    a dedicated renderer — the pack maps each to its nearest kind and says so.
    Untriaged they scored 0-0: "scatter plot of response time against request
    volume" came back as a "Performance Observability Dashboard" architecture."""
    assert classify_intent("scatter plot of latency vs load") == "simple"
    assert classify_intent("bubble chart of market segments") == "simple"
    assert classify_intent("area chart of storage growth") == "simple"


def test_supported_kinds_reached_by_every_common_phrasing():
    # Each of these has a real renderer; none had a term that reached it.
    assert classify_intent("stacked bar of costs by team") == "simple"
    assert classify_intent("timeline of company milestones") == "simple"
    assert classify_intent("user journey map for checkout") == "simple"
    assert classify_intent("funnel of signup conversion") == "simple"


def test_cloud_still_wins_over_a_bare_chart_mention():
    assert classify_intent("architecture chart for our AWS platform") == "cloud"
    assert classify_intent("plot our services on aws") == "cloud"
    assert classify_intent("timeline for our aws migration with vpc and rds") == "cloud"


def test_follow_up_stays_with_the_diagram_on_screen():
    """"change Q3 to 200" carries no vocabulary either way. Scored alone it took
    the cloud tie-break, which sent a chart edit down the cloud surgical-edit
    path — relabelling a bar to 200 while leaving it drawn at its old height."""
    assert classify_intent("change Q3 to 200", current_diagram_type="simple") == "simple"
    assert classify_intent("rename that box", current_diagram_type="simple") == "simple"
    assert classify_intent("change Q3 to 200", current_diagram_type="cloud") == "cloud"


def test_weak_cloud_mention_does_not_hijack_a_simple_canvas():
    # With a flowchart on screen these are requests *about the flowchart*.
    assert classify_intent("rename that box to S3 upload", current_diagram_type="simple") == "simple"
    assert classify_intent("add a lambda step", current_diagram_type="simple") == "simple"


def test_real_cloud_request_still_switches_away_from_a_simple_canvas():
    assert classify_intent(
        "now draw an aws vpc with alb and rds", current_diagram_type="simple",
    ) == "cloud"
    # And the mode picker remains the deterministic override in both directions.
    assert classify_intent(
        "change Q3 to 200", diagram_mode="cloud", current_diagram_type="simple",
    ) == "cloud"


def test_wants_three_d_detects_keyword_variants():
    assert wants_three_d("show me a 3D diagram of this VPC")
    assert wants_three_d("isometric AWS architecture")
    assert wants_three_d("three-dimensional view please")
    assert not wants_three_d("aws vpc with alb and rds")
    assert not wants_three_d("")


def test_wants_three_d_does_not_false_positive_on_substrings():
    # "3d" as a whole token only — must not match inside e.g. "l33d" or "aw3d".
    assert not wants_three_d("service named aw3dprint")
