# Competitor Brief Agent

A LangGraph agent that discovers a company's competitors, researches each one,
and drafts a sourced markdown brief. Two human approval gates: one before
research runs, one before anything is written to disk.

```
discover -> [GATE 1: confirm competitors] -> research -> extract
         -> compile -> [GATE 2: approve brief] -> save
```

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt

cp .env.example .env            # then fill in the keys
```

### Keys

| Key | Used for | Required |
|-----|----------|----------|
| `YDC_API_KEY` | you.com search — the agent's only data source | yes |
| `NEBIUS_API_KEY` | extraction, and discovery by default | yes |
| `OPENAI_API_KEY` | discovery, only with `DISCOVER_WITH_OPENAI=1` | no |

The pipeline runs on Nebius alone. OpenAI is opt-in.

## Run

```bash
python run_cli.py --company "Notion" --context "team wiki / collaborative docs"
python run_cli.py --company "Notion" --auto     # skip both gates
streamlit run ui/app.py
```

Briefs are written to `samples/`.

## Layout

```
graph/      state, nodes, graph assembly
tools/      you.com search wrapper (retry, disk cache, SearchError)
ui/         streamlit front end
data/       sqlite checkpoints + search cache (gitignored)
samples/    generated briefs
```

## Design notes

- **A failed competitor doesn't kill the run.** It's marked `failed` and the
  brief renders the gap rather than filling it in.
- **Nothing is invented.** Missing fields stay `"not found"` — enforced in the
  prompt and defaulted in the pydantic schema.
- **The brief is compiled deterministically** (no model call), so it can never
  drift from the extracted objects.
- **Search is cached to disk**, so re-running the same company during
  development is free. Set `NO_CACHE=1` for live results.

## Gotchas

- **you.com uses `/v1/search`.** The older `api.ydc-index.io/{search,news}`
  endpoints return 403. There is no separate news endpoint — news comes from
  `/v1/search` with `freshness` set.
- **News reads the `web` block, not `news`.** you.com's `news` block matches
  loosely on keywords; a "Notion news" query returns crypto and sports stories.
  The web block with `freshness` gives on-topic coverage.
- **Always pass `encoding="utf-8"` when writing files.** Windows defaults to
  cp1252 and corrupts the em-dashes in the brief.
- **Dependencies are pinned.** The code was written for langgraph 0.2.x but
  runs on 1.x; the `interrupt()` / `Command(resume=)` contract is unchanged.
  Re-test both gates if you bump langgraph.
