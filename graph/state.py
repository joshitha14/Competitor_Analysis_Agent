"""Shared state for the competitor analysis graph.

One TypedDict flows through every node. Nodes return partial dicts;
LangGraph merges them. Keep this file boring and stable - if you find
yourself wanting to add a field mid-build, add it here first.
"""

from __future__ import annotations

from typing import Annotated, Literal, Optional, TypedDict
from operator import add

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------
# Structured extraction target. The extract node asks the model to fill this
# exactly. Every field is optional-with-default so a missing figure renders
# as "not found" rather than blowing up or getting invented.
# --------------------------------------------------------------------------


class CompetitorProfile(BaseModel):
    name: str
    pricing: str = Field(
        default="not found",
        description="Pricing tiers and rough numbers. Say 'not found' if the "
        "sources do not state pricing. Never estimate.",
    )
    core_features: list[str] = Field(default_factory=list)
    positioning: str = Field(
        default="not found",
        description="Who they target and how they differentiate, in 1-2 sentences.",
    )
    recent_news: list[str] = Field(
        default_factory=list,
        description="Dated headlines from the last ~6 months.",
    )
    sources: list[str] = Field(
        default_factory=list, description="URLs the above was drawn from."
    )


class Competitor(TypedDict):
    """Tracked per competitor across the run."""

    name: str
    why: str                      # one line from discovery on why it's a competitor
    status: Literal["pending", "researched", "extracted", "failed"]
    raw: list[dict]               # search results, kept for the audit trail
    profile: Optional[dict]       # CompetitorProfile.model_dump()
    error: Optional[str]          # populated when status == "failed"


class ResearchState(TypedDict, total=False):
    # --- inputs -----------------------------------------------------------
    company: str                  # the company we're finding competitors FOR
    context: str                  # optional: market/segment hint from the user

    # --- discovery --------------------------------------------------------
    candidates: list[Competitor]  # what discover() proposed
    competitors: list[Competitor] # what the human approved. research runs on this

    # --- output -----------------------------------------------------------
    brief: str                    # compiled markdown
    saved_path: Optional[str]

    # --- control / observability -----------------------------------------
    stage: str                    # "discover" | "research" | "extract" | ...
    errors: Annotated[list[str], add]   # appended, never overwritten
    tool_calls: Annotated[int, lambda a, b: a + b]
    approved_competitors: bool
    approved_brief: bool
