"""Nodes. Each one takes state, returns a partial dict.

Design rules, enforced here:
  - A competitor that fails research does NOT kill the run. It is marked
    "failed" and the brief renders a gap.
  - Nothing is invented. If a field is absent from sources, it stays
    "not found". This is stated in the prompt AND defaulted in the schema.
  - Both write-ish moments (running research, saving the brief) sit behind
    a human gate. See graph/build.py for where the interrupts land.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from graph.state import Competitor, CompetitorProfile, ResearchState
from tools.search import SearchError, search


# --------------------------------------------------------------------------
# Models. The extract call goes to Nebius (cohort requirement); discovery and
# compilation can use whatever you like. Nebius is OpenAI-compatible, so
# ChatOpenAI with a base_url is all it takes.
# --------------------------------------------------------------------------

def nebius(model: str = "Qwen/Qwen3-30B-A3B-Instruct-2507", **kw) -> ChatOpenAI:
    import os
    return ChatOpenAI(
        model=model,
        base_url="https://api.studio.nebius.com/v1/",
        api_key=os.environ["NEBIUS_API_KEY"],
        temperature=0,
        **kw,
    )


def default_llm(**kw) -> ChatOpenAI:
    """Model for discovery.

    Defaults to Nebius so the project needs only one LLM provider. Set
    OPENAI_API_KEY and DISCOVER_WITH_OPENAI=1 to use gpt-4o-mini instead.
    """
    import os
    if os.getenv("DISCOVER_WITH_OPENAI") and os.getenv("OPENAI_API_KEY"):
        return ChatOpenAI(model="gpt-4o-mini", temperature=0, **kw)
    return nebius(**kw)


# --------------------------------------------------------------------------
# 1. DISCOVER
# --------------------------------------------------------------------------

DISCOVER_SYS = """You identify direct competitors.

Given search results about a company, name its 3 closest DIRECT competitors -
companies selling a substitutable product to the same buyer. Not partners, not
adjacent tooling, not parent companies.

Return ONLY a JSON array, no prose, no markdown fences:
[{"name": "...", "why": "one line on why they compete"}]

If the search results do not support 3 confident names, return fewer. Never
pad the list with guesses."""


def discover(state: ResearchState) -> dict:
    company = state["company"]
    ctx = state.get("context", "")
    try:
        hits = search(f"{company} competitors alternatives {ctx}", num=8)
    except SearchError as e:
        return {"errors": [f"discover: {e}"], "candidates": [], "stage": "discover"}

    resp = default_llm().invoke([
        SystemMessage(content=DISCOVER_SYS),
        HumanMessage(content=f"Company: {company}\n\nSearch results:\n{json.dumps(hits)[:6000]}"),
    ])
    raw = resp.content.strip().removeprefix("```json").removeprefix("```").removesuffix("```")

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"errors": ["discover: model did not return valid JSON"],
                "candidates": [], "stage": "discover"}

    candidates: list[Competitor] = [
        {"name": c["name"], "why": c.get("why", ""), "status": "pending",
         "raw": [], "profile": None, "error": None}
        for c in parsed[:3]
    ]
    return {"candidates": candidates, "stage": "discover", "tool_calls": 1}


# --------------------------------------------------------------------------
# 2. RESEARCH  (loop, not fan-out - 3 competitors, ~30s, not worth the
#    debugging cost of Send() on a 10-hour build)
# --------------------------------------------------------------------------

def research(state: ResearchState) -> dict:
    out: list[Competitor] = []
    errors: list[str] = []
    calls = 0

    for comp in state["competitors"]:
        name = comp["name"]
        raw: list[dict] = []
        for kind, q in (("web", f"{name} pricing plans features"),
                        ("news", f"{name} news announcement")):
            try:
                raw += search(q, kind=kind, num=5)
                calls += 1
            except SearchError as e:
                errors.append(f"research[{name}/{kind}]: {e}")

        if not raw:
            out.append({**comp, "status": "failed",
                        "error": "all searches failed - no data gathered"})
        else:
            out.append({**comp, "status": "researched", "raw": raw})

    return {"competitors": out, "errors": errors,
            "tool_calls": calls, "stage": "research"}


# --------------------------------------------------------------------------
# 3. EXTRACT  (Nebius call, structured output)
# --------------------------------------------------------------------------

EXTRACT_SYS = """Extract a competitor profile from the supplied search results.

Rules:
- Use ONLY what appears in the results. Do not use prior knowledge.
- If a field is not stated in the results, leave it as "not found".
- Never estimate, approximate, or infer a price.
- Put the URLs you actually used in `sources`."""


def extract(state: ResearchState) -> dict:
    model = nebius().with_structured_output(CompetitorProfile)
    out: list[Competitor] = []
    errors: list[str] = []

    for comp in state["competitors"]:
        if comp["status"] == "failed":
            out.append(comp)
            continue
        try:
            profile = model.invoke([
                SystemMessage(content=EXTRACT_SYS),
                HumanMessage(content=f"Competitor: {comp['name']}\n\n"
                                     f"Results:\n{json.dumps(comp['raw'])[:12000]}"),
            ])
            out.append({**comp, "status": "extracted",
                        "profile": profile.model_dump()})
        except Exception as e:
            errors.append(f"extract[{comp['name']}]: {e}")
            out.append({**comp, "status": "failed", "error": f"extraction failed: {e}"})

    return {"competitors": out, "errors": errors, "stage": "extract"}


# --------------------------------------------------------------------------
# 4. COMPILE  (deterministic - no model call, so the brief can never drift
#    from the extracted objects)
# --------------------------------------------------------------------------

def compile_brief(state: ResearchState) -> dict:
    company = state["company"]
    lines = [f"# Competitor Brief: {company}",
             f"_Generated {date.today().isoformat()}_", ""]

    for comp in state["competitors"]:
        lines.append(f"## {comp['name']}")
        if comp["status"] == "failed":
            lines += [f"> **Research incomplete.** {comp['error']}", ""]
            continue
        p = comp["profile"]
        lines += [
            f"*Why a competitor:* {comp['why']}", "",
            f"**Pricing** — {p['pricing']}", "",
            "**Core features**",
            *([f"- {f}" for f in p["core_features"]] or ["- not found"]), "",
            f"**Positioning** — {p['positioning']}", "",
            "**Recent news**",
            *([f"- {n}" for n in p["recent_news"]] or ["- not found"]), "",
            "**Sources**",
            *([f"- {s}" for s in p["sources"]] or ["- none recorded"]), "",
        ]

    if state.get("errors"):
        lines += ["---", "### Run diagnostics",
                  *[f"- {e}" for e in state["errors"]]]

    return {"brief": "\n".join(lines), "stage": "compile"}


# --------------------------------------------------------------------------
# 5. SAVE  (the write action - only reachable after approval)
# --------------------------------------------------------------------------

def save(state: ResearchState) -> dict:
    slug = state["company"].lower().replace(" ", "-")
    path = Path("samples") / f"{slug}-{date.today().isoformat()}.md"
    path.parent.mkdir(exist_ok=True)
    # Explicit encoding: Windows defaults to cp1252 and mangles the em-dashes.
    path.write_text(state["brief"], encoding="utf-8")
    return {"saved_path": str(path), "stage": "done"}
