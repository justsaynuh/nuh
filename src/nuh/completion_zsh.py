"""The zsh completion function (compsys), rendered from nuh's argparse tree.

``render`` returns a ``#compdef`` file: one function per command path
(``_nuh_config_show``) that hands ``_arguments`` one spec per option and
positional, and one ``_describe`` list per level of subcommands. The file
works autoloaded from a directory on ``fpath`` and sourced
(``source <(nuh completion zsh)``).

Every spec is single-quoted for the shell, and its text is escaped for the
field it lands in: an option's ``[explanation]`` escapes ``\\``, ``[``, ``]``
and ``:``; a ``:message:`` escapes ``\\`` and ``:``; a word of a value list
(which ``_arguments`` evaluates) is shell-escaped; a ``_describe`` entry
escapes ``\\`` throughout and the ``:`` of its name. So help text or a
choice holding ``$(...)`` is shown, never run.
"""

from __future__ import annotations

import argparse
import string
from collections import defaultdict

from nuh import __version__
from nuh.cli_tree import exits, help_option, options, remainder, repeatable, subcommand_label, walk
from nuh.completion import Values, ValueKind, plain, values

_SHELL_SAFE = frozenset(string.ascii_letters + string.digits + "_.,/+@%=-")


def _quote(text: str) -> str:
    """*text* as one single-quoted zsh word."""
    return "'" + text.replace("'", "'\\''") + "'"


def _escape(text: str, specials: str) -> str:
    return "".join(f"\\{char}" if char in specials else char for char in text)


def _explanation(text: str) -> str:
    """Help text for an option spec's ``[...]``."""
    return _escape(plain(text), "\\[]:")


def _message(text: str) -> str:
    return _escape(plain(text), "\\:")


def _entry(name: str, summary: str) -> str:
    """A ``_describe`` entry, ``name:description``: backslashes escaped in both, colons in the name."""
    return _escape(name, "\\:") + ":" + _escape(plain(summary), "\\")


def _word(text: str) -> str:
    """*text* shell-escaped, as one word of a list ``_arguments`` evaluates."""
    return "".join(char if char in _SHELL_SAFE else f"\\{char}" for char in text)


def _function(prog: str, path: str) -> str:
    return "_" + "_".join([prog, *path.split()])


def _words(found: Values) -> str:
    """A value list: ``(a b)``, or ``((a\\:about\\ a b\\:about\\ b))`` when the words are described.

    In the described form ``_arguments`` evaluates each item and hands the result to ``_describe``, so an
    item is a ``_describe`` entry, shell-escaped.
    """
    if not found.descriptions:
        return "(" + " ".join(_word(word) for word in found.words) + ")"
    items = (_word(_entry(word, text)) for word, text in zip(found.words, found.descriptions))
    return "((" + " ".join(items) + "))"


def _action(arg: argparse.Action) -> str:
    """The ``_arguments`` action that completes *arg*'s value; empty completes nothing and shows the message."""
    found = values(arg)
    if found.kind is ValueKind.WORDS:
        return _words(found)
    return {ValueKind.FILES: "_files", ValueKind.DIRECTORIES: "_directories"}.get(found.kind, "")


def _value(arg: argparse.Action) -> str:
    """``:message:action`` once per value *arg* takes (``::`` when the value is optional)."""
    label = str(arg.metavar) if arg.metavar is not None else (arg.dest.upper() if arg.option_strings else arg.dest)
    one = f":{_message(label)}:{_action(arg)}"
    if arg.nargs == "?":
        return f":{one}"
    return one * (arg.nargs if isinstance(arg.nargs, int) else 1)


def _exclusive(parser: argparse.ArgumentParser, arg: argparse.Action) -> list[str]:
    """The other options *arg* cannot be given with: its mutually exclusive groups' flags."""
    return [flag for group in parser._mutually_exclusive_groups if arg in group._group_actions
            for other in group._group_actions if other is not arg for flag in other.option_strings]


def _option_specs(parser: argparse.ArgumentParser, arg: argparse.Action) -> list[str]:
    flags = arg.option_strings
    many = repeatable(arg)
    excluded = _exclusive(parser, arg) + (flags if len(flags) > 1 and not many else [])
    if exits(arg):
        exclusion = "(- : *)"
    else:
        exclusion = f"({' '.join(excluded)})" if excluded else ""
    text = f"[{_explanation(arg.help or '')}]"
    specs: list[str] = []
    for flag in flags:
        joiner = "" if arg.nargs == 0 else ("=" if flag.startswith("--") else "+")
        tail = "" if arg.nargs == 0 else _value(arg)
        specs.append(f"{exclusion}{'*' if many else ''}{flag}{joiner}{text}{tail}")
    return specs


def _positional_spec(arg: argparse.Action) -> str:
    if arg.nargs in ("*", "+") or remainder(arg):
        return "*" + _value(arg)
    return _value(arg)


def _specs(parser: argparse.ArgumentParser) -> list[str]:
    """Every ``_arguments`` spec of *parser*: ``-h/--help``, then its options and positionals in help order."""
    found = help_option(parser)
    result = _option_specs(parser, found) if found is not None else []
    for arg in options(parser):
        result += _option_specs(parser, arg) if arg.option_strings else [_positional_spec(arg)]
    return result


def _arguments(flags: str, spec_list: list[str]) -> list[str]:
    words = [f'_arguments {flags} : "$@"', *(_quote(spec) for spec in spec_list)]
    return ["  " + " \\\n    ".join(words)]


def _commands_function(name: str, label: str, children: list[tuple[str, str]]) -> list[str]:
    entries = [f"    {_quote(_entry(child, summary))}" for child, summary in children]
    return [f"{name}_commands() {{", "  local -a commands=(", *entries, "  )",
            f"  _describe -t commands {_quote(label)} commands", "}", ""]


def _parent(parser: argparse.ArgumentParser, prog: str, path: str, children: list[tuple[str, str]]) -> list[str]:
    """A level with subcommands: its own options, then the subcommand name, then that subcommand's function."""
    name = _function(prog, path)
    label = (subcommand_label(parser) or "command").lower()
    context = "-".join([prog, *path.split()])
    lines = [f"{name}() {{", "  local curcontext=$curcontext state state_descr line ret=1", "  typeset -A opt_args"]
    lines += _arguments("-C", [*_specs(parser), f"1:{_message(label)}:{name}_commands", "*:: :->args"])
    lines[-1] += " && ret=0"
    lines += ["  if [[ $state == args ]]; then", f"    curcontext=${{curcontext%:*:*}}:{context}-$words[1]:"]
    lines += ["    case $words[1] in"]
    lines += [f"      ({_word(child)}) {_function(prog, f'{path} {child}')} && ret=0 ;;" for child, _ in children]
    lines += ["    esac", "  fi", "  return ret", "}", ""]
    return lines + _commands_function(name, label, children)


def _leaf(parser: argparse.ArgumentParser, prog: str, path: str) -> list[str]:
    return [f"{_function(prog, path)}() {{", *_arguments("-S", _specs(parser)), "}", ""]


def render(parser: argparse.ArgumentParser) -> str:
    """The completion file for *parser*'s program (``nuh``; the tests also render fixture parsers)."""
    prog = parser.prog
    tree = list(walk(parser))
    children: defaultdict[str, list[tuple[str, str]]] = defaultdict(list)
    for path, _, summary in tree:
        if path:
            parent, _, child = path.rpartition(" ")
            children[parent].append((child, summary))
    lines = [f"#compdef {prog}",
             f"# zsh completion for {prog} {__version__}, generated by `{prog} completion zsh` from the installed CLI.",
             f"# `{prog} install` writes it and `{prog} update` refreshes it after an upgrade.", ""]
    for path, sub, _ in tree:
        lines += _parent(sub, prog, path, children[path]) if children[path] else _leaf(sub, prog, path)
    main = _function(prog, "")
    lines += ["# Autoloaded from fpath, this file is the function's body; sourced, it registers the function.",
              f'if [[ "$funcstack[1]" == {_quote(main)} ]]; then', f'  {main} "$@"', "else",
              f"  compdef {main} {prog}", "fi"]
    return "\n".join(lines) + "\n"
