# Project graph

One typed graph per project, with time on every edge. Moss finds where to start
(by meaning); the graph says what is connected. Built by parsers, not by an LLM.

## Layers

| Layer | Nodes | Edges | Built from |
|---|---|---|---|
| Code | module, file, symbol (function, class, route handler) | imports, calls, defines, tests | SCIP indexers (scip-python, scip-typescript) where available, tree-sitter otherwise |
| Data (lineage) | table, field, enum value | code reads / writes field, migration alters field, maps legacy.field -> v2.field, endpoint exposes field | ORM models, migrations, schema files, SQL parsed with sqlglot, mapping specs |
| Service | service, endpoint, queue, job, external API | calls, publishes, consumes, deployed as | route decorators, OpenAPI, docker-compose / k8s / terraform |
| Change | commit, PR, deploy, release | touches symbol / field, reviewed by, shipped in, fixes bug | GitHub history + deployments |
| People | person, team | wrote, reviewed, owns, handled bug, on shift | blame, PR reviews, CODEOWNERS, ticket history, rota |
| Runtime | log pattern, error, ticket cluster, user cohort | emitted by symbol, reported as, affects cohort | log templates, stack traces, triage |
| Knowledge | decision, risk, playbook, incident, fix | applies to field / service, supersedes, confirmed by check | war room, pre-mortem, postmortems |

Every edge has `valid_from`, `valid_to` and `source` (sha or URL), so the graph can answer
"what was true at go-live" and "what changed since the staging fix".

## Queries it enables
- Blast radius: PR -> symbols -> fields -> endpoints -> features -> cohorts, before deploy
- Root cause path: ticket cluster -> log pattern -> symbol -> field -> change -> author
- Regression: production bug -> same symbol or field as a staging fix, changed after the fix
- Routing: symbol -> wrote / reviewed / owns -> people on shift
- Risk: field with nullable -> required mapping, no test touching it, changed in this release

## Storage
SQLite tables `nodes(id, type, project, props)` and `edges(src, dst, type, valid_from, valid_to, source)`
for the hackathon; an embedded graph DB (Kuzu) later if traversals get deep. Moss card IDs equal
graph node IDs, so a search hit is a traversal starting point.

## UI rule
No full-graph hairball. Show focused paths: blast radius for a PR or field, and the evidence
chain for a theory (which is a path through this graph).
