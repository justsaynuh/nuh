"""Context for db_write: the database target a command names, checked against ``db_targets``."""

import urllib.parse

from nuh import taxonomy


def resolve_database_context(tokens: list[str], tool_input: dict | None) -> tuple[str, str]:
    """Resolve database context for db_write commands.

    Extracts database target from CLI flags, checks against configured db_targets.
    Returns (decision, reason).
    """
    target = _extract_db_target(tokens, tool_input)
    if target is None:
        return taxonomy.ASK, "unknown database target"

    database, schema = target
    # Normalize to uppercase for comparison
    database = database.upper() if database else None
    schema = schema.upper() if schema else None

    if not database:
        return taxonomy.ASK, "unknown database target"

    from nuh.config import get_config
    cfg = get_config()

    if not cfg.db_targets:
        return taxonomy.ASK, "no db_targets configured"

    if _matches_db_targets(database, schema, cfg.db_targets):
        label = f"{database}.{schema}" if schema else database
        return taxonomy.ALLOW, f"allowed target: {label}"

    label = f"{database}.{schema}" if schema else database
    return taxonomy.ASK, f"unrecognized target: {label}"


def _extract_db_target(tokens: list[str] | None, tool_input: dict | None) -> tuple[str, str | None] | None:
    """Extract database target from CLI flags or tool input.

    Returns (database, schema) or None if not determinable.
    """
    # Phase 2 MCP path: tool_input has database/schema keys
    if tool_input:
        if "database" in tool_input or "schema" in tool_input:
            db = tool_input.get("database")
            sc = tool_input.get("schema")
            if db:
                return (db, sc)

    if not tokens:
        return None

    cmd = tokens[0]

    if cmd == "psql":
        return _extract_psql_target(tokens)
    if cmd == "snowsql":
        return _extract_snowsql_target(tokens)
    if cmd == "snow" and len(tokens) >= 2 and tokens[1] == "sql":
        return _extract_snow_sql_target(tokens)

    return None


def _extract_flag_value(tokens: list[str], short: str, long: str, start: int = 1) -> str | None:
    """Extract first occurrence of a flag value from tokens.

    Handles: -d VALUE, -dVALUE (glued), --dbname VALUE, --dbname=VALUE.
    """
    i = start
    while i < len(tokens):
        tok = tokens[i]
        # Long form: --flag=value or --flag value
        if tok == long:
            if i + 1 < len(tokens):
                return tokens[i + 1]
            return None
        if tok.startswith(long + "="):
            return tok[len(long) + 1:]
        # Short form: -d value or -dvalue (glued)
        if short:
            if tok == short:
                if i + 1 < len(tokens):
                    return tokens[i + 1]
                return None
            if tok.startswith(short) and len(tok) > len(short):
                return tok[len(short):]
        i += 1
    return None


def _extract_psql_target(tokens: list[str]) -> tuple[str, str | None] | None:
    """Extract database from psql flags or connection string."""
    # CLI flag takes priority
    db = _extract_flag_value(tokens, "-d", "--dbname")
    if db:
        return (db, None)

    # Check for connection string URL
    for tok in tokens[1:]:
        if tok.startswith("-"):
            continue
        if tok.startswith("postgresql://") or tok.startswith("postgres://"):
            parsed = urllib.parse.urlparse(tok)
            # Only extract from path, not query params (fail-safe)
            if parsed.path and len(parsed.path) > 1:
                return (parsed.path.lstrip("/"), None)

    return None


def _extract_snowsql_target(tokens: list[str]) -> tuple[str, str | None] | None:
    """Extract database and schema from snowsql flags."""
    db = _extract_flag_value(tokens, "-d", "--dbname")
    if not db:
        return None
    schema = _extract_flag_value(tokens, "-s", "--schemaname")
    return (db, schema)


def _extract_snow_sql_target(tokens: list[str]) -> tuple[str, str | None] | None:
    """Extract database and schema from snow sql flags."""
    # snow sql --database X --schema Y (start at 2 to skip "snow sql")
    db = _extract_flag_value(tokens, "", "--database", start=2)
    if not db:
        return None
    schema = _extract_flag_value(tokens, "", "--schema", start=2)
    return (db, schema)


def _matches_db_targets(database: str, schema: str | None, db_targets: list[dict]) -> bool:
    """Check if database/schema matches any configured db_targets entry.

    Each entry has 'database' (required) and optional 'schema'.
    Wildcard '*' matches anything.
    """
    for entry in db_targets:
        entry_db = entry.get("database")
        if not entry_db:
            continue
        entry_db = entry_db.upper() if isinstance(entry_db, str) else entry_db

        # Database must match
        if entry_db != "*" and entry_db != database:
            continue

        # Schema: absent/None/"*" matches anything, otherwise must match
        entry_schema = entry.get("schema")
        if entry_schema is None or (isinstance(entry_schema, str) and entry_schema == "*"):
            return True
        entry_schema = entry_schema.upper() if isinstance(entry_schema, str) else entry_schema
        if schema is None or entry_schema == schema:
            return True

    return False
