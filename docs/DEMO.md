# Onboarding the Tiffin demo project

Tiffin (`github.com/njp-coder/tiffin`, private, cloned at `~/Documents/tiffin`) is a small meal-delivery app built to exercise every part of War Room with real signals. Nothing is faked: the errors and slow queries come from real code on `main` running against real data.

## What's planted in it

| What | Where it comes from | What should find it |
|---|---|---|
| Checkout crashes with WELCOME50 (`AttributeError: 'NoneType' object has no attribute 'replace'`) | PR #10 "Coupons expired 5.5 hours early" calls `.replace()` on `expires_at`, which is None for coupons that never expire | Log source: new error, frames map to `coupon_is_live` in `services/pricing.py` and PR #10 |
| Kitchen dashboard takes about 2 s | PR #7 indexed `kitchen_id` only; the query also filters and sorts on `created_at`, and prod has 2M orders | Monitoring: slow query on `orders`, index hint |
| Support phone search takes 0.1 to 0.6 s | PR #8 compares `replace(replace(contact_phone...))`, so no index can help | Monitoring: slow query (lower "Slow query above" to 100 ms to catch it) |
| 29,693 staging orders have no delivery slot | Open PR #15 makes `Order.delivery_slot` required, but migration 0007 adds it nullable and never backfills | Agent tester data check, bug relay, expert review |
| 2 menu items with an empty name | A bad menu import on staging | Agent tester data check |
| Coupon stacking breaks the "coupon errors are 400s" rule | Open PR #17 | Pre-mortem risk on `services/pricing.py` |

Every merged PR has review notes (the seniors' judgement the expert agents learn from), including "a coupon with no expiry is valid forever: every check must handle None", which is exactly what PR #10 broke.

## 1. Start Tiffin

```bash
cd ~/Documents/tiffin && docker compose -p tiffin up -d
```

The databases are already seeded. To rebuild them: `cd backend && .venv/bin/python scripts/seed.py` (about 3 minutes).

Staging API, for the agent tester (leave it running):

```bash
cd ~/Documents/tiffin/backend && PATH=.venv/bin:$PATH ./scripts/run_staging.sh
```

## 2. Onboard it in War Room (http://localhost:3217)

1. **New project**: name it Tiffin, then pick the `njp-coder/tiffin` repo. Sync reads code, schema, routes, 10 merged PRs with reviews, issues and docs.
2. **Setup → Connect the staging database**: `postgresql://readonly_staging:readonly@localhost:55440/tiffin_staging`
3. **Setup → Connect production monitoring**: `postgresql://readonly_monitor:readonly@localhost:55440/tiffin_prod`
4. **Setup → Logs and errors → Log file**: path `/Users/nagajyothiprakash/Documents/tiffin/backend/logs/api.log`
5. **Setup → Set the staging URL**: `http://localhost:8100`
6. **Setup → Automatic monitoring**: turn it on. Optionally set "Slow query above" to 100 ms.
7. **Setup → Expert agents → Build from history**.
8. **Now → Schedule a release**: scope Milestone, value `v1.4 Scheduled delivery`. The four open PRs (#15 to #18) join it.

## 3. Make production busy

```bash
cd ~/Documents/tiffin/backend && .venv/bin/python scripts/traffic.py --minutes 20 --quiet-start 3
```

The first 3 minutes are healthy (the monitoring agent learns baselines). After that the full lunch mix runs: WELCOME50 checkouts crash, and support and dashboard queries run slow.

## 4. What to watch

- The monitoring agent proposes war rooms under **Now → Needs you** (in "suggest" mode, a person opens them).
- In the release: plan checks, run the agent tester. It files the missing `delivery_slot` bug, and the relay reproduces it, finds the cause, and proposes an owner.

GitHub shows one author (njp-coder) on every PR. Add teammates in **Setup → People** to see routing, capacity and seniority decisions play out.
