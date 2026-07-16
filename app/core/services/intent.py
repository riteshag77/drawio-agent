"""Classify user prompts as cloud architecture vs simple (Mermaid) diagrams."""

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
)

_CLOUD_PLATFORMS = frozenset({"aws", "gcp", "azure", "multi"})


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


def classify_intent(prompt: str, *, diagram_mode: str = "auto", platform: str = "aws") -> str:
    """Return ``cloud`` or ``simple``.

    Explicit ``diagram_mode`` wins. Otherwise score cloud vs simple keywords;
    ambiguous prompts default to cloud (this product's primary path), especially
    when the UI platform picker is a cloud vendor.
    """
    mode = (diagram_mode or "auto").strip().lower()
    if mode in ("cloud", "simple"):
        return mode

    text = (prompt or "").lower()
    cloud_score = _score_terms(text, _CLOUD_TERMS)
    simple_score = _score_terms(text, _SIMPLE_TERMS)

    platform_key = (platform or "aws").strip().lower()
    if platform_key in _CLOUD_PLATFORMS and cloud_score == 0 and simple_score == 0:
        # Platform picker implies cloud architecture when the prompt is vague.
        return "cloud"

    if simple_score > cloud_score:
        return "simple"
    if cloud_score > simple_score:
        return "cloud"
    # Tie: prefer simple only when simple signals exist and no cloud signals.
    if simple_score > 0 and cloud_score == 0:
        return "simple"
    return "cloud"
