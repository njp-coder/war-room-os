---
name: Text double-encoded during charset change
fields: users.display_name
signals: garbled name, weird characters, broken accents, accented letters, garbage symbols, replacement character
check: SELECT count(*) AS affected FROM users WHERE name_encoding = 'latin1' AND signup_year < 2019
check_expect: affected > 0
---
latin1 bytes decoded as utf8 (or encoded twice) produce mojibake like "Ã©". Usually limited to older rows.
Confirm by counting latin1 rows in the affected cohort. Fix: detect encoding per row and re-decode.
