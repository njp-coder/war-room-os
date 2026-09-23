---
name: Enum value with no mapping falls back to default
fields: preferences.theme
signals: settings wiped, saved preferences gone, settings reset to default, theme preference, dark mode reset
check: SELECT count(*) AS affected FROM users WHERE theme NOT IN ('LIGHT','DARK')
check_expect: affected > 0
---
When the v2 enum is narrower, unmapped legacy values silently fall back to default.
Confirm by counting legacy values outside the mapping table. Fix: add the missing mapping and re-run for affected users.
