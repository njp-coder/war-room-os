# War Room OS

People and agents run a release together: plan, build, staging, go/no-go, live, closed.
Underneath is a **context engine** that gives agents (ours, and coding agents over MCP) the smallest
context that is sufficient for the right decision.

Built for the YC Fall 2026 x Moss Zero Latency Builder Sprint. Stack: Moss, Next.js, Python, FastAPI (LiveKit next).

## How a bug moves (who does what)

```
Reported -> Reproduced -> Cause found -> Owner proposed -> Fixing -> Fixed -> Verified
 anyone     repro agent   root cause     dispatcher +      owner      owner    tester
                          agent          ETA agent         accepts,
                                                           agrees ETA
```
Agents do the first four steps the moment a bug arrives. People only accept (or pass), agree the ETA, fix and verify.
The dispatcher balances load against each person's remaining hours (12h cap) and pairs a free engineer with the expert
when the expert is full or off shift. Every bug has a room with the relay, a flow diagram (symptom, entry point, code,
data, change, people), runnable repro steps, and the cause with its evidence.

## War rooms: opened by people or by the monitoring agent
Connect a read-only **production monitoring** database (Postgres `pg_stat_statements` / `pg_stat_activity` /
`pg_stat_database`, MySQL `performance_schema`). The monitoring agent samples it, learns a baseline per query, and flags
slow queries, spikes against baseline, failing queries, failed transactions, long-running queries, lock waits and
connection saturation. Guardrails per project: thresholds, how many checks a breach must last, cooldown, a cap on auto
war rooms per hour, and a mode (suggest / auto for medium / auto). When a war room opens, agents link the query to its
tables, the code that uses them, the PRs that last changed that code and their authors, find missing indexes, and propose
mitigations (add index, cancel query, revert). The agent never runs them; a person approves and runs.

## Databases

The staging database and production monitoring both go through `backend/adapters.py`, so data checks, repro queries, the schema diagram and slow-query monitoring work the same way on every engine:

| Database | Connection URL | Queries agents write | Monitoring source |
|---|---|---|---|
| PostgreSQL | `postgresql://user:pw@host/db` | SQL | pg_stat_statements, pg_stat_activity |
| MySQL | `mysql://user:pw@host/db` | SQL | performance_schema |
| SQLite | `sqlite:////path/file.db` | SQL | none |
| SQL Server | `mssql://user:pw@host:1433/db` | T-SQL | sys.dm_exec_query_stats, dm_exec_requests (needs VIEW SERVER STATE) |
| Snowflake | `snowflake://user:pw@account/db/schema?warehouse=WH&role=R` | Snowflake SQL | INFORMATION_SCHEMA.QUERY_HISTORY() |
| BigQuery | `bigquery://project/dataset?credentials_path=key.json&location=US` | GoogleSQL, capped at 1 GB billed per query | INFORMATION_SCHEMA.JOBS_BY_PROJECT |
| MongoDB | `mongodb://user:pw@host/db` | JSON: find, count or aggregate (no $out, $merge or JavaScript) | profiler (level 1) and currentOp |
| DynamoDB | `dynamodb://KEY:SECRET@region` (`?endpoint=` for Local) | PartiQL SELECT, scans capped at 5,000 items | none (use CloudWatch through Datadog or Grafana) |

Postgres, MySQL, SQLite, SQL Server, MongoDB and DynamoDB were tested against real servers. Snowflake and BigQuery were only checked for the queries they generate and the write-blocking; they haven't been run against a live account.

For document databases the schema comes from sampling 200 documents per collection. A field that is missing or null in some of them counts as nullable. MongoDB references are inferred from `<name>_id` fields.

## Staging database
Connect a read-only staging database (Postgres, MySQL, SQLite; URL encrypted at rest). The agent tester generates data
checks from the schema it parsed from your code (required fields with NULLs or empty values), picks affected records as
test accounts, and repro agents run read-only queries. Every statement is parsed and must be a single SELECT, the session
is read-only with a timeout, and results are capped.

## Humans and agents test together
One test session per release. The agent tester plans checks from the pre-mortem, runs what is safe to automate
(read-only data checks, parameter-free GETs on staging) and hands everything else to human testers, balanced by load,
with draft steps, a named test account, the reason it needs a person, and a thread. Testers record their screen in the
browser, attach files, pass or fail. A failure files a bug with the evidence attached. Saved steps become a recipe the
agent reuses next time ("taught by Ishita"). When the repro agent can't reproduce a bug safely, it asks a tester the same way.

## Expert agents: seniors' judgement when only juniors are on

Each top-level area of the repo with 5 or more PRs gets an expert agent. It isn't an expert because its prompt says so. It's an expert because of what it knows and how well that has held up, and you can inspect both:

- **Its knowledge base** covers only its area and is built from what people actually did:
  - review comments on PRs in the area (bots, "thanks" and pasted code are dropped)
  - bugs in the area that were fixed and verified, with their cause
  - war rooms in the area and what resolved them
  - lessons people teach it directly in Setup
  - replies seniors or leads give it in a bug thread
- **It shows who it learned from**, by name. People who aren't on the project are marked.
- **It has a track record:** how often its advice was followed, whether the fix then held (settled when the bug is verified), and how often it was marked not useful.

After the dispatcher proposes an owner, the expert reviews the plan in the thread in two cases: the owner isn't senior in that area, or the change touches a migration, the data model, auth, payments or shared data access. Seniority is a level a lead sets per project. If no one sets it, history decides: 3 or more PRs written or reviewed in the area. When the owner isn't senior and the change is risky, the expert names a senior who must sign off, preferring one who's on shift. If no senior is on the project, it says who to invite. It never approves anything itself.

## Tracks: teams inside a project

A project can have tracks such as Ops, Sales or Platform, each marked engineering or business, set up in Setup → Tracks.

Each track owns areas of the context map, one owner per area. Ownership drives routing:
- A bug or war room in an area is routed to the track that owns it as agents diagnose it.
- A release lists the tracks whose areas it changes.
- Each track has its own page showing what affects it now, what's shipping, and what's being fixed.

Context has two layers:
- **Shared:** code, schema, history, the context map, the project brief and the org experts.
- **Track:** the track's own questions (business tracks get customer and cost questions, not code ones) and its notes and runbooks.

Track context is tagged with its track:
- People see shared plus their own tracks; owners and leads see everything.
- An agent working on a bug sees shared plus the owning track's context, or shared only if the bug hasn't been routed.

Business tracks can draft a customer update for a live problem, in plain words with no internals. It's only a draft: nothing is sent.

## Agents run through one harness
`backend/harness/`: role files in `backend/agents/*.md` declare tools, trust and budget; one tool registry; hooks check
every call (allowlist, trust vs tool risk, pinned decisions) and the citation gate checks final answers; every call is
traced in `runs` and shown in the bug room. Judgment steps (root cause, repro) are model-driven when a key is set;
routing and test execution stay deterministic.

## Run it

```bash
# API (port 8010)
cd ~/Documents/war-room-os
uv venv pyenv --python 3.12
VIRTUAL_ENV=$PWD/pyenv uv pip install -r backend/requirements.txt "uvicorn[standard]" "mcp[cli]"
pyenv/bin/python -m backend.demo.generate      # fictional Lumen demo data
pyenv/bin/uvicorn backend.app:app --port 8010

# Web (port 3217)
cd web && npm install && npm run dev -- --port 3217
```

Open http://localhost:3217 and **Continue with GitHub**. The first time, you name your org and become its admin; then **New project** and pick your repo. Invite teammates from **People** by GitHub handle: they join when they sign in with that account.

Sign-in needs a GitHub OAuth App (github.com/settings/developers → OAuth Apps → New). Homepage URL is `WARROOM_PUBLIC_URL`; callback URL is `WARROOM_PUBLIC_URL/api/auth/github/callback`. Put the client ID and secret in `.env` as `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET`.

### Keys (`.env` in the repo root)
| Key | What it does | Without it |
|---|---|---|
| `MOSS_PROJECT_ID`, `MOSS_PROJECT_KEY` | Real Moss indexes, one set per project | Local TF-IDF fallback, labelled "Local index" in the UI |
| `GEMINI_API_KEY` | Root-cause theories (reason role) | Theories are built from evidence only, labelled as such |
| `GITHUB_TOKEN` | GitHub API and clone | Uses your `gh` CLI login |

Model roles live in `config/models.yaml` (Gemini now, Claude later: change `provider`).

## What happens when you connect a repo
Shallow clone, then seven layers, all parsing and no LLM tokens:

| Layer | Built from |
|---|---|
| Structure | Python AST, TS/JS parser: symbols, calls, imports |
| Data | SQL (sqlglot), Prisma, SQLAlchemy/Django/SQLModel models, migrations, code-to-field links |
| Service | FastAPI/Flask decorators, Express routes, Next.js route handlers |
| History | Last 60 PRs with review comments, issues, deployments, co-changed files |
| People | PR authors and reviewers, CODEOWNERS |
| Docs | Markdown sections |

Re-sync only re-parses files whose blob SHA changed. Secrets are redacted before indexing; `.env` and lockfiles are skipped.

## Context engine
`POST /api/projects/{id}/engine` with `op` = `understand | precedent | impact | conventions | explain | context`.
Every item says why it is included, and output is packed to a token budget.

Measured with `GET /api/projects/{id}/engine/eval` (Recall@10 of the files each merged PR touched, from its title).
On fastapi/full-stack-fastapi-template (22 PRs): keyword 50%, vector-only 45%, **engine 75%**. Measured on HEAD, so treat it as an upper bound.

### Use it from Claude Code
```bash
claude mcp add warroom -e WARROOM_PROJECT=<project id> -- bash -c "cd ~/Documents/war-room-os && pyenv/bin/python -m backend.mcp_server"
```

## Roles
owner, lead, engineer, tester, support, viewer, client_guest. Every project route checks membership.
Client guests see release progress only, never code, PRs or the graph.
Sign-in is GitHub OAuth with server-side sessions (an HttpOnly cookie; only its hash is stored). Every `/api` route needs a session, and writes from another site are refused. `WARROOM_DEMO_MODE=1` brings back the pick-a-person sign-in for stage demos; anyone can act as anyone while it's on, and every page says so.

## Known limits
- The local fallback index is lexical; semantic clustering and search need Moss keys.
- The agent tester calls parameter-free GET endpoints on the staging URL; data checks run only on the demo replica for now.
- Voice (LiveKit) is wired in the demo war room UI but not connected yet.
- Code parsing covers Python and TS/JS; other languages get file-level context only.

Docs: [workflow](docs/WORKFLOW.md), [graph](docs/GRAPH.md).
