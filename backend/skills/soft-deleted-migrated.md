---
name: Soft-deleted rows migrated as active
fields: users.deleted_at
signals: deleted account, closed account, still getting emails, newsletters, stop emailing, unsubscribe
check: SELECT count(*) AS affected FROM users WHERE deleted_at IS NOT NULL
check_expect: affected > 0
---
If the target schema has no soft-delete column, deleted rows arrive as active and re-enter email and billing flows.
Confirm by counting soft-deleted rows in legacy. Fix: suppress them now, decide whether to migrate them at all.
