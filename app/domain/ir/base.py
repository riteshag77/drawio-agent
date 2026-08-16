"""Shared IR primitives used by every view's IR."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator

ViewKind = Literal["network", "logical", "security", "data_flow", "archimate"]

# Cell ids end up as draw.io `id=` attributes and as edge source/target refs.
# Constraining them here means the compiler never has to escape or sanitize.
_ID_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")

# draw.io reserves 0 (model root) and 1 (default layer).
RESERVED_IDS = frozenset({"0", "1"})


class IRModel(BaseModel):
    """Base for every IR node.

    ``extra="forbid"`` is deliberate: when the LLM invents a field (a style
    string, a coordinate, a stencil name) we want a loud validation error naming
    the field, not a silently dropped value that changes the diagram's meaning.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    #: True when ``flows`` was filled in by ``flow_spine``, not by the model.
    #: A *private* attribute on purpose: every public field here is part of the
    #: JSON schema the LLM is shown, and a field named "were these flows real?"
    #: is an invitation for it to answer. Private attributes are outside both
    #: the schema and ``extra="forbid"``.
    _flows_synthesized: bool = PrivateAttr(default=False)

    def mark_flows_synthesized(self) -> None:
        self._flows_synthesized = True

    def flows_are_synthesized(self) -> bool:
        """Whether ``flows`` is this app's invention rather than the model's.

        Layout heuristics that read the flow graph — leaf ranking, barycenter
        ordering, cross-corridor row alignment — must not run when this is
        true. They exist to make a *described* topology readable; run against
        edges we invented, they rearrange the diagram to satisfy a call graph
        nobody claimed, and throw away the one ordering signal a model that
        returned no flows did give: the order it listed things in.
        """
        return self._flows_synthesized


class Identified(IRModel):
    id: str = Field(description="Unique, stable identifier. Letters/digits/underscore, must start with a letter.")

    @field_validator("id")
    @classmethod
    def _valid_id(cls, value: str) -> str:
        if value in RESERVED_IDS:
            raise ValueError(f"id {value!r} is reserved by draw.io (0 and 1 are the model root and default layer)")
        if not _ID_RE.match(value):
            raise ValueError(
                f"id {value!r} must start with a letter and contain only letters, digits or underscores (max 64 chars)"
            )
        return value


class Leaf(Identified):
    """A single service icon — the atom of every view.

    ``shape_type`` is a *semantic* service key (``aws_rds``, ``azure_functions``).
    It is never a draw.io stencil string: resolving it to an official icon is the
    MCP layer's job at render time, exactly as in the existing pipeline. Keeping
    the model on semantic keys is what preserves that ownership split.
    """

    shape_type: str = Field(
        description=(
            "Semantic cloud service key, e.g. 'aws_rds', 'gcp_cloud_run', 'azure_functions'. "
            "Never a draw.io style or stencil string."
        )
    )
    label: str = Field(description="Short human-readable name shown under the icon.")
    annotation: str = Field(
        default="",
        description=(
            "Optional short (2-3 word) contextual tag rendered as a bracket suffix on the "
            "label, e.g. 'PII', 'KMS', 'Node.js' — the same convention Data-flow edges already "
            "use for '[PII]' tags, applied to leaves. Use sparingly (most leaves need none); "
            "never a full sentence."
        ),
    )
    criticality: Literal["standard", "critical"] = Field(
        default="standard",
        description=(
            "'critical' only for a business-critical or single-point-of-failure component "
            "(e.g. the primary datastore, the auth service) — most leaves are 'standard'. "
            "Binary on purpose: a finer tier is not something you can judge reliably from a "
            "brief description, so don't guess."
        ),
    )

    @field_validator("shape_type")
    @classmethod
    def _semantic_only(cls, value: str) -> str:
        lowered = value.lower()
        # These are the exact leak-throughs the prompt packs currently have to
        # forbid in prose. Enforcing it here makes the rule structural.
        for banned in ("resicon=", "shape=", "mxgraph.", "image=", ";"):
            if banned in lowered:
                raise ValueError(
                    f"shape_type {value!r} looks like a draw.io style, not a semantic service key. "
                    "Icon resolution belongs to the renderer — emit e.g. 'aws_rds'."
                )
        if not value:
            raise ValueError("shape_type must not be empty")
        return value


ActorKind = Literal["users", "saas_ingress", "saas_egress", "on_prem"]


class ExternalActor(Identified):
    """Anything outside the architecture's own boundary — never nested inside it.

    Shared by every view: Network's Users/SaaS-left-of-Cloud, Logical's
    Persona/IdP spine actors, Security's and Data-flow's equivalents are all the
    same shape. Keeping this one type (rather than one per view) is what makes
    "external actors can never end up nested inside a container" a single
    guarantee instead of four near-identical ones.

    ``kind`` drives placement side in every compiler: ingress (what is hit
    first — a user, an identity provider) renders left; egress (what the
    architecture calls out to) renders right.
    """

    kind: ActorKind
    label: str


FlowClassification = Literal["none", "internal", "confidential", "pii", "pci"]
FlowProtection = Literal["none", "tls", "mtls"]


class Flow(IRModel):
    """A directed request/data-path edge between two leaf ids."""

    source: str = Field(description="id of the source cell")
    target: str = Field(description="id of the target cell")
    label: str = Field(
        default="",
        description=(
            "Short edge label (1-2 words preferred; corridor views may use up to ~6). "
            "Examples: 'HTTPS', 'DNS', 'SQL', 'Invokes'. Required on Network primary "
            "paths and all corridor flows — blank labels fail architect review."
        ),
    )
    primary: bool = Field(
        default=True,
        description="True for the main request path (solid). False for secondary/async paths (dashed).",
    )
    classification: FlowClassification = Field(
        default="none",
        description=(
            "Sensitivity of the data this flow carries. 'pii'/'pci' only when the request "
            "genuinely implies personal or payment data — never invent sensitivity that isn't "
            "there. 'confidential' for internal-but-sensitive (e.g. credentials, internal "
            "financials) that isn't personal/payment data. 'internal' for ordinary internal "
            "traffic; 'none' when classification doesn't apply (e.g. a health check)."
        ),
    )
    protection: FlowProtection = Field(
        default="none",
        description=(
            "Transport protection actually in effect on this flow. 'mtls' only for mutual-TLS "
            "(both sides authenticate — typical for service-to-service and partner links); "
            "'tls' for one-way TLS (typical for client-to-edge); 'none' only when the flow is "
            "genuinely unencrypted or protection doesn't apply."
        ),
    )

    @field_validator("label")
    @classmethod
    def _short_label(cls, value: str) -> str:
        # Long edge labels clutter diagrams, but corridor packs teach 4–6 word
        # verbs ("Cache Miss: Fetches via API", "JSON: Failed API Retries").
        # Cap at 6 words — truncate rather than reject so one chatty label
        # never fails a build, without mangling the allowed vocabulary.
        words = value.split()
        return " ".join(words[:6])


class DiagramIR(IRModel):
    """Common surface every view IR implements, so the pipeline can stay view-agnostic.

    ``external_actors``/``flows`` are deliberately NOT declared here even
    though every subclass has them identically: Pydantic orders JSON-schema
    properties by *first* definition, and both Gemini's json_mode (constrained
    decoding honours schema property order) and function-calling models emit
    fields in that order. Declaring flows on the base put it *before* the
    structural fields (corridors / cloud), forcing the model to write the
    edges before it had decided the nodes — live captures showed models
    emitting ``"flows": []`` early and never returning to it, which failed
    every view over to the direct-XML path. Each concrete IR therefore
    declares ``external_actors`` and ``flows`` itself, *after* its structural
    fields, so generation order is nodes-then-edges.
    """

    view: ViewKind
    platform: str = Field(default="aws", description="aws | gcp | azure | multi")
    title: str = Field(default="Architecture", description="Page title")

    if TYPE_CHECKING:
        # Declared per-subclass after the structural fields (see docstring);
        # annotated here (non-runtime) so the shared helpers below type-check.
        external_actors: list[ExternalActor]
        flows: list[Flow]

    def leaf_ids(self) -> set[str]:  # pragma: no cover - overridden
        raise NotImplementedError

    def structural_ids(self) -> set[str]:  # pragma: no cover - overridden
        """Every non-leaf, non-actor id: containers for Network, corridors for the rest."""
        raise NotImplementedError

    def all_ids(self) -> set[str]:
        """Every id that can legally appear as a flow endpoint.

        One formula for every view — leaves, structural containers, and
        external actors — rather than each subclass reassembling the same
        three-way union.
        """
        return self.leaf_ids() | self.structural_ids() | {a.id for a in self.external_actors}

    def _base_flow_errors(self) -> list[str]:
        """The two flow-validity checks every view needs identically.

        Subclasses with additional rules (Network's cross-cutting-group edges)
        start their ``semantic_errors()`` from this list and append their own
        — one implementation of "does this edge even make sense" instead of
        one per view.
        """
        errors: list[str] = []
        known = self.all_ids()
        for flow in self.flows:
            if flow.source not in known:
                errors.append(f"flow source {flow.source!r} is not a known cell id")
            if flow.target not in known:
                errors.append(f"flow target {flow.target!r} is not a known cell id")
            if flow.source == flow.target:
                errors.append(f"flow {flow.source!r} -> itself is not meaningful")
        return errors

    def semantic_errors(self) -> list[str]:  # pragma: no cover - overridden
        """View-specific rules that Pydantic field validation cannot express."""
        raise NotImplementedError

    def semantic_warnings(self) -> list[str]:
        """Judgment-call rules that should prompt a correction but must never
        block compilation — unlike ``semantic_errors()``, a non-empty result
        here is fed into one best-effort corrective LLM pass
        (``agent._generate_ir_with_corrections``) and then ignored regardless
        of the outcome. Default: none. ``CorridorIR`` subclasses override this
        with the "required companions" rules promoted from prompt-pack prose
        (see ``domain/ir/{logical,security,data_flow}.py``). ``NetworkIR``
        warns on missing ingress when external actors are present.
        """
        return []

    def density_notes(self) -> list[str]:
        """How big this page got, for the log — never a block, never a nudge.

        A third channel deliberately separate from both of the above, because
        density is neither. It is not ``semantic_errors``: Logical rejected a
        15-leaf journey and Network a 27-leaf estate outright, which failed the
        user for a readability preference they never agreed to — and a
        54-leaf Network page and a 24-leaf Logical page were both rendered and
        found to lay out correctly, so the caps were never describing a limit
        the compiler actually had. And it is not ``semantic_warnings`` either:
        those buy one corrective LLM pass asking the model to change the
        design, which for density means asking it to drop components the
        request named.

        So this only reaches the log. If a user asks for every service in their
        estate, they get every service in their estate, and the operator can
        still see in the logs that the page came out dense.
        """
        return []

    def is_valid(self) -> bool:
        return not self.semantic_errors()
