"""HTTP clients (curl, wget, HTTPie): sending data or a write method is network_write, else network_outbound.

Phase 2 flag classifiers of ``taxonomy.classify_tokens``.
"""

from nuh.taxonomy import NETWORK_OUTBOUND, NETWORK_WRITE


_CURL_DATA_FLAGS = {
    "-d", "--data", "--data-raw", "--data-binary", "--data-urlencode",
    "-F", "--form", "--form-string", "-T", "--upload-file", "--json",
}
_CURL_DATA_LONG_PREFIXES = (
    "--data=", "--data-raw=", "--data-binary=", "--data-urlencode=",
    "--form=", "--form-string=", "--upload-file=", "--json=",
)
_CURL_METHOD_FLAGS = {"-X", "--request"}
_WRITE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}


def _classify_curl(tokens: list[str]) -> str | None:
    """Flag-dependent: curl with write flags → network_write; else → network_outbound."""
    if not tokens or tokens[0] != "curl":
        return None

    has_data = False
    has_write_method = False

    i = 1
    while i < len(tokens):
        tok = tokens[i]

        # Standalone data flags
        if tok in _CURL_DATA_FLAGS:
            has_data = True
            i += 1
            continue

        # =joined long data flags
        if any(tok.startswith(p) for p in _CURL_DATA_LONG_PREFIXES):
            has_data = True
            i += 1
            continue

        # Method flags: -X METHOD, --request METHOD, --request=METHOD
        if tok in _CURL_METHOD_FLAGS:
            if i + 1 < len(tokens):
                method = tokens[i + 1].upper()
                if method in _WRITE_METHODS:
                    has_write_method = True
            i += 2
            continue
        if tok.startswith("--request="):
            method = tok.split("=", 1)[1].upper()
            if method in _WRITE_METHODS:
                has_write_method = True
            i += 1
            continue

        # Combined short flags: -sXPOST, -XPOST, etc.
        if tok.startswith("-") and not tok.startswith("--") and len(tok) > 1:
            letters = tok[1:]
            if "X" in letters:
                x_idx = letters.index("X")
                rest = letters[x_idx + 1:]
                # Extract method: chars after X until non-alpha
                method_chars = []
                for c in rest:
                    if c.isalpha():
                        method_chars.append(c)
                    else:
                        break
                if method_chars:
                    method = "".join(method_chars).upper()
                    if method in _WRITE_METHODS:
                        has_write_method = True
                elif i + 1 < len(tokens):
                    # X is last char in combined flags, method is next token
                    method = tokens[i + 1].upper()
                    if method in _WRITE_METHODS:
                        has_write_method = True
                    i += 2
                    continue

        i += 1

    # Data flags take priority over method flags
    if has_data:
        return NETWORK_WRITE
    if has_write_method:
        return NETWORK_WRITE
    return NETWORK_OUTBOUND


def _classify_wget(tokens: list[str]) -> str | None:
    """Flag-dependent: wget with write flags → network_write; else → network_outbound."""
    if not tokens or tokens[0] != "wget":
        return None

    has_data = False
    has_write_method = False

    i = 1
    while i < len(tokens):
        tok = tokens[i]

        # --post-data, --post-file (standalone or =joined)
        if tok in ("--post-data", "--post-file"):
            has_data = True
            i += 2  # skip value
            continue
        if tok.startswith("--post-data=") or tok.startswith("--post-file="):
            has_data = True
            i += 1
            continue

        # --method METHOD or --method=METHOD
        if tok == "--method":
            if i + 1 < len(tokens):
                method = tokens[i + 1].upper()
                if method in _WRITE_METHODS:
                    has_write_method = True
            i += 2
            continue
        if tok.startswith("--method="):
            method = tok.split("=", 1)[1].upper()
            if method in _WRITE_METHODS:
                has_write_method = True
            i += 1
            continue

        i += 1

    if has_data:
        return NETWORK_WRITE
    if has_write_method:
        return NETWORK_WRITE
    return NETWORK_OUTBOUND


_HTTPIE_CMDS = {"http", "https", "xh", "xhs"}
_HTTPIE_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}


def _classify_httpie(tokens: list[str]) -> str | None:
    """Flag-dependent: httpie with write indicators → network_write; else → network_outbound."""
    if not tokens or tokens[0] not in _HTTPIE_CMDS:
        return None

    args = tokens[1:]
    has_form = False
    has_write_method = False
    has_data_item = False
    found_url = False

    for arg in args:
        # Check for --form / -f
        if arg == "--form" or arg == "-f":
            has_form = True
            continue

        # Skip other flags
        if arg.startswith("-"):
            continue

        # First non-flag arg: check if it's an uppercase method
        if not found_url and arg.upper() in _HTTPIE_METHODS:
            if arg.upper() in _WRITE_METHODS:
                has_write_method = True
            continue

        if not found_url:
            found_url = True
            continue

        # After URL: check for data item patterns (key=value, key:=value, key@file)
        if "=" in arg or ":=" in arg or "@" in arg:
            has_data_item = True

    if has_write_method:
        return NETWORK_WRITE
    if has_form:
        return NETWORK_WRITE
    if has_data_item:
        return NETWORK_WRITE
    return NETWORK_OUTBOUND
