"""Classify user prompts as cloud architecture vs simple (non-cloud) diagrams.

``"simple"`` covers both families the non-cloud path can draw: node-and-arrow
diagrams (flowchart, org chart, state machine, ER, mindmap) and charts (bar,
pie, gantt). Both compile to draw.io XML via ``SimpleDiagramIR``.
"""

from __future__ import annotations

import re

# Weighted keyword signals. Higher weight = stronger push toward that intent.
_CLOUD_TERMS: tuple[tuple[str, int], ...] = (
    ("cloud architecture", 4),
    ("well-architected", 3),
    ("google cloud", 3),
    ("microsoft azure", 3),
    ("availability zone", 3),
    ("api gateway", 3),
    ("load balancer", 2),
    ("cloudfront", 2),
    ("route 53", 2),
    ("route53", 2),
    ("cloud run", 2),
    ("cloud sql", 2),
    ("cloud watch", 2),
    ("cloudwatch", 2),
    ("dynamodb", 2),
    ("kubernetes", 2),
    ("serverless", 2),
    ("microservices", 2),
    ("three tier", 2),
    ("3-tier", 2),
    ("3 tier", 2),
    ("multi-cloud", 2),
    ("multicloud", 2),
    ("amazon", 2),
    ("subnet", 2),
    ("lambda", 2),
    ("aurora", 1),
    ("cloud", 1),
    ("azure", 2),
    ("aws", 2),
    ("gcp", 2),
    ("vpc", 2),
    ("vnet", 2),
    ("ecs", 1),
    ("eks", 1),
    ("aks", 1),
    ("gke", 1),
    ("rds", 1),
    ("s3", 1),
    ("alb", 1),
    ("nlb", 1),
    ("k8s", 2),
    ("iam", 1),
    ("sqs", 1),
    ("sns", 1),
)

_SIMPLE_TERMS: tuple[tuple[str, int], ...] = (
    ("sequence diagram", 4),
    ("organization chart", 4),
    ("organizational chart", 4),
    ("state machine", 3),
    ("state diagram", 3),
    ("flow chart", 3),
    ("flowchart", 3),
    ("org chart", 3),
    ("process flow", 3),
    ("workflow diagram", 3),
    ("simple diagram", 3),
    ("basic flow", 2),
    ("no cloud", 4),
    ("mermaid", 3),
    ("uml", 2),
    ("swimlane", 2),
    ("erd", 2),
    ("entity relationship", 3),
    # Charts. Missing entirely until the simple path could draw them, which
    # meant a chart request scored 0-0 and fell through to the cloud default
    # below: "draw a bar graph of monthly sales" returned an AWS architecture
    # diagram. Weighted 4 because these phrases are unambiguous — nobody says
    # "pie chart" about a cloud architecture.
    ("bar chart", 4),
    ("bar graph", 4),
    ("column chart", 4),
    ("pie chart", 4),
    ("donut chart", 4),
    ("gantt chart", 4),
    ("gantt", 3),
    ("line chart", 4),
    ("line graph", 4),
    ("histogram", 3),
    # Forms the chart compiler has no renderer for, but which the simple path
    # still handles far better than the cloud one: the prompt pack maps each to
    # its nearest supported kind and says so in the reply. Untriaged, they
    # scored 0-0 and took the cloud tie-break — "scatter plot of response time
    # against request volume" came back as a "Performance Observability
    # Dashboard" architecture diagram.
    ("scatter plot", 4),
    ("scatter", 3),
    ("bubble chart", 4),
    ("area chart", 4),
    ("plot", 2),
    # Phrasings for kinds that *are* supported but that no term reached:
    # "stacked bar" never matched "bar chart"/"bar graph", and a timeline is a
    # gantt. A user journey/funnel is an ordered set of stages — a flow.
    ("stacked bar", 4),
    ("timeline", 3),
    ("user journey", 4),
    ("journey map", 4),
    ("funnel", 3),
    ("project plan", 3),
    ("roadmap", 2),
    # Bare "chart" is a whole-token match, so it does not fire on "flowchart"
    # (which scores on its own entry anyway). Low weight: it is the one term
    # here that a cloud prompt could plausibly contain.
    ("chart", 2),
    # Node-and-arrow diagrams the simple path has always been able to draw, but
    # which no term routed to it. "erd" above is a whole-token match, so it
    # never fired on the far more common spelling "ER diagram".
    ("er diagram", 4),
    ("class diagram", 3),
    ("mind map", 4),
    ("mindmap", 4),
    ("use case diagram", 3),
    ("decision tree", 3),
)

_CLOUD_PLATFORMS = frozenset({"aws", "gcp", "azure", "multi"})

#: How much cloud vocabulary a follow-up needs before it may switch a non-cloud
#: diagram to the cloud pipeline. Set above the weight of any *single* weak term
#: (``s3``, ``rds``, ``ecs`` = 1; ``aws``, ``lambda`` = 2) on purpose: with a
#: flowchart on screen, "rename that box to S3 upload" or "add a Lambda step"
#: is a request about the flowchart, not a request to redraw it as an
#: architecture. Phrases that genuinely mean "start over as cloud" clear it
#: comfortably ("cloud architecture" = 4, "aws vpc" = 4, "api gateway" = 3).
#: A user who really does want the switch on weaker wording still has the
#: deterministic escape hatch: the diagram-mode picker wins over all of this.
_CLOUD_SWITCH_THRESHOLD = 3

_THREE_D_PATTERN = re.compile(r"(?<![a-z0-9])(3d|three-dimensional|isometric)(?![a-z0-9])", re.IGNORECASE)


def _score_terms(text: str, terms: tuple[tuple[str, int], ...]) -> int:
    score = 0
    for term, weight in terms:
        if " " in term or "-" in term:
            if term in text:
                score += weight
            continue
        # Whole-token match for short codes (aws, s3, vpc) to avoid false hits.
        if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text):
            score += weight
    return score


def classify_intent(
    prompt: str,
    *,
    diagram_mode: str = "auto",
    platform: str = "aws",
    current_diagram_type: str = "",
) -> str:
    """Return ``cloud`` or ``simple``.

    Explicit ``diagram_mode`` wins. Otherwise score cloud vs simple keywords;
    ambiguous prompts default to cloud (this product's primary path), especially
    when the UI platform picker is a cloud vendor.

    ``current_diagram_type`` is what is already on the canvas ("simple" |
    "cloud" | ""), and it exists because scoring the *follow-up text alone* gets
    continuations badly wrong. "change Q3 to 200" carries no vocabulary either
    way, so it scored 0-0 and took the tie-break to cloud — which sent an edit
    of a bar chart down the cloud surgical-edit path. That path relabelled the
    bar's text to 200 and left the bar itself drawn at its old height of 90,
    then reported success: a chart that states one number and draws another.
    Once a non-cloud diagram exists, a follow-up stays with it unless the user
    actually asks for cloud architecture.
    """
    mode = (diagram_mode or "auto").strip().lower()
    if mode in ("cloud", "simple"):
        return mode

    text = (prompt or "").lower()
    cloud_score = _score_terms(text, _CLOUD_TERMS)
    simple_score = _score_terms(text, _SIMPLE_TERMS)

    if (current_diagram_type or "").strip().lower() == "simple":
        return "cloud" if cloud_score >= _CLOUD_SWITCH_THRESHOLD else "simple"

    platform_key = (platform or "aws").strip().lower()
    if platform_key in _CLOUD_PLATFORMS and cloud_score == 0 and simple_score == 0:
        # Platform picker implies cloud architecture when the prompt is vague.
        return "cloud"

    if simple_score > cloud_score:
        return "simple"
    if cloud_score > simple_score:
        return "cloud"
    # Tie (including 0-0): default to cloud, this product's primary path.
    return "cloud"


def wants_three_d(prompt: str) -> bool:
    """Whether the prompt text itself asks for an isometric/3D diagram — lets
    "draw this in 3D" work without the explicit three_d request flag, the
    same way cloud vocabulary alone routes a prompt to the cloud pipeline."""
    return bool(_THREE_D_PATTERN.search(prompt or ""))
