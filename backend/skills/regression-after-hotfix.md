---
name: Hotfix undone by a later change
fields: invoices.due_date
signals: due date, invoice date, one day off, renewal date, shifted again, came back
check: SELECT count(*) AS affected FROM users WHERE tz IN ('Asia/Kolkata','Asia/Singapore') AND batch IN ('batch-1','batch-2')
check_expect: affected > 0
---
A symptom that was fixed earlier in the war room returns after a later deploy touching the same file.
Confirm by finding a fix card and a later change to the same file. Fix: restore the hotfix and add a test.
