---
name: Date anchor recalculated in the wrong timezone
fields: subscriptions.billing_anchor
signals: charged twice, double charge, two payments, duplicate payment, billed twice, refund, card charged, renewal anchor, timezone
check: SELECT count(*) AS affected FROM subscriptions WHERE anchor_v2 <> anchor_legacy AND tz <> 'UTC'
check_expect: affected > 0
---
When anchors are recomputed in UTC, non-UTC accounts shift a day and a second charge lands in the same cycle.
Confirm by comparing legacy and v2 anchors for non-UTC accounts. Fix: compute in account timezone, refund duplicates, notify affected clients.
Precedent: inc-2024-07.
