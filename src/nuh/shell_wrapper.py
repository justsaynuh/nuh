"""Shell wrappers: the command text ``bash -c``, ``bash <<<`` and ``eval`` run."""

from nuh import exec_source, word_quoting
from nuh.taxonomy import _normalize_interpreter


# Shell wrappers that need unwrapping.
_SHELL_WRAPPERS = {"bash", "sh", "dash", "zsh"}


def is_shell_wrapper(tokens: list[str]) -> tuple[bool, str | None]:
    """Detect shell-wrapper inner commands. Returns (is_wrapper, inner_command_or_None)."""
    if not tokens:
        return False, None

    cmd = _normalize_interpreter(tokens[0])

    if cmd in _SHELL_WRAPPERS:
        return _shell_command_text(tokens)

    # eval "string"
    if cmd == "eval" and len(tokens) >= 2:
        return True, word_quoting.joined(tokens[1:])

    # source / . (not unwrapped — classify as lang_exec)
    if cmd in ("source", "."):
        return False, None

    return False, None


def _shell_command_text(tokens: list[str]) -> tuple[bool, str | None]:
    """``(True, text)`` for ``bash -c TEXT`` and ``bash <<< TEXT``, else ``(False, None)``.

    ``-c`` counts only among the shell's options, before its first operand:
    in ``bash ./x.sh -c ls`` it is an argument of the script, which is what
    runs. A here-string is the program only when no operand names a script.
    exec_source holds the shell argv grammar; argv it cannot read is no
    wrapper, so the stage is classified as running an unknown program,
    never as the inner command.
    """
    source = exec_source.program_source(tokens)
    if source is None:
        return False, None
    if source.kind == exec_source.INLINE:
        return True, source.value
    if source.kind == exec_source.STDIN:
        # (None, code) for one here-string; a file, a heredoc (unwrapped by
        # the caller from the stage's body) or several redirects are not text here.
        found = exec_source.stdin_program(tokens, "")
        if found is not None and found[0] is None and found[1]:
            return True, found[1]
    return False, None
