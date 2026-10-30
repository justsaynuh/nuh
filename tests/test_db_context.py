"""Unit tests for database context resolution — FD-042."""

from nuh import config
from nuh.config import NuhConfig
from nuh.db_context import (
    _extract_db_target,
    _matches_db_targets,
    resolve_database_context,
)


# --- _extract_db_target ---


class TestExtractDbTarget:
    """CLI flag extraction for psql, snowsql, snow sql."""

    # psql
    def test_psql_d_flag(self):
        assert _extract_db_target(["psql", "-d", "mydb"], None) == ("mydb", None)

    def test_psql_d_glued(self):
        assert _extract_db_target(["psql", "-dPROD"], None) == ("PROD", None)

    def test_psql_dbname_equals(self):
        assert _extract_db_target(["psql", "--dbname=PROD"], None) == ("PROD", None)

    def test_psql_dbname_space(self):
        assert _extract_db_target(["psql", "--dbname", "PROD"], None) == ("PROD", None)

    def test_psql_connection_string(self):
        assert _extract_db_target(["psql", "postgresql://host/mydb"], None) == ("mydb", None)

    def test_psql_connection_string_query_param_none(self):
        """Query param dbname is not extracted (fail-safe)."""
        assert _extract_db_target(["psql", "postgresql://host/?dbname=mydb"], None) is None

    def test_psql_cli_wins_over_url(self):
        result = _extract_db_target(["psql", "postgresql://host/sandbox", "-d", "production"], None)
        assert result == ("production", None)

    def test_psql_first_d_wins(self):
        result = _extract_db_target(["psql", "-d", "prod", "-d", "dev"], None)
        assert result == ("prod", None)

    def test_psql_bare(self):
        assert _extract_db_target(["psql"], None) is None

    # snowsql
    def test_snowsql_d_s(self):
        assert _extract_db_target(["snowsql", "-d", "SALES", "-s", "DEV"], None) == ("SALES", "DEV")

    def test_snowsql_glued(self):
        assert _extract_db_target(["snowsql", "-dSALES", "-sDEV"], None) == ("SALES", "DEV")

    def test_snowsql_no_d(self):
        assert _extract_db_target(["snowsql"], None) is None

    def test_snowsql_d_only(self):
        assert _extract_db_target(["snowsql", "-d", "SALES"], None) == ("SALES", None)

    # snow sql
    def test_snow_sql_database_schema(self):
        result = _extract_db_target(["snow", "sql", "--database", "SALES", "--schema", "DEV"], None)
        assert result == ("SALES", "DEV")

    def test_snow_sql_bare(self):
        assert _extract_db_target(["snow", "sql"], None) is None

    def test_snow_sql_database_only(self):
        result = _extract_db_target(["snow", "sql", "--database", "SALES"], None)
        assert result == ("SALES", None)

    # tool_input (MCP path)
    def test_tool_input_database_schema(self):
        result = _extract_db_target(None, {"database": "PROD", "schema": "PUBLIC"})
        assert result == ("PROD", "PUBLIC")

    def test_tool_input_database_only(self):
        result = _extract_db_target(None, {"database": "PROD"})
        assert result == ("PROD", None)

    # unknown command
    def test_unknown_command(self):
        assert _extract_db_target(["mysql", "-e", "SELECT 1"], None) is None

    def test_empty_tokens(self):
        assert _extract_db_target([], None) is None

    def test_none_tokens(self):
        assert _extract_db_target(None, None) is None


# --- _matches_db_targets ---


class TestMatchesDbTargets:
    """db_targets matching logic."""

    def test_exact_match(self):
        targets = [{"database": "SALES", "schema": "DEV"}]
        assert _matches_db_targets("SALES", "DEV", targets) is True

    def test_database_only_match(self):
        targets = [{"database": "SANDBOX"}]
        assert _matches_db_targets("SANDBOX", "ANYTHING", targets) is True

    def test_database_only_no_schema(self):
        targets = [{"database": "SANDBOX"}]
        assert _matches_db_targets("SANDBOX", None, targets) is True

    def test_wildcard_database(self):
        targets = [{"database": "*", "schema": "DEV"}]
        assert _matches_db_targets("ANY", "DEV", targets) is True

    def test_wildcard_schema(self):
        targets = [{"database": "SALES", "schema": "*"}]
        assert _matches_db_targets("SALES", "ANYTHING", targets) is True

    def test_no_match(self):
        targets = [{"database": "SANDBOX"}]
        assert _matches_db_targets("PROD", "MAIN", targets) is False

    def test_empty_targets(self):
        assert _matches_db_targets("SALES", "DEV", []) is False

    def test_multiple_entries_second_matches(self):
        targets = [{"database": "SANDBOX"}, {"database": "SALES", "schema": "DEV"}]
        assert _matches_db_targets("SALES", "DEV", targets) is True

    def test_schema_mismatch(self):
        targets = [{"database": "SALES", "schema": "PROD"}]
        assert _matches_db_targets("SALES", "DEV", targets) is False


# --- resolve_database_context integration ---


class TestResolveDatabaseContext:
    """Integration tests for resolve_database_context."""

    def setup_method(self):
        config._cached_config = NuhConfig(db_targets=[
            {"database": "SANDBOX"},
            {"database": "SALES", "schema": "DEV"},
        ])

    def teardown_method(self):
        config._cached_config = None

    def test_matching_target_allow(self):
        decision, reason = resolve_database_context(["psql", "-d", "sandbox"], None)
        assert decision == "allow"
        assert "allowed target" in reason

    def test_non_matching_target_ask(self):
        decision, reason = resolve_database_context(["psql", "-d", "prod"], None)
        assert decision == "ask"
        assert "unrecognized target" in reason

    def test_no_flags_ask(self):
        decision, reason = resolve_database_context(["psql"], None)
        assert decision == "ask"
        assert "unknown database target" in reason

    def test_case_normalization(self):
        """Lowercase input matches uppercase target (schema-less input matches schema entry)."""
        decision, reason = resolve_database_context(["psql", "-d", "sales"], None)
        assert decision == "allow"

    def test_no_db_targets_configured(self):
        config._cached_config = NuhConfig(db_targets=[])
        decision, reason = resolve_database_context(["psql", "-d", "mydb"], None)
        assert decision == "ask"
        assert "no db_targets configured" in reason

    def test_snowsql_matching(self):
        decision, reason = resolve_database_context(["snowsql", "-d", "SALES", "-s", "DEV"], None)
        assert decision == "allow"
        assert "SALES.DEV" in reason
