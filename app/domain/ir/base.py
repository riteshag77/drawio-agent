"""Shared IR primitives used by every view's IR."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ViewKind = Literal["network", "logical", "security", "data_flow"]

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


class Flow(IRModel):
    """A directed request/data-path edge between two leaf ids."""

    source: str = Field(description="id of the source cell")
    target: str = Field(description="id of the target cell")
    label: str = Field(default="", description="Optional 1-2 word edge label, e.g. 'HTTPS'. Prefer empty.")
    primary: bool = Field(
        default=True,
        description="True for the main request path (solid). False for secondary/async paths (dashed).",
    )

    @field_validator("label")
    @classmethod
    def _short_label(cls, value: str) -> str:
        # Long edge labels are the single biggest source of unreadable diagrams;
        # truncate rather than reject so one chatty label never fails a build.
        words = value.split()
        return " ".join(words[:3])


class DiagramIR(IRModel):
    """Common surface every view IR implements, so the pipeline can stay view-agnostic."""

    view: ViewKind
    platform: str = Field(default="aws", description="aws | gcp | azure | multi")
    title: str = Field(default="Architecture", description="Page title")

    def leaf_ids(self) -> set[str]:  # pragma: no cover - overridden
        raise NotImplementedError

    def semantic_errors(self) -> list[str]:  # pragma: no cover - overridden
        """View-specific rules that Pydantic field validation cannot express."""
        raise NotImplementedError
