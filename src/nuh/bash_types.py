"""What the Bash classifier passes around: stages, their results, and how a result escalates.

``Stage`` is one simple command as parsed (``bash_parse``); ``StageResult``
is its classification and ``ClassifyResult`` the whole command's. The
helpers here raise a result to a stricter verdict, never lower it.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field

from nuh import exec_source, floor, taxonomy

_MAX_UNWRAP_DEPTH = 5


_STDOUT_FDS = ("", "1", "&")


@dataclass
class Redirect:
    """One redirect that writes a file: descriptor ("" / "1" / "2" / "&", "0" for ``<>``), target, ``>>``.

    A ``<>`` redirect is also a read: its operator and file stay among the
    stage's words (``input_redirects``).
    """

    fd: str
    target: str
    append: bool = False


@dataclass
class Stage:
    tokens: list[str]
    operator: str = ""  # |, &&, ||, ;
    # Every output redirect to a file, wherever it sits in the command
    # (`git 2>/dev/null status` is one command with one redirect).
    redirects: list[Redirect] = field(default_factory=list)
    heredoc_literal: str = ""
    heredoc_bodies: tuple[str, ...] = ()  # separate bodies, in redirect order (including other descriptors)
    stdin_ambiguous: bool = False  # bash replaces repeated stdin redirects; zsh may concatenate them
    body_words: int = 0  # trailing tokens that are heredoc body words, not shell words
    action_hint: str = ""  # Pre-set action type (e.g. env var exec sink)
    action_reason: str = ""
    unparseable: bool = False  # shlex could not read it: tokens are a whitespace split, always ask
    brace_unread: str = ""  # a word whose brace expansion nuh cannot read (shell_braces): unknown, it asks
    script_body: str | None = None  # the script it runs was written by a heredoc just before
    env_floor: tuple[str, str] | None = None  # floor.Verdict for the environment this stage sets
    prefix_env: tuple[str, ...] = ()  # the NAME=value words before its command, as written (shell_env)
    # Inside compound syntax (if, loops, braces, functions) as far as the nesting
    # shows (zsh_syntax.compound_depth_change): a cd or assignment here may run
    # any number of times.
    compound: bool = False
    scopes: tuple[int, ...] = ()  # ids of the compound constructs open around it, outermost first (_Scopes)
    # Loop variables a for header binds just before this command (shell_loops),
    # each with the words of its list (None: none nuh reads) and the scope id of the loop's body.
    loop_vars: tuple[tuple[str, tuple[str, ...] | None, int], ...] = ()
    # Words holding a substitution that compound syntax left out of every stage
    # (a for/select list, a repeat count): the substitution still runs, here.
    carried: tuple[str, ...] = ()
    opens: int = 0    # subshells `(` opened just before this command
    closes: int = 0   # subshells `)` closed just after it (its operator is the outer one)

    @property
    def redirect_targets(self) -> list[str]:
        return [r.target for r in self.redirects]


def _shell_words(stage: Stage) -> list[str]:
    """*stage*'s tokens without the heredoc body words attached to their tail."""
    return stage.tokens[:len(stage.tokens) - stage.body_words]


def _words_with_targets(stage: Stage) -> list[str]:
    """*stage*'s tokens, redirect targets and carried words: every word a substitution can sit in."""
    return [*stage.tokens, *(r.target for r in stage.redirects), *stage.carried]


@dataclass
class StageResult:
    tokens: list[str]
    action_type: str = taxonomy.UNKNOWN
    default_policy: str = taxonomy.ASK
    decision: str = taxonomy.ASK
    reason: str = ""
    redirect_target: str = ""
    # The path a filesystem stage's context policy decided by (for a write, the
    # strictest of the operands it modifies): what an "outside project" hint names.
    context_target: str = ""
    # lang_exec program text that is not a file (-c code, heredoc, here-string,
    # write-then-run body): what the content scan read, shown to the LLM reviewer.
    inline_program: exec_source.InlineProgram | None = None
    unreadable_program: bool = False  # unknown produced bytes cannot rest on a recipe's content scan
    # Results of the commands folded into this one: the inner stages of an
    # unwrapped shell (``bash -c "echo; python3 -c …"``) and the commands of
    # its substitutions. The decision above already accounts for them; later
    # layers (the LLM veto and prompt) still need each one, so none is dropped.
    inner_stages: list["StageResult"] = field(default_factory=list)
    # The floor rule (floor.RULES) behind this decision, "" when none: the
    # LLM may not lower it unless the global config sets the rule to review.
    floor: str = ""

    def walk(self) -> Iterator["StageResult"]:
        """This result, then every folded inner result, depth first."""
        yield self
        for inner in self.inner_stages:
            yield from inner.walk()


@dataclass
class ClassifyResult:
    command: str
    stages: list[StageResult] = field(default_factory=list)
    final_decision: str = taxonomy.ASK
    reason: str = ""
    composition_rule: str = ""
    floor: str = ""  # a command-level floor rule (hidden characters, fork bomb), "" when none

    def all_stages(self) -> list[StageResult]:
        """Every stage result, the inner ones folded into unwraps and substitutions included."""
        return [sr for top in self.stages for sr in top.walk()]

    def floor_rules(self) -> list[str]:
        """Floor rules behind this command's ask or block stages, command-level first."""
        rules = [self.floor] if self.floor else []
        for sr in self.all_stages():
            if sr.floor and sr.decision in (taxonomy.ASK, taxonomy.BLOCK) and sr.floor not in rules:
                rules.append(sr.floor)
        return rules


def _escalate(sr: StageResult, verdict: tuple[str, str]) -> None:
    """Take *verdict* when it blocks, is stricter than *sr*, or is as strict and floored higher.

    A floored ask replaces an unfloored one (and an enforced rule one set to
    review): the stage still asks, but the LLM may no longer lower it (``floor``).
    """
    decision, reason = verdict
    rule = floor.rule_of(verdict)
    new = taxonomy.STRICTNESS.get(decision, 2)
    current = taxonomy.STRICTNESS.get(sr.decision, 2)
    if decision == taxonomy.BLOCK or new > current or (
            decision != taxonomy.ALLOW and new == current and floor.outranks(rule, sr.floor)):
        sr.decision, sr.reason, sr.floor = decision, reason, rule


def _escalate_unknown(sr: StageResult, reason: str, user_actions: dict[str, str] | None) -> StageResult:
    """Treat *sr* as an unknown command when that is stricter: what it touches is not known.

    The stage's own result stays in ``inner_stages``.
    """
    policy = taxonomy.get_policy(taxonomy.UNKNOWN, user_actions)
    if taxonomy.STRICTNESS.get(policy, 2) <= taxonomy.STRICTNESS.get(sr.decision, 2):
        return sr
    return StageResult(tokens=sr.tokens, action_type=taxonomy.UNKNOWN, default_policy=policy,
                       decision=policy, reason=reason, inner_stages=[sr])


def _obfuscated_result(tokens: list[str], reason: str, user_actions: dict[str, str] | None) -> StageResult:
    """Build a StageResult for obfuscated commands."""
    sr = StageResult(tokens=tokens)
    sr.action_type = taxonomy.OBFUSCATED
    sr.default_policy = taxonomy.get_policy(taxonomy.OBFUSCATED, user_actions)
    sr.decision = sr.default_policy
    sr.reason = reason
    return sr


def _runs_found_result(tokens: list[str], reason: str, user_actions: dict[str, str] | None) -> StageResult:
    """An unknown command (not floored): the program is a file found at run time."""
    policy = taxonomy.get_policy(taxonomy.UNKNOWN, user_actions)
    return StageResult(tokens=tokens, action_type=taxonomy.UNKNOWN, default_policy=policy, decision=policy,
                       reason=reason)


def _only_unknown_asks(sr: StageResult) -> bool:
    """True when every stage in *sr* that asks is an unknown command with no floor, and none blocks."""
    asking = [s for s in sr.walk() if s.decision != taxonomy.ALLOW]
    return bool(asking) and all(s.decision == taxonomy.ASK and s.action_type == taxonomy.UNKNOWN and not s.floor
                                and not s.unreadable_program
                                for s in asking)
