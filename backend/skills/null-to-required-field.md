---
name: NULL mapped to a required field
fields: users.email
signals: invalid email, email rejected, email not valid, validation error, required field, empty string, legacy accounts, sign in, login, locked out, password reset
check: SELECT count(*) AS affected FROM users WHERE email IS NULL AND batch = 'batch-3'
check_expect: affected > 0
---
A legacy column allowed NULL; the v2 column is required. Mappers often coerce NULL to "" which then fails v2 validation.
Confirm by counting NULLs in the legacy replica for the affected cohort. Fix: route NULL values to a capture flow instead of defaulting.
False lead to avoid: session cache (fresh sessions fail too).
