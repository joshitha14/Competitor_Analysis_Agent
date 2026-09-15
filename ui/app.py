"""Streamlit front end.

The whole job of this file is to drive the graph's interrupt/resume loop and
render whatever gate the graph is currently sitting at. It holds no business
logic - if you find yourself writing analysis here, it belongs in a node.

    streamlit run ui/app.py

State machine, mirrored from graph/build.py:
    idle -> running -> gate:confirm_competitors -> running
         -> gate:approve_brief -> done
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
from dotenv import load_dotenv
from langgraph.types import Command

from graph.build import build

load_dotenv()

st.set_page_config(page_title="Competitor Brief Agent", page_icon="🔭",
                   layout="wide")


# --------------------------------------------------------------------------
# Session bootstrap. The graph itself is cached across reruns; the thread_id
# is what separates one research run from another in the checkpointer.
# --------------------------------------------------------------------------

@st.cache_resource
def get_app():
    return build()


app = get_app()

for key, default in [("thread_id", None), ("phase", "idle"),
                     ("gate", None), ("final", None)]:
    st.session_state.setdefault(key, default)


def cfg():
    return {"configurable": {"thread_id": st.session_state.thread_id}}


def pump(payload):
    """Run the graph until it either finishes or hits a gate."""
    result = app.invoke(payload, cfg())
    if "__interrupt__" in result:
        st.session_state.gate = result["__interrupt__"][0].value
        st.session_state.phase = "gate"
    else:
        st.session_state.gate = None
        st.session_state.phase = "done"
        st.session_state.final = app.get_state(cfg()).values


# --------------------------------------------------------------------------
# Sidebar: inputs + live run state. The run state panel is cheap to build and
# does a lot of work in a demo - it shows the graph is stateful rather than
# you asserting that it is.
# --------------------------------------------------------------------------

with st.sidebar:
    st.header("Research a company")
    company = st.text_input("Company", placeholder="Notion")
    context = st.text_input("Market context (optional)",
                            placeholder="team wiki / collaborative docs")

    start = st.button("Find competitors", type="primary",
                      disabled=not company.strip())
    if st.button("Reset"):
        for k in ("thread_id", "phase", "gate", "final"):
            st.session_state[k] = None
        st.session_state.phase = "idle"
        st.rerun()

    if st.session_state.thread_id:
        st.divider()
        snap = app.get_state(cfg()).values
        st.caption("**Run state**")
        st.write(f"Stage: `{snap.get('stage', '—')}`")
        st.write(f"Tool calls: `{snap.get('tool_calls', 0)}`")
        for c in snap.get("competitors") or snap.get("candidates") or []:
            icon = {"pending": "○", "researched": "◐",
                    "extracted": "●", "failed": "✕"}.get(c["status"], "○")
            st.write(f"{icon} {c['name']}")
        if snap.get("errors"):
            with st.expander(f"⚠ {len(snap['errors'])} non-fatal error(s)"):
                for e in snap["errors"]:
                    st.code(e, language=None)
        st.caption(f"thread `{st.session_state.thread_id[:8]}`")


st.title("Competitor Brief Agent")
st.caption("Discovers competitors, researches each one, and drafts a sourced "
           "brief. You approve the list before research runs, and the brief "
           "before it is saved.")


# --------------------------------------------------------------------------
# Kick off
# --------------------------------------------------------------------------

if start:
    st.session_state.thread_id = str(uuid.uuid4())
    st.session_state.final = None
    with st.spinner("Searching for competitors…"):
        pump({"company": company.strip(), "context": context.strip()})
    st.rerun()


# --------------------------------------------------------------------------
# GATE 1 - confirm the competitor list.
# This is the high-value gate: a wrong name here wastes three research loops.
# The user can drop any candidate or add one the agent missed.
# --------------------------------------------------------------------------

gate = st.session_state.gate

if gate and gate["gate"] == "confirm_competitors":
    st.subheader("Step 1 · Confirm competitors")
    st.write("The agent proposes these. Uncheck anything that isn't a direct "
             "competitor, or add one it missed.")

    keep = []
    for i, c in enumerate(gate["candidates"]):
        col1, col2 = st.columns([1, 12])
        with col1:
            checked = st.checkbox("", value=True, key=f"keep_{i}",
                                  label_visibility="collapsed")
        with col2:
            st.markdown(f"**{c['name']}** — {c['why']}")
        if checked:
            keep.append(c)

    extra = st.text_input("Add a competitor the agent missed (optional)")

    c1, c2 = st.columns([1, 6])
    with c1:
        if st.button("Research these", type="primary", disabled=not (keep or extra)):
            final_list = list(keep)
            if extra.strip():
                final_list.append({"name": extra.strip(), "why": "added by user",
                                   "status": "pending", "raw": [],
                                   "profile": None, "error": None})
            with st.spinner("Researching and extracting… (~1 min)"):
                pump(Command(resume={"approved": True, "competitors": final_list}))
            st.rerun()
    with c2:
        if st.button("Cancel"):
            pump(Command(resume={"approved": False}))
            st.rerun()


# --------------------------------------------------------------------------
# GATE 2 - approve the brief before it is written to disk.
# Saving is the only write action in the system, so it sits behind approval.
# --------------------------------------------------------------------------

elif gate and gate["gate"] == "approve_brief":
    st.subheader("Step 2 · Review the brief")

    snap = app.get_state(cfg()).values
    failed = [c for c in snap.get("competitors", []) if c["status"] == "failed"]
    if failed:
        st.warning(f"{len(failed)} competitor(s) could not be researched. "
                   "The brief below shows those gaps rather than filling them in.")

    st.markdown(gate["brief"])
    st.divider()

    c1, c2, c3 = st.columns([1, 1, 5])
    with c1:
        if st.button("Approve & save", type="primary"):
            pump(Command(resume={"approved": True}))
            st.rerun()
    with c2:
        if st.button("Discard"):
            pump(Command(resume={"approved": False}))
            st.rerun()
    with c3:
        # `company` comes from the sidebar widget, which is empty on reruns
        # after the run starts - read the name off graph state instead.
        st.download_button("Download .md", gate["brief"],
                           file_name=f"{snap.get('company') or 'brief'}.md")


# --------------------------------------------------------------------------
# Terminal states
# --------------------------------------------------------------------------

elif st.session_state.phase == "done":
    final = st.session_state.final or {}
    if final.get("saved_path"):
        st.success(f"Saved to `{final['saved_path']}`")
        st.markdown(final.get("brief", ""))
        st.download_button("Download .md", final.get("brief", ""),
                           file_name=Path(final["saved_path"]).name)
    elif final.get("stage") == "cancelled":
        st.info("Run cancelled. Nothing was saved.")
    elif not final.get("candidates"):
        st.error("No competitors could be identified. This usually means the "
                 "search returned nothing usable — try adding market context, "
                 "or check the diagnostics in the sidebar.")
    else:
        st.info("Brief discarded. Nothing was saved.")

elif st.session_state.phase == "idle":
    st.info("Enter a company in the sidebar to begin.")
