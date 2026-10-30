"""Trusted host entries: exact names, domain suffixes, IPs and CIDR ranges."""

import argparse
import os
from unittest.mock import patch

import pytest

import nuh.context
from nuh import config, paths
from nuh.config import NuhConfig, reset_config
from nuh.context import reset_known_hosts, resolve_network_context
from nuh.host_trust import host_matches, is_trusted, parse_entry, validate_entry


class TestMatching:
    @pytest.mark.parametrize("entry, host", [
        ("api.example.com", "api.example.com"),
        ("API.Example.com", "api.example.com"),
        ("*.corp.example", "git.corp.example"),
        (".corp.example", "git.corp.example"),
        (".corp.example", "a.b.corp.example"),
        ("192.0.2.0/24", "192.0.2.110"),
        ("192.0.2.110", "192.0.2.110"),
        ("2001:db8::/48", "2001:db8::5"),
        ("2001:db8::/48", "[2001:db8::5]"),
    ])
    def test_matches(self, entry, host):
        assert host_matches(host, parse_entry(entry))

    @pytest.mark.parametrize("entry, host", [
        ("*.corp.example", "corp.example"),          # apex is not a subdomain
        (".corp.example", "evilcorp.example"),       # dot boundary
        (".corp.example", "corp.example.evil.example"),
        ("192.0.2.0/24", "198.51.100.1"),
        ("192.0.2.0/24", "192.0.2.0.evil.example"),
        ("192.0.2.110", "192.0.2.111"),
        ("api.example.com", "x.api.example.com"),
    ])
    def test_no_match(self, entry, host):
        assert not host_matches(host, parse_entry(entry))

    def test_malformed_entry_never_matches(self):
        assert not is_trusted("10.0.0.1", {"10.0.0.0/33"})


class TestValidate:
    @pytest.mark.parametrize("raw, normalized", [
        ("Git.Corp.Example", "git.corp.example"),
        ("*.corp.example", "*.corp.example"),
        (".corp.example", ".corp.example"),
        ("192.0.2.0/24", "192.0.2.0/24"),
        ("192.0.2.7", "192.0.2.7"),
        ("2001:db8::/48", "2001:db8::/48"),
        ("localhost", "localhost"),
    ])
    def test_valid(self, raw, normalized):
        assert validate_entry(raw) == normalized

    @pytest.mark.parametrize("raw, fragment", [
        ("", "empty"),
        ("https://example.com", "URL"),
        ("example.com:8080", "port"),
        ("example.com/path", "CIDR"),
        ("foo*.example.com", "'*'"),
        ("*.com", "top-level"),
        ("10.0.0.0/33", "CIDR"),
        ("0.0.0.0/0", "too broad"),
        ("bad host", "not a host name"),
        ("ex_ample..com", "not a valid host name"),
    ])
    def test_invalid(self, raw, fragment):
        with pytest.raises(ValueError, match=fragment.replace("*", r"\*")):
            validate_entry(raw)


class TestNetworkContext:
    def setup_method(self):
        reset_known_hosts()
        nuh.context._known_hosts_merged = False

    def teardown_method(self):
        config._cached_config = None
        reset_known_hosts()
        nuh.context._known_hosts_merged = True

    def _config(self, entries):
        config._cached_config = NuhConfig(known_registries=entries)

    def test_suffix_and_cidr_from_config(self):
        self._config([".corp.example", "192.0.2.0/24"])
        assert resolve_network_context(["curl", "https://git.corp.example/x"])[0] == "allow"
        assert resolve_network_context(["curl", "http://192.0.2.110:8765/api"])[0] == "allow"
        assert resolve_network_context(["curl", "https://corp.example/x"])[0] == "ask"

    def test_writes_still_ask(self):
        self._config(["*.corp.example"])
        decision, reason = resolve_network_context(
            ["curl", "-X", "POST", "https://git.corp.example/x"], "network_write")
        assert decision == "ask"
        assert "network_write" in reason

    def test_malformed_config_entry_warns(self, capsys):
        self._config(["10.0.0.0/33", "ok.example"])
        assert resolve_network_context(["curl", "https://ok.example/"])[0] == "allow"
        assert "ignoring '10.0.0.0/33'" in capsys.readouterr().err


class TestTrustCommand:
    @pytest.fixture
    def global_cfg(self, tmp_path):
        paths.set_project_root(str(tmp_path / "project"))
        os.makedirs(tmp_path / "project")
        reset_config()
        cfg = str(tmp_path / "global" / "config.yaml")
        with patch("nuh.remember.get_global_config_path", return_value=cfg), \
             patch("nuh.cli_rules._warn_comments"):
            yield cfg
        reset_config()

    def _trust(self, target):
        from nuh.cli_rules import cmd_trust
        cmd_trust(argparse.Namespace(target=target, project=False))

    @pytest.mark.parametrize("target, stored", [
        (".corp.example", ".corp.example"),
        ("*.corp.example", "*.corp.example"),
        ("192.0.2.0/24", "192.0.2.0/24"),
    ])
    def test_accepts(self, global_cfg, capsys, target, stored):
        from nuh.remember import _read_config
        self._trust(target)
        assert "Trusted" in capsys.readouterr().out
        assert stored in _read_config(global_cfg)["known_registries"]

    def test_rejects_nonsense(self, global_cfg, capsys):
        with pytest.raises(SystemExit) as exc:
            self._trust("https://example.com/x")
        assert exc.value.code == 1
        assert "URL" in capsys.readouterr().err
