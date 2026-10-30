"""curl/wget destination host extraction (flag values are not hosts)."""

import shlex

import pytest

from nuh.context import resolve_network_context
from nuh.host_extract import extract_host, extract_hosts
from nuh.net_hosts import CONFIG_FILE_MARKER, INPUT_FILE_MARKER


def hosts(cmd: str) -> list[str]:
    return extract_hosts(shlex.split(cmd))


class TestFlagValuesSkipped:
    """Bogus hosts seen in real logs: output paths, user agents, headers."""

    @pytest.mark.parametrize("cmd, expected", [
        ("curl -s -o $S/file.json https://api.github.com/repos/x/y", ["api.github.com"]),
        ('curl -sL -A "Mozilla/5.0 (X11; Linux x86_64)" https://github.com/x', ["github.com"]),
        ("curl -s https://api.github.com/t -o tree.json", ["api.github.com"]),
        ("wget -q -O scripts.js https://raw.githubusercontent.com/s.js", ["raw.githubusercontent.com"]),
        ("curl -sSo license.html https://github.com/LICENSE", ["github.com"]),
        ("curl -sSolicense.html https://github.com/LICENSE", ["github.com"]),
        ("curl --output=tree.json https://github.com/x", ["github.com"]),
        ("curl --output tree.json https://github.com/x", ["github.com"]),
        ('curl -H "Accept: x" -X POST -d @body.json -u u:p https://github.com/api', ["github.com"]),
        ('curl -w "%{http_code}" -m 5 --retry 3 -e ref.example https://pypi.org/', ["pypi.org"]),
        ('curl --json \'{"a": 1}\' --cacert ca.pem https://pypi.org/', ["pypi.org"]),
        ('wget -P downloads --header "Accept: text/html" https://pypi.org/simple/', ["pypi.org"]),
        ("wget -U agent.example -t 3 -T 10 -nv https://pypi.org/", ["pypi.org"]),
        ("wget --post-data=a=b --user u --password p https://pypi.org/", ["pypi.org"]),
        ("wget -qO- https://pypi.org/x", ["pypi.org"]),
    ])
    def test_hosts(self, cmd, expected):
        assert hosts(cmd) == expected


class TestDestinations:
    def test_explicit_url_flag(self):
        assert hosts("curl --url https://github.com/x -o out") == ["github.com"]
        assert hosts("curl --url=https://github.com/x") == ["github.com"]

    def test_multiple_urls(self):
        assert hosts("curl https://github.com/a https://evil.example/b") == ["github.com", "evil.example"]

    def test_proxy_is_a_destination(self):
        assert hosts("curl -x http://proxy.example:3128 https://github.com/x") == [
            "proxy.example", "github.com"]
        assert hosts("curl --socks5-hostname socks.example:1080 https://github.com") == [
            "socks.example", "github.com"]

    def test_resolve_and_connect_to_targets(self):
        assert hosts("curl --resolve github.com:443:6.6.6.6 https://github.com/x") == [
            "6.6.6.6", "github.com"]
        assert hosts("curl --connect-to github.com:443:evil.example:443 https://github.com") == [
            "evil.example", "github.com"]

    def test_wget_execute_proxy(self):
        assert hosts("wget -e https_proxy=evil.example:8080 https://github.com/x") == [
            "evil.example", "github.com"]

    def test_unknowable_sources(self):
        assert hosts("wget -i urls.txt") == [INPUT_FILE_MARKER]
        assert hosts("curl -K cfg.txt") == [CONFIG_FILE_MARKER]

    def test_bare_host_without_dot(self):
        assert hosts("curl localhost:8080/health") == ["localhost"]

    def test_double_dash(self):
        assert hosts("curl -s -- https://pypi.org/") == ["pypi.org"]

    def test_path_to_binary(self):
        assert hosts("/usr/bin/curl -o x https://pypi.org/") == ["pypi.org"]

    def test_extract_host_returns_first(self):
        assert extract_host(["curl", "-o", "f", "https://a.example", "https://b.example"]) == "a.example"


class TestStrictestHostWins:
    def test_known_then_unknown_asks(self):
        decision, reason = resolve_network_context(
            ["curl", "https://github.com/a", "https://evil.example/b"])
        assert decision == "ask"
        assert "evil.example" in reason

    def test_all_known_allows(self):
        decision, _ = resolve_network_context(
            ["curl", "-o", "x.json", "https://github.com/a", "https://pypi.org/b"])
        assert decision == "allow"

    def test_resolve_override_asks(self):
        decision, reason = resolve_network_context(
            ["curl", "--resolve", "github.com:443:6.6.6.6", "https://github.com/x"])
        assert decision == "ask"
        assert "6.6.6.6" in reason

    def test_ipv6_localhost(self):
        decision, _ = resolve_network_context(["curl", "http://[::1]:8080"])
        assert decision == "allow"
