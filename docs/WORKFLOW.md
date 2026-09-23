# War Room OS: product workflow

The war room is one stage of a release, not the product. The product is a project's
memory that grows from the day a release is planned until after it is live.

## Hierarchy

```
Organization (company or agency)
 └─ Client                (agencies only; hidden for a single company)
     └─ Project           (repos + people + always-on context)
         └─ Release       (a scheduled deployment, e.g. "v2 migration, Oct 4, 22:00")
             ├─ Plan
             ├─ Build           development context attaches as work happens
             ├─ Staging         bugs found here must close before go-live
             ├─ Go / no-go      gates checked, CTO signs off
             ├─ Live            deploy window, then the war room (hypercare)
             └─ Closed          postmortem, learnings feed the next release
```

Access is granted per project with a role (owner, lead, engineer, support, client guest, viewer).
Each project has its own Moss indexes, so an agent briefed on one project can't retrieve another's context.

## Release stages

| Stage | What happens | Context added | Agents | Exit gate |
|---|---|---|---|---|
| Plan | Name, target window, environments, scope (GitHub milestone, label or release branch), migration yes/no | Scope issues, design docs, decisions | Dispatcher checks who is available in the window | Scope and window set |
| Build | Branches and PRs get linked automatically | PRs with review comments, schema and migration diffs, feature flags, decisions from calls | Risk agent keeps the pre-mortem current against the release diff | Code complete, staging deploy |
| Staging | QA tickets, test failures and staging logs come in | Bugs tagged `env=staging`, fixes, checks | Triage, root cause, dispatcher | No open blockers, every high risk verified on staging |
| Go / no-go | Readiness brief generated from the board | Rollback plan, runbook, on-call roster, client comms | Readiness agent | CTO approves in "Needs you" |
| Live | Deploy window runs, then the war room opens with everything above already loaded | Production tickets, log patterns, call, decisions, hotfixes | Triage, root cause, dispatcher, status, call | Hypercare exit criteria met |
| Closed | Auto postmortem from the event log | Confirmed causes become playbooks and risk rules | Pattern library | Archived, context stays searchable |

Regression rule: any production bug that matches a staging fix is flagged as a regression.

## Testing (Build and Staging stages)

Human testers (role: tester) and an agentic tester share one staging board.

**Human testers**
- Raise bugs with repro steps, screenshots, severity and priority, and can mark a bug as a release blocker
- Verify fixes on staging; a blocker closes only after a tester or the agent re-verifies it
- The agent suggests a priority (severity x blast radius from the graph: users affected, client-facing, money path); the tester decides, and both values are kept

**Agentic tester** (role file `agents/tester.md`, trust: acts on staging only)
1. Test plan from the release diff: blast radius (graph) -> changed endpoints, fields, flows -> risk-based charters tied to pre-mortem risks. A lead reviews the plan.
2. Runs on staging: API checks from routes/OpenAPI, data checks (legacy vs v2 reconciliation, null and enum checks), and critical UI journeys with a headless browser.
3. Uses the risky cohorts as test data (for example legacy batch-3 accounts with NULL email), not just happy-path accounts.
4. Files bugs into the same board with evidence (request/response, screenshot, graph links). Triage dedups against human reports.
5. Re-runs the failing test when a fix deploys to staging, and every confirmed cause becomes a regression test.
- Guardrails: staging only, destructive tests need approval, per-release token budget. Test generation and execution are code; the model only writes charters and repro text.

**Coverage map**: each high risk and each changed flow shows who tested it (human or agent), when, and the result. Untested high risks block the go/no-go gate.

## Gates (configurable per project)
- No open release blockers
- Every high risk from the pre-mortem has a verification check that passed on staging
- Rollback plan attached
- On-call coverage for the deploy window, nobody over the hours cap
- Client update approved when a client-facing change ships

## GitHub mapping
- Release scope: milestone, label or release branch
- Environments and deploys: GitHub Deployments / Actions environments (`staging`, `production`)
- Incremental sync by webhook (push, pull_request, pull_request_review, issues, deployment_status), re-indexing only changed blobs by SHA
