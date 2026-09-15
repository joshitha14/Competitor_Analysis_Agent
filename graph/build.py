"""Graph assembly.

    discover -> [GATE 1: confirm competitors] -> research -> extract
             -> compile -> [GATE 2: approve brief] -> save

Gate 1 is the high-value one: catching a wrong competitor here saves three
wasted research loops. Gate 2 guards the only write action in the system.
"""

from __future__ import annotations

import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from graph.nodes import compile_brief, discover, extract, research, save
from graph.state import ResearchState


# --------------------------------------------------------------------------
# Gate nodes. interrupt() pauses the graph; the value passed in is surfaced
# to the caller (CLI or Streamlit), and whatever the human sends back becomes
# the return value when the graph resumes.
# --------------------------------------------------------------------------

def confirm_competitors(state: ResearchState) -> dict:
    decision = interrupt({
        "gate": "confirm_competitors",
        "question": "Research these three? Edit the list or approve.",
        "candidates": state.get("candidates", []),
    })
    # decision: {"approved": bool, "competitors": [...]}
    if not decision.get("approved"):
        return {"competitors": [], "approved_competitors": False, "stage": "cancelled"}
    return {
        "competitors": decision.get("competitors") or state["candidates"],
        "approved_competitors": True,
    }


def approve_brief(state: ResearchState) -> dict:
    decision = interrupt({
        "gate": "approve_brief",
        "question": "Save this brief?",
        "brief": state["brief"],
    })
    return {"approved_brief": bool(decision.get("approved"))}


# --------------------------------------------------------------------------
# Conditional edges
# --------------------------------------------------------------------------

def after_discover(state: ResearchState) -> str:
    # Discovery found nothing -> ask, don't invent. Ends the run cleanly.
    return "confirm" if state.get("candidates") else "compile"


def after_confirm(state: ResearchState) -> str:
    return "research" if state.get("approved_competitors") else END


def after_approve(state: ResearchState) -> str:
    return "save" if state.get("approved_brief") else END


def build(db_path: str = "data/checkpoints.sqlite"):
    g = StateGraph(ResearchState)

    g.add_node("discover", discover)
    g.add_node("confirm", confirm_competitors)
    g.add_node("research", research)
    g.add_node("extract", extract)
    g.add_node("compile", compile_brief)
    g.add_node("approve", approve_brief)
    g.add_node("save", save)

    g.add_edge(START, "discover")
    g.add_conditional_edges("discover", after_discover,
                            {"confirm": "confirm", "compile": "compile"})
    g.add_conditional_edges("confirm", after_confirm,
                            {"research": "research", END: END})
    g.add_edge("research", "extract")
    g.add_edge("extract", "compile")
    g.add_edge("compile", "approve")
    g.add_conditional_edges("approve", after_approve, {"save": "save", END: END})
    g.add_edge("save", END)

    conn = sqlite3.connect(db_path, check_same_thread=False)
    return g.compile(checkpointer=SqliteSaver(conn))
