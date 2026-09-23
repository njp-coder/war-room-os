"""Generate the fictional "Lumen" migration demo dataset.

Everything here is fictional demo data (company, people, users, tickets).
Deterministic: same seed, same files. No LLM calls.
Run: python -m backend.demo.generate
"""
import json
import random
import sqlite3
from pathlib import Path

OUT = Path(__file__).parent / "data"
SEED = 7

# War clock: go-live was Day 1 09:00. Times are minutes since go-live.
BATCHES = [
    {"id": "batch-1", "rule": "modern accounts, signup >= 2022", "start": 0, "end": 55},
    {"id": "batch-2", "rule": "modern accounts, signup 2019-2021", "start": 60, "end": 95},
    {"id": "batch-3", "rule": "legacy accounts, signup < 2019", "start": 100, "end": 135},
    {"id": "batch-4", "rule": "billing-heavy accounts (annual plans)", "start": 140, "end": 190},
    {"id": "batch-5", "rule": "accounts with custom preferences", "start": 195, "end": 230},
    {"id": "batch-6", "rule": "remaining accounts incl. soft-deleted", "start": 235, "end": 280},
]

PEOPLE = [
    # id, name, role, level, reports_to, owns, client_facing, can_approve, shift, hours_today, hours_week
    ("arjun", "Arjun Menon", "CTO", "exec", None, [], True, ["rollback", "prod_check", "client_update"], "on", 7, 41),
    ("you", "You", "War room lead", "senior", "arjun", ["migrate"], True, ["prod_check", "rollback"], "on", 9, 48),
    ("meera", "Meera Iyer", "Senior engineer, auth", "senior", "arjun", ["auth", "migrate/users"], False, ["prod_check"], "on", 9, 44),
    ("rohan", "Rohan Das", "Senior engineer, billing", "senior", "arjun", ["billing"], False, ["prod_check"], "off", 14, 52),
    ("anjali", "Anjali Verma", "Account lead", "senior", "arjun", [], True, ["client_update"], "on", 6, 30),
    ("farhan", "Farhan Qureshi", "Data engineer, migration mappers", "mid", "you", ["migrate/users", "migrate/billing"], False, [], "on", 8, 39),
    ("sameer", "Sameer Kulkarni", "Engineer, payments", "mid", "rohan", ["billing"], False, [], "on", 5, 33),
    ("kavya", "Kavya Rao", "Support lead", "mid", "anjali", ["support"], True, [], "on", 6, 35),
    ("divya", "Divya Nair", "Engineer, settings and UI", "mid", "you", ["prefs"], False, [], "on", 4, 29),
    ("tanvi", "Tanvi Shah", "Engineer, notifications", "junior", "you", ["notifications"], False, [], "on", 7, 36),
    ("vikram", "Vikram Bose", "SRE", "senior", "arjun", ["infra"], False, ["prod_check", "rollback"], "on", 10, 47),
    ("nisha", "Nisha Pillai", "Support engineer", "junior", "kavya", ["support"], False, [], "off", 0, 22),
]

# Past resolved work: evidence for "who has handled what". Never invented at runtime.
HISTORY = [
    ("meera", "auth", "Fixed login failures after SSO provider change"),
    ("meera", "migrate/users", "Reviewed email mapper for v2 validation rules"),
    ("farhan", "migrate/users", "Wrote user field mappers for legacy to v2"),
    ("farhan", "migrate/users", "Fixed charset conversion for bulk imports"),
    ("rohan", "billing", "Built billing cycle anchor calculation"),
    ("rohan", "billing", "Fixed proration bug on annual plans"),
    ("sameer", "billing", "Handled duplicate charge refunds for three clients"),
    ("sameer", "billing", "Reviewed timezone handling in invoice scheduler"),
    ("divya", "prefs", "Added theme and notification preferences to settings"),
    ("tanvi", "notifications", "Built suppression list for marketing email"),
    ("tanvi", "notifications", "Fixed unsubscribe not respected for old accounts"),
    ("vikram", "infra", "Ran cutover runbook and rollback drills"),
    ("kavya", "support", "Triaged launch-week tickets and wrote macros"),
    ("anjali", "client", "Owns Kestrel Fitness and Harbourline accounts"),
]

CODEOWNERS = """# fictional demo repo
/migrate/users/      @farhan @meera
/migrate/billing/    @farhan @rohan
/billing/            @rohan @sameer
/auth/               @meera
/prefs/              @divya
/notifications/      @tanvi
/infra/              @vikram
"""

# The planted bugs. Each is a real class of migration failure.
BUGS = {
    "email": {
        "cohort": lambda u: u["batch"] == "batch-3" and u["email"] is None,
        "weight": 94, "first_seen": 102, "service": "auth", "fields": ["users.email"],
        "phrases": [
            "can't log in, it says my email is invalid",
            "login fails with invalid email error",
            "email not valid when I sign in, but I've used this account for years",
            "sign in broken after your update, email rejected",
            "login nahi ho raha, invalid email bol raha hai",
            "password reset says email is invalid",
            "locked out of my account, invalid email address",
        ],
    },
    "charge": {
        "cohort": lambda u: u["batch"] == "batch-4" and u["tz"] != "UTC",
        "weight": 61, "first_seen": 150, "service": "billing", "fields": ["subscriptions.billing_anchor"],
        "client_affected": True,
        "phrases": [
            "I was charged twice this month",
            "double charge on my card for the subscription",
            "two payments taken for the same plan",
            "billed twice, please refund",
            "do baar paise kat gaye subscription ke",
            "our company card got charged twice for all seats",
        ],
    },
    "names": {
        "cohort": lambda u: u["signup_year"] < 2019 and u["name_latin1"],
        "weight": 48, "first_seen": 108, "service": "migrate/users", "fields": ["users.display_name"],
        "phrases": [
            "my name shows weird characters like Ã©",
            "name displays garbage symbols after the update",
            "profile name is garbled",
            "accented letters in my name are broken",
            "my name looks like Ã¡Ã± now",
        ],
    },
    "prefs": {
        "cohort": lambda u: u["theme"] == "LEGACY_DARK",
        "weight": 39, "first_seen": 200, "service": "prefs", "fields": ["preferences.theme"],
        "phrases": [
            "my saved preferences are gone",
            "dark mode reset to light after update",
            "settings got wiped",
            "theme preference not saved anymore",
            "all my settings reset to default",
        ],
    },
    "dates": {
        "cohort": lambda u: u["tz"] in ("Asia/Kolkata", "Asia/Singapore") and u["batch"] in ("batch-1", "batch-2"),
        "weight": 33, "first_seen": 2880 + 60, "service": "billing", "fields": ["invoices.due_date"],
        "phrases": [
            "invoice due date is one day off",
            "dates on my invoice are wrong by a day",
            "renewal date shows yesterday",
            "due date shifted by one day again",
        ],
    },
    "deleted": {
        "cohort": lambda u: u["soft_deleted"],
        "weight": 25, "first_seen": 250, "service": "notifications", "fields": ["users.deleted_at"],
        "phrases": [
            "I deleted my account years ago, why am I getting emails",
            "getting newsletters for a closed account",
            "account was deleted but I still receive mail",
            "please stop emailing me, my account is deleted",
        ],
    },
}

COMMITS = [
    # id, pr, minute, author, reviewer, title, files, fields, review_comment
    ("9b1e", 38, -900, "farhan", "meera", "Add v2 user schema mappers", ["migrate/users/user_mapper.py"], ["users.id", "users.created_at"], "Looks good."),
    ("c9e1", 39, -840, "farhan", "meera", "Bulk import legacy display names", ["migrate/users/name_mapper.py"], ["users.display_name"],
     "Are we sure legacy names are utf8? Some pre-2019 rows were latin1."),
    ("a1f3", 41, -720, "farhan", "meera", "EmailMapper: default missing email to empty string", ["migrate/users/email_mapper.py"], ["users.email"],
     "v2 validates email format. Won't an empty string fail validation? Merging to unblock batch 3, will revisit."),
    ("e2b6", 42, -700, "farhan", "you", "Migrate all user rows including soft-deleted", ["migrate/users/user_mapper.py"], ["users.deleted_at"],
     "Do we need soft-deleted users in v2? Keeping them for audit."),
    ("b7c2", 44, -600, "rohan", "sameer", "Recalculate billing anchors during migration", ["migrate/billing/anchor.py", "billing/cycle.py"], ["subscriptions.billing_anchor"],
     "Anchor now computed in UTC. Should we use the account timezone?"),
    ("d4a8", 46, -560, "divya", "you", "Map legacy theme enum to v2 values", ["prefs/theme_map.py"], ["preferences.theme"],
     "LIGHT and DARK mapped. Anything else falls back to default."),
    ("f001", 47, -30, "vikram", "you", "Cutover runbook and feature flags", ["infra/cutover.md"], [], "Ship it."),
    ("aa49", 49, 1500, "sameer", "rohan", "Fix invoice due date timezone (hotfix day 2)", ["billing/invoice_dates.py"], ["invoices.due_date"], "Good catch."),
    ("f5d0", 52, 2820, "farhan", "sameer", "Refactor date helpers for invoice export", ["billing/invoice_dates.py", "billing/export.py"], ["invoices.due_date"],
     "Refactor only, no behaviour change expected."),
]

DEPLOYS = [
    ("d-101", -20, "Go-live build with all mappers"),
    ("d-118", 145, "Billing anchor recalculation enabled for batch 4"),
    ("d-131", 1510, "Hotfix: invoice due date timezone"),
    ("d-140", 2830, "Date helper refactor released"),
]

CODE = {
    "migrate/users/email_mapper.py": [
        "def map_email(legacy):",
        "    # v2 requires a syntactically valid email",
        "    email = legacy.email or \"\"  # NULL becomes empty string",
        "    return V2User(email=email, verified=legacy.verified)",
    ],
    "migrate/users/name_mapper.py": [
        "def map_name(legacy):",
        "    raw = legacy.display_name.encode('latin1')",
        "    return raw.decode('utf8', errors='replace')  # double-encodes pre-2019 rows",
    ],
    "migrate/users/user_mapper.py": [
        "def map_user(legacy):",
        "    # migrates every row, deleted_at is not checked",
        "    return V2User(id=legacy.id, status='active')",
    ],
    "migrate/billing/anchor.py": [
        "def billing_anchor(sub):",
        "    return sub.renewed_at.astimezone(UTC).date()  # ignores account timezone",
    ],
    "prefs/theme_map.py": [
        "THEME = {'LIGHT': 'light', 'DARK': 'dark'}",
        "def map_theme(value):",
        "    return THEME.get(value, 'default')  # LEGACY_DARK falls through",
    ],
    "billing/invoice_dates.py": [
        "def due_date(invoice):",
        "    return invoice.issued_at.date() + NET_TERMS  # tz conversion removed in refactor",
    ],
}

MAPPING = [
    # table.field, legacy type, v2 type, nullable legacy, required v2, note
    ("users.email", "varchar(255) NULL", "email NOT NULL", True, True, "legacy allowed NULL for phone-only signups"),
    ("users.display_name", "varchar(120) latin1", "text utf8", False, True, "charset change"),
    ("users.deleted_at", "datetime NULL", "(not migrated)", True, False, "v2 has no soft-delete column"),
    ("subscriptions.billing_anchor", "date (account tz)", "date (UTC)", False, True, "timezone change"),
    ("preferences.theme", "enum(LIGHT,DARK,LEGACY_DARK)", "enum(light,dark,default)", False, True, "enum narrowed"),
    ("invoices.due_date", "date (account tz)", "date", False, True, "timezone sensitive"),
    ("users.created_at", "datetime", "timestamptz", False, True, "timezone change"),
]

PAST_INCIDENTS = [
    ("inc-2024-07", "Duplicate charges after billing provider switch",
     "Anchor dates recalculated in UTC shifted renewals for non-UTC accounts, causing a second charge. Fix: compute in account timezone, refund duplicates."),
    ("inc-2025-02", "Login outage after SSO change", "Session cache not rebuilt after cutover. Fix: flush sessions."),
    ("inc-2025-09", "Garbled names in CSV export", "latin1 data decoded as utf8 twice. Fix: detect encoding per row."),
]


def main():
    rng = random.Random(SEED)
    OUT.mkdir(parents=True, exist_ok=True)

    # users
    users = []
    tzs = ["UTC", "Asia/Kolkata", "Asia/Singapore", "Europe/London", "America/New_York"]
    for i in range(5000):
        year = rng.choice([2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024])
        legacy = year < 2019
        if legacy:
            batch = "batch-3" if rng.random() < 0.8 else "batch-6"
        elif year >= 2022:
            batch = rng.choice(["batch-1", "batch-1", "batch-4", "batch-5"])
        else:
            batch = rng.choice(["batch-2", "batch-2", "batch-4", "batch-5"])
        u = {
            "user_id": f"u{10000 + i}",
            "batch": batch,
            "account_type": "legacy" if legacy else "modern",
            "signup_year": year,
            "platform": rng.choice(["iOS", "Android", "Web"]),
            "tz": rng.choice(tzs),
            "email": None if (legacy and rng.random() < 0.35) else f"user{i}@example.com",
            "name_latin1": legacy and rng.random() < 0.3,
            "theme": rng.choice(["LIGHT", "DARK", "LEGACY_DARK"]) if batch == "batch-5" else rng.choice(["LIGHT", "DARK"]),
            "soft_deleted": batch == "batch-6" and rng.random() < 0.25,
            "plan": "annual" if batch == "batch-4" else rng.choice(["monthly", "annual"]),
            "client": "Kestrel Fitness" if (batch == "batch-4" and rng.random() < 0.3) else None,
        }
        users.append(u)

    # legacy replica (read-only target for checks)
    db = OUT / "legacy.db"
    if db.exists():
        db.unlink()
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE users(user_id TEXT, batch TEXT, account_type TEXT, signup_year INT, platform TEXT,
        tz TEXT, email TEXT, name_encoding TEXT, theme TEXT, deleted_at TEXT, plan TEXT, client TEXT)""")
    con.executemany("INSERT INTO users VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", [
        (u["user_id"], u["batch"], u["account_type"], u["signup_year"], u["platform"], u["tz"], u["email"],
         "latin1" if u["name_latin1"] else "utf8", u["theme"], "2021-03-01" if u["soft_deleted"] else None,
         u["plan"], u["client"]) for u in users])
    con.execute("CREATE TABLE subscriptions(user_id TEXT, tz TEXT, anchor_legacy TEXT, anchor_v2 TEXT, charges_this_month INT)")
    con.executemany("INSERT INTO subscriptions VALUES(?,?,?,?,?)", [
        (u["user_id"], u["tz"], "2026-09-01", "2026-08-31" if (u["batch"] == "batch-4" and u["tz"] != "UTC") else "2026-09-01",
         2 if (u["batch"] == "batch-4" and u["tz"] != "UTC") else 1) for u in users if u["plan"] == "annual"])
    con.commit()
    con.close()

    # tickets (the flood). Weighted by bug, users drawn from the planted cohort.
    tickets = []
    noise = ["", " asap", " please help", " this is urgent", " third time reporting", " on the app", " since yesterday"]
    tid = 0
    for key, bug in BUGS.items():
        cohort = [u for u in users if bug["cohort"](u)]
        for n in range(bug["weight"]):
            u = rng.choice(cohort)
            text = rng.choice(bug["phrases"]) + rng.choice(noise)
            minute = bug["first_seen"] + int(rng.expovariate(1 / 40))
            tickets.append({"id": f"t{tid:04d}", "text": text, "user_id": u["user_id"], "minute": minute, "_bug": key,
                            "client": u["client"]})
            tid += 1
    tickets.sort(key=lambda t: t["minute"])

    # logs reduced to templates (what a Drain pass would produce)
    logs = [
        {"id": "tpl-0042", "template": "ValidationError: email '' is not a valid address in EmailMapper.map_email user_id=<*>", "count": 1284, "first": 102, "service": "auth", "fields": ["users.email"]},
        {"id": "tpl-0051", "template": "Charge created for subscription <*> anchor=<*> (second charge in cycle)", "count": 612, "first": 150, "service": "billing", "fields": ["subscriptions.billing_anchor"]},
        {"id": "tpl-0063", "template": "UnicodeWarning: replacement char in display_name for user_id=<*>", "count": 734, "first": 105, "service": "migrate/users", "fields": ["users.display_name"]},
        {"id": "tpl-0077", "template": "Unknown theme value LEGACY_DARK, using default for user_id=<*>", "count": 402, "first": 198, "service": "prefs", "fields": ["preferences.theme"]},
        {"id": "tpl-0088", "template": "Email sent to user_id=<*> status=active (legacy deleted_at set)", "count": 219, "first": 240, "service": "notifications", "fields": ["users.deleted_at"]},
        {"id": "tpl-0091", "template": "Invoice due_date computed without tz for user_id=<*>", "count": 310, "first": 2835, "service": "billing", "fields": ["invoices.due_date"]},
        {"id": "tpl-0012", "template": "GET /api/session 200 in <*>ms", "count": 912345, "first": 0, "service": "auth", "fields": []},
    ]

    data = {
        "company": "Lumen (fictional)",
        "migration": "Legacy monolith to Lumen v2",
        "batches": BATCHES,
        "people": [dict(zip(["id", "name", "role", "level", "reports_to", "owns", "client_facing", "can_approve", "shift",
                             "hours_today", "hours_week"], p)) for p in PEOPLE],
        "history": [{"person": a, "area": b, "text": c} for a, b, c in HISTORY],
        "codeowners": CODEOWNERS,
        "commits": [dict(zip(["id", "pr", "minute", "author", "reviewer", "title", "files", "fields", "review"], c)) for c in COMMITS],
        "deploys": [{"id": a, "minute": b, "title": c} for a, b, c in DEPLOYS],
        "code": CODE,
        "mapping": [dict(zip(["field", "legacy", "v2", "nullable_legacy", "required_v2", "note"], m)) for m in MAPPING],
        "incidents": [{"id": a, "title": b, "text": c} for a, b, c in PAST_INCIDENTS],
        "logs": logs,
        "clients": [{"name": "Kestrel Fitness", "owner": "anjali", "seats": 240}, {"name": "Harbourline", "owner": "anjali", "seats": 90}],
    }
    (OUT / "lumen.json").write_text(json.dumps(data, indent=1))
    (OUT / "users.json").write_text(json.dumps(users))
    (OUT / "tickets.json").write_text(json.dumps(tickets, indent=0))
    print(f"users={len(users)} tickets={len(tickets)} -> {OUT}")


if __name__ == "__main__":
    main()
