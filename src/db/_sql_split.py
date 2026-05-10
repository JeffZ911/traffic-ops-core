"""
SQL statement splitter — respects single-quoted strings, dollar-quoted blocks
($$...$$ or $tag$...$tag$), line comments (--), and block comments (/* */).

Why we need this: psycopg can run multi-statement SQL in one shot, but we want
per-statement reporting. A naive split on ';' breaks the function body in
001_initial_schema.sql (it contains 'new.updated_at = now();' inside $$...$$).
"""

from __future__ import annotations


def split_sql_statements(sql: str) -> list[str]:
    stmts: list[str] = []
    buf: list[str] = []
    i, n = 0, len(sql)

    while i < n:
        c = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""

        # Line comment: keep in output but skip splitting
        if c == "-" and nxt == "-":
            while i < n and sql[i] != "\n":
                buf.append(sql[i])
                i += 1
            continue

        # Block comment
        if c == "/" and nxt == "*":
            buf.append(sql[i]); buf.append(sql[i + 1]); i += 2
            while i + 1 < n and not (sql[i] == "*" and sql[i + 1] == "/"):
                buf.append(sql[i]); i += 1
            if i + 1 < n:
                buf.append(sql[i]); buf.append(sql[i + 1]); i += 2
            continue

        # Single-quoted string (with '' escape)
        if c == "'":
            buf.append(c); i += 1
            while i < n:
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        buf.append("''"); i += 2
                        continue
                    buf.append("'"); i += 1
                    break
                buf.append(sql[i]); i += 1
            continue

        # Dollar quote: $tag$ ... $tag$
        if c == "$":
            j = sql.find("$", i + 1)
            # The tag must be empty or [A-Za-z_][A-Za-z0-9_]*
            if j != -1 and all(ch.isalnum() or ch == "_" for ch in sql[i + 1 : j]):
                tag = sql[i : j + 1]
                buf.append(tag)
                i = j + 1
                end = sql.find(tag, i)
                if end == -1:
                    buf.append(sql[i:])
                    i = n
                else:
                    buf.append(sql[i : end + len(tag)])
                    i = end + len(tag)
                continue

        # Statement terminator
        if c == ";":
            stmt = "".join(buf).strip()
            if stmt:
                stmts.append(stmt)
            buf = []
            i += 1
            continue

        buf.append(c); i += 1

    tail = "".join(buf).strip()
    if tail:
        stmts.append(tail)

    # Drop pure-comment statements
    out = []
    for s in stmts:
        # remove leading comment lines to check if anything remains
        stripped = "\n".join(
            line for line in s.splitlines() if not line.strip().startswith("--")
        ).strip()
        if stripped:
            out.append(s)
    return out
