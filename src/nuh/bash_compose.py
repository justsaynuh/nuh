"""Stage results combined: pipe composition rules, then the strictest stage."""

from nuh import file_operands, floor, sensitive_paths, taxonomy
from nuh.bash_programs import _reads_program_from_pipe
from nuh.bash_types import ClassifyResult, Stage, StageResult


def _check_composition(stage_results: list[StageResult], stages: list[Stage]) -> tuple[str, str, str]:
    """Check pipe composition rules. Returns (decision, reason, rule) or ('', '', '')."""
    # A redirect with no command (`} 2>/dev/null | sh`) sits between the
    # commands it does not separate: compose only stages that run something.
    pairs = [(sr, st) for sr, st in zip(stage_results, stages) if st.tokens]
    stage_results = [sr for sr, _st in pairs]
    stages = [st for _sr, st in pairs]
    if len(stage_results) < 2:
        return "", "", ""

    for i in range(len(stage_results) - 1):
        # Only check pipe compositions (not && or ||)
        if i < len(stages) and stages[i].operator != "|":
            continue

        left = stage_results[i]
        right = stage_results[i + 1]

        # sensitive_read | network → block (exfiltration)
        if _is_sensitive_read(left) and right.action_type in (taxonomy.NETWORK_OUTBOUND, taxonomy.NETWORK_WRITE):
            return (taxonomy.BLOCK, f"data exfiltration: {right.tokens[0]} receives sensitive input",
                    "sensitive_read | network")

        # network | exec → block (remote code execution)
        if left.action_type in (taxonomy.NETWORK_OUTBOUND, taxonomy.NETWORK_WRITE) and _is_exec_sink_stage(right):
            return taxonomy.BLOCK, f"remote code execution: {right.tokens[0]} receives network input", "network | exec"

        # decode | exec → block (obfuscation)
        if taxonomy.is_decode_stage(left.tokens) and _is_exec_sink_stage(right):
            return taxonomy.BLOCK, f"obfuscated execution: {right.tokens[0]} receives decoded input", "decode | exec"

        # any_read | exec → ask
        if left.action_type == taxonomy.FILESYSTEM_READ and _is_exec_sink_stage(right):
            return taxonomy.ASK, f"local code execution: {right.tokens[0]} receives file input", "read | exec"

    return "", "", ""


def _is_sensitive_read(sr: StageResult) -> bool:
    """Check if a stage reads from a sensitive path."""
    inputs: list[str] = []
    for tokens in file_operands.option_readings(sr.tokens):
        reads = taxonomy.FILESYSTEM_READ in (sr.action_type, file_operands.write_mode(tokens))
        inputs += file_operands.files(tokens) + tokens[1:] if reads else file_operands.input_files(tokens)
    for tok in inputs:
        if tok.startswith("-"):
            continue
        basic = sensitive_paths.check_path_basic_raw(tok)
        if not basic:
            continue
        _decision, reason = basic
        if "hook directory" in reason:
            return True
        if "sensitive path" in reason:
            return True
    return False


def _is_exec_sink_stage(sr: StageResult) -> bool:
    """Check if a stage is an exec sink that may run its piped stdin as code.

    An interpreter whose program provably comes from elsewhere (``python -c``,
    ``node script.js``) only receives data on stdin, so the pipe rules skip it
    and the stages keep their normal classification (see exec_source).
    """
    return _reads_program_from_pipe(sr.tokens)


def _aggregate(result: ClassifyResult) -> None:
    """Aggregate stage decisions — most restrictive wins."""
    if not result.stages:
        result.final_decision = taxonomy.ALLOW
        result.reason = "no stages"
        return

    # Among equally strict stages the one holding the highest-ranked floor rule
    # (floor.rank) gives the reason, so the reason shown is the one behind the
    # floor that keeps the LLM out (floor_rules covers every stage).
    worst = max(result.stages, key=lambda sr: (taxonomy.STRICTNESS.get(sr.decision, 2), _floor_rank(sr)))
    result.final_decision = worst.decision
    result.reason = worst.reason


def _floor_rank(sr: StageResult) -> tuple[bool, bool, bool]:
    """The highest floor.rank among the asks and blocks of *sr* and its folded results."""
    return max((floor.rank(r.floor) for r in sr.walk() if r.decision in (taxonomy.ASK, taxonomy.BLOCK)),
               default=floor.rank(""))
