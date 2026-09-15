"""CLI runner. Build against this first - it's faster to debug than Streamlit,
and it proves the interrupt/resume loop works before any UI exists.

    python run_cli.py --company "Notion" --context "team wiki / docs"
    python run_cli.py --company "Notion" --auto     # skip gates, for eval runs
"""

from __future__ import annotations

import argparse
import uuid

from dotenv import load_dotenv
from langgraph.types import Command

from graph.build import build

load_dotenv()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--company", required=True)
    ap.add_argument("--context", default="")
    ap.add_argument("--auto", action="store_true",
                    help="auto-approve both gates (eval mode)")
    args = ap.parse_args()

    app = build()
    cfg = {"configurable": {"thread_id": str(uuid.uuid4())}}
    payload: object = {"company": args.company, "context": args.context}

    while True:
        result = app.invoke(payload, cfg)

        if "__interrupt__" not in result:
            break

        gate = result["__interrupt__"][0].value

        if gate["gate"] == "confirm_competitors":
            for c in gate["candidates"]:
                print(f"  - {c['name']}: {c['why']}")
            ok = True if args.auto else input("\nResearch these? [Y/n] ").lower() != "n"
            payload = Command(resume={"approved": ok})

        elif gate["gate"] == "approve_brief":
            print("\n" + gate["brief"])
            ok = True if args.auto else input("\nSave? [Y/n] ").lower() != "n"
            payload = Command(resume={"approved": ok})

    state = app.get_state(cfg).values
    if state.get("saved_path"):
        print(f"\nSaved -> {state['saved_path']}")
    if state.get("errors"):
        print(f"\n{len(state['errors'])} non-fatal error(s) during run:")
        for e in state["errors"]:
            print(f"  ! {e}")


if __name__ == "__main__":
    main()
