"""Deterministic parsers: code structure, schemas, routes, owners. Zero LLM tokens.

Returns plain dicts; context.py turns them into cards and graph edges.
Heuristics are labelled where they are heuristics.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".next", "vendor", "venv", ".venv", "__pycache__", "coverage",
             ".turbo", "target", "out", ".cache", "site-packages", "migrations_backup"}
SKIP_FILES = re.compile(r"(^|/)(\.env[^/]*|.*\.(lock|min\.js|map|png|jpe?g|gif|svg|ico|pdf|zip|woff2?|ttf|mp4|mov)|package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock)$", re.I)
CODE_EXT = {".py": "python", ".ts": "ts", ".tsx": "ts", ".js": "js", ".jsx": "js", ".mjs": "js", ".go": "go",
            ".java": "java", ".rb": "ruby", ".rs": "rust", ".kt": "kotlin", ".php": "php", ".cs": "csharp"}
DOC_EXT = {".md", ".mdx", ".rst", ".txt"}
SECRET = re.compile(r"(AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}|sk-[A-Za-z0-9_\-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|xox[baprs]-[A-Za-z0-9-]{10,})")
MAX_BYTES = 200_000


def scrub(text: str) -> str:
    return SECRET.sub("[redacted secret]", text)


def walk(root: Path) -> list[Path]:
    out = []
    for p in root.rglob("*"):
        rel = p.relative_to(root)
        if any(part in SKIP_DIRS or part.startswith(".") and part not in (".github",) for part in rel.parts[:-1]):
            continue
        if p.is_file() and not SKIP_FILES.search(str(rel)) and p.stat().st_size <= MAX_BYTES:
            out.append(p)
    return out


def is_test(path: str) -> bool:
    return bool(re.search(r"(^|/)(tests?|__tests__|spec)/|(_test|\.test|\.spec|test_)[^/]*$", path))


# ---------------- Python ----------------

ROUTE_DECOR = re.compile(r"^(app|router|api|bp|blueprint)\.(get|post|put|patch|delete|route)$")
ROUTE_METHODS = {"get", "post", "put", "patch", "delete", "route", "api_route"}
ROUTER_CTORS = {"APIRouter": "prefix", "FastAPI": "root_path", "Blueprint": "url_prefix", "Flask": None}


def _kw_str(call: ast.Call, name: str | None) -> str:
    for k in call.keywords:
        if name and k.arg == name and isinstance(k.value, ast.Constant) and isinstance(k.value.value, str):
            return k.value.value
    return ""


def _routers(tree) -> dict[str, str]:
    """Module-level `router = APIRouter(prefix="/orders")` (or Blueprint url_prefix): variable -> its own prefix."""
    out = {}
    for n in tree.body:
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call):
            ctor = n.value.func.id if isinstance(n.value.func, ast.Name) else getattr(n.value.func, "attr", "")
            if ctor in ROUTER_CTORS:
                for t in n.targets:
                    if isinstance(t, ast.Name):
                        out[t.id] = "" if ctor == "FastAPI" else _kw_str(n.value, ROUTER_CTORS[ctor])
    return out


def _constants(tree) -> dict[str, str]:
    """String constants at module level and in class bodies (pydantic Settings: API_V1_STR: str = "/api/v1")."""
    out = {}
    for n in list(tree.body) + [b for c in tree.body if isinstance(c, ast.ClassDef) for b in c.body]:
        if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
            out[n.target.id] = n.value.value
        elif isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str):
            out.update({t.id: n.value.value for t in n.targets if isinstance(t, ast.Name)})
    return out


def _mounts(tree) -> list[dict]:
    """`app.include_router(orders.router, prefix="/v1")` / `app.register_blueprint(bp, url_prefix=...)`: mounts with prefixes.
    A prefix that isn't a string literal (settings.API_V1_STR) is kept as its expression so it can be resolved or reported."""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("include_router", "register_blueprint") and n.args:
            prefix, unresolved = "", None
            for k in n.keywords:
                if k.arg in ("prefix", "url_prefix"):
                    if isinstance(k.value, ast.Constant) and isinstance(k.value.value, str):
                        prefix = k.value.value
                    else:
                        unresolved = ast.unparse(k.value)
            out.append({"parent": ast.unparse(n.func.value), "child": ast.unparse(n.args[0]), "prefix": prefix, "unresolved": unresolved})
    return out


def parse_python(path: str, src: str) -> dict:
    out = {"symbols": [], "imports": [], "routes": [], "models": [], "routers": {}, "mounts": [], "import_names": {}, "constants": {}}
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return out
    lines = src.splitlines()
    out["routers"] = _routers(tree)
    out["constants"] = _constants(tree)
    out["mounts"] = _mounts(tree)
    for node in tree.body:  # `from app.routes import orders` -> orders is module app.routes.orders
        if isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                out["import_names"][a.asname or a.name] = f"{node.module}.{a.name}"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out["imports"] += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            out["imports"].append(node.module)
    for node in tree.body:
        _py_symbol(path, node, lines, out, prefix="")
    for r in out["routes"]:  # apply the router's own prefix
        r["path"] = _join(out["routers"].get(r.get("router", ""), ""), r["path"])
    # Keep only real tables, with fields inherited from base classes in the same file (SQLModel's UserBase -> User).
    classes = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
    own = {m["model"]: m for m in out["models"]}
    tables = []
    for name, node in classes.items():
        if not _is_table(node):
            continue
        fields, seen, stack = [], set(), [name]
        while stack:
            cur = stack.pop()
            if cur in seen or cur not in classes:
                continue
            seen.add(cur)
            fields = [f for f in own.get(cur, {}).get("fields", []) if f["name"] not in {x["name"] for x in fields}] + fields
            stack += [b.id for b in classes[cur].bases if isinstance(b, ast.Name)]
        if fields:
            tables.append({"model": name, "table": _py_tablename(node) or _table_name(node), "fields": fields})
    out["models"] = tables
    return out


def _is_table(node: ast.ClassDef) -> bool:
    if _py_tablename(node):
        return True
    if any(k.arg == "table" and isinstance(k.value, ast.Constant) and k.value.value is True for k in node.keywords):
        return True  # SQLModel
    bases = {ast.unparse(b) for b in node.bases}
    return bool(bases & {"Base", "DeclarativeBase", "db.Model", "models.Model", "Model"})


def _table_name(node: ast.ClassDef) -> str:
    if any(k.arg == "table" for k in node.keywords):
        return node.name.lower()  # SQLModel default: lowercased class name
    return _snake(node.name) + "s"


def _py_symbol(path, node, lines, out, prefix):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        name = f"{prefix}{node.name}"
        end = getattr(node, "end_lineno", node.lineno)
        body = "\n".join(lines[node.lineno - 1:end])
        calls = sorted({n.func.id if isinstance(n.func, ast.Name) else n.func.attr
                        for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, (ast.Name, ast.Attribute))})
        doc = (ast.get_docstring(node) or "").split("\n")[0][:160]
        kind = "class" if isinstance(node, ast.ClassDef) else "function"
        sig = lines[node.lineno - 1].strip()[:160]
        out["symbols"].append({"name": name, "kind": kind, "line": node.lineno, "end": end, "sig": sig, "doc": doc,
                               "calls": calls[:40], "body": body[:3000]})
        for d in getattr(node, "decorator_list", []):
            if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute):
                var = getattr(d.func.value, "id", "")
                target = f"{var}.{d.func.attr}"
                known = var in out.get("routers", {})
                if (ROUTE_DECOR.match(target) or (known and d.func.attr in ROUTE_METHODS)) and d.args and isinstance(d.args[0], ast.Constant):
                    method = d.func.attr.upper() if d.func.attr not in ("route", "api_route") else "GET"
                    for k in d.keywords:  # @bp.route("/x", methods=["POST"])
                        if k.arg == "methods" and isinstance(k.value, (ast.List, ast.Tuple)) and k.value.elts and isinstance(k.value.elts[0], ast.Constant):
                            method = str(k.value.elts[0].value).upper()
                    out["routes"].append({"method": method, "path": str(d.args[0].value), "handler": name, "router": var})
        if isinstance(node, ast.ClassDef):
            fields = _py_model_fields(node)
            if fields:
                table = _py_tablename(node) or _snake(node.name) + "s"
                out["models"].append({"model": node.name, "table": table, "fields": fields})
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    _py_symbol(path, child, lines, out, prefix=f"{node.name}.")


def _join(prefix: str, path: str) -> str:
    if not prefix:
        return path or "/"
    joined = prefix.rstrip("/") + ("/" + path.lstrip("/") if path and path != "/" else "")
    return joined or "/"


def _py_tablename(node):
    for b in node.body:
        if isinstance(b, ast.Assign) and any(getattr(t, "id", "") == "__tablename__" for t in b.targets):
            if isinstance(b.value, ast.Constant):
                return str(b.value.value)
    return None


def _py_model_fields(node) -> list[dict]:
    """SQLAlchemy Column/mapped_column, Django models.*Field, Pydantic/SQLModel Field."""
    fields = []
    for b in node.body:
        target, annotation = None, None
        if isinstance(b, ast.Assign) and len(b.targets) == 1 and isinstance(b.targets[0], ast.Name):
            target, value = b.targets[0].id, b.value
        elif isinstance(b, ast.AnnAssign) and isinstance(b.target, ast.Name) and b.value is not None:
            target, value, annotation = b.target.id, b.value, ast.unparse(b.annotation)
        if not target or not isinstance(value, ast.Call):
            continue
        fn = value.func.attr if isinstance(value.func, ast.Attribute) else getattr(value.func, "id", "")
        if fn in ("Column", "mapped_column") or fn.endswith("Field"):
            # typed models (SQLModel/Pydantic/SQLAlchemy 2 Mapped[]): no Optional / None in the type means required
            nullable = True if annotation is None else bool(re.search(r"Optional|None", annotation))
            fk, pk = None, False
            for kw in value.keywords:
                if kw.arg in ("nullable", "null") and isinstance(kw.value, ast.Constant):
                    nullable = bool(kw.value.value)
                if kw.arg == "primary_key":
                    nullable, pk = False, True
                if kw.arg == "foreign_key" and isinstance(kw.value, ast.Constant):  # SQLModel
                    fk = str(kw.value.value)
                if kw.arg == "to" and isinstance(kw.value, ast.Constant):  # Django ForeignKey(to="app.Model")
                    fk = str(kw.value.value).split(".")[-1].lower() + ".id"
            for a in value.args:  # SQLAlchemy Column(ForeignKey("users.id")) / Django models.ForeignKey(User)
                if isinstance(a, ast.Call) and getattr(a.func, "id", getattr(a.func, "attr", "")) == "ForeignKey" and a.args \
                        and isinstance(a.args[0], ast.Constant):
                    fk = str(a.args[0].value)
                elif fn == "ForeignKey" and isinstance(a, ast.Name):
                    fk = _snake(a.id) + "s.id"
            fields.append({"name": target, "type": fn, "nullable": nullable, "fk": fk, "pk": pk})
    return fields


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


# ---------------- JS / TS ----------------

JS_FN = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\(|"
                   r"^\s*(?:export\s+)?(?:const|let)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*(?::[^=]+)?=>|"
                   r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)", re.M)
JS_IMPORT = re.compile(r"""(?:import\s[^'"]*from\s*|require\()\s*['"]([^'"]+)['"]""")
JS_ROUTE = re.compile(r"""\b(?:app|router|server)\.(get|post|put|patch|delete)\(\s*['"]([^'"]+)['"]""")
NEXT_HANDLER = re.compile(r"export\s+(?:async\s+)?function\s+(GET|POST|PUT|PATCH|DELETE)\b")


def parse_js(path: str, src: str) -> dict:
    out = {"symbols": [], "imports": JS_IMPORT.findall(src), "routes": [], "models": []}
    lines = src.splitlines()
    starts = []
    for m in JS_FN.finditer(src):
        name = next(g for g in m.groups() if g)
        line = src.count("\n", 0, m.start()) + 1
        starts.append((line, name, "class" if m.group(3) else "function"))
    for i, (line, name, kind) in enumerate(starts):
        end = starts[i + 1][0] - 1 if i + 1 < len(starts) else len(lines)
        body = "\n".join(lines[line - 1:end])
        calls = sorted(set(re.findall(r"\b([A-Za-z_$][\w$]*)\s*\(", body)) - {"if", "for", "while", "switch", "return", "function", name})
        out["symbols"].append({"name": name, "kind": kind, "line": line, "end": end, "sig": lines[line - 1].strip()[:160],
                               "doc": "", "calls": calls[:40], "body": body[:3000]})
    for method, route in JS_ROUTE.findall(src):
        out["routes"].append({"method": method.upper(), "path": route, "handler": None})
    m = re.search(r"(?:^|/)app/(.*?)/route\.(?:ts|js)$", path) or re.search(r"(?:^|/)pages/api/(.*?)\.(?:ts|js)$", path)
    if m:
        route_path = "/" + re.sub(r"\(.*?\)/", "", m.group(1))
        methods = NEXT_HANDLER.findall(src) or ["GET"]
        for meth in methods:
            out["routes"].append({"method": meth, "path": route_path if route_path.startswith("/api") else "/api/" + m.group(1), "handler": meth})
    return out


# ---------------- Schemas ----------------

def parse_sql(src: str) -> dict:
    """CREATE TABLE and ALTER TABLE via sqlglot; falls back to regex when a dialect won't parse."""
    tables, alters = [], []
    try:
        import sqlglot
        from sqlglot import exp

        for stmt in sqlglot.parse(src, error_level=sqlglot.ErrorLevel.IGNORE):
            if stmt is None:
                continue
            if isinstance(stmt, exp.Create) and stmt.args.get("kind") == "TABLE":
                schema = stmt.this
                tname = schema.this.name if isinstance(schema, exp.Schema) else schema.name
                cols = []
                for col in stmt.find_all(exp.ColumnDef):
                    sql = col.sql().upper()
                    ref = re.search(r"REFERENCES\s+\"?(\w+)\"?\s*\(\s*\"?(\w+)", col.sql(), re.I)
                    cols.append({"name": col.name, "type": (col.args.get("kind").sql() if col.args.get("kind") else ""),
                                 "nullable": "NOT NULL" not in sql and "PRIMARY KEY" not in sql, "pk": "PRIMARY KEY" in sql,
                                 "fk": f"{ref.group(1)}.{ref.group(2)}" if ref else None})
                for fkc in stmt.find_all(exp.ForeignKey):
                    ref = re.search(r"REFERENCES\s+\"?(\w+)\"?\s*\(\s*\"?(\w+)", fkc.sql(), re.I)
                    local = [e.name for e in fkc.expressions]
                    for c in cols:
                        if ref and c["name"] in local:
                            c["fk"] = f"{ref.group(1)}.{ref.group(2)}"
                tables.append({"table": tname, "fields": cols})
            elif isinstance(stmt, exp.Alter):
                tname = stmt.this.name
                for action in stmt.args.get("actions", []):
                    alters.append({"table": tname, "sql": action.sql()[:300]})
    except Exception:
        pass
    if not tables:
        for m in re.finditer(r"create\s+table\s+(?:if\s+not\s+exists\s+)?[`\"]?(\w+)[`\"]?\s*\((.*?)\);", src, re.I | re.S):
            cols = []
            for line in m.group(2).split(","):
                cm = re.match(r"\s*[`\"]?(\w+)[`\"]?\s+(\w+)", line)
                if cm and cm.group(1).lower() not in ("primary", "foreign", "constraint", "unique", "key", "index"):
                    cols.append({"name": cm.group(1), "type": cm.group(2), "nullable": "not null" not in line.lower()})
            tables.append({"table": m.group(1), "fields": cols})
    if not alters:
        for m in re.finditer(r"alter\s+table\s+[`\"]?(\w+)[`\"]?\s+(.*?);", src, re.I | re.S):
            alters.append({"table": m.group(1), "sql": m.group(2)[:300]})
    return {"tables": tables, "alters": alters}


def parse_prisma(src: str) -> dict:
    tables = []
    for m in re.finditer(r"model\s+(\w+)\s*\{(.*?)\}", src, re.S):
        fields = []
        rels = {}
        for line in m.group(2).splitlines():
            rm = re.search(r"(\w+)\s+(\w+)\??\s+@relation\([^)]*fields:\s*\[(\w+)\][^)]*references:\s*\[(\w+)\]", line)
            if rm:
                rels[rm.group(3)] = f"{rm.group(2)}.{rm.group(4)}"
        for line in m.group(2).splitlines():
            fm = re.match(r"\s*(\w+)\s+(\w+)(\??)", line)
            if fm and not line.strip().startswith("@@") and "@relation" not in line and not fm.group(2)[0].isupper() or \
                    (fm and fm.group(2) in ("String", "Int", "BigInt", "Float", "Decimal", "Boolean", "DateTime", "Json", "Bytes")):
                fields.append({"name": fm.group(1), "type": fm.group(2), "nullable": fm.group(3) == "?", "pk": "@id" in line,
                               "fk": rels.get(fm.group(1))})
        mm = re.search(r'@@map\("(\w+)"\)', m.group(2))
        tables.append({"table": mm.group(1) if mm else m.group(1), "model": m.group(1), "fields": fields})
    enums = [{"name": m.group(1), "values": re.findall(r"^\s*(\w+)", m.group(2), re.M)}
             for m in re.finditer(r"enum\s+(\w+)\s*\{(.*?)\}", src, re.S)]
    return {"tables": tables, "enums": enums}


def parse_codeowners(src: str) -> list[tuple[str, list[str]]]:
    rules = []
    for line in src.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pattern, *owners = line.split()
        rules.append((pattern, [o.lstrip("@") for o in owners]))
    return rules


def owners_for(path: str, rules: list[tuple[str, list[str]]]) -> list[str]:
    match = []
    for pattern, owners in rules:  # last match wins, like GitHub
        p = pattern.lstrip("/")
        if p == "*" or path.startswith(p.rstrip("*")) or (p.startswith("*.") and path.endswith(p[1:])):
            match = owners
    return match


def doc_sections(path: str, src: str) -> list[dict]:
    sections, current, buf = [], path, []
    for line in src.splitlines():
        if line.startswith("#"):
            if buf:
                sections.append({"title": current, "text": "\n".join(buf)[:1500]})
            current, buf = line.lstrip("# ").strip(), []
        else:
            buf.append(line)
    if buf:
        sections.append({"title": current, "text": "\n".join(buf)[:1500]})
    return [s for s in sections if s["text"].strip()]
