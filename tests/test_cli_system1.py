"""``nuh system1 export``: the shipped definition written out as YAML to edit, and how nuh classifies it."""

import json
import os
import sys

import pytest
import yaml

from nuh import __version__, cli, config, system1_def, system1_export
from nuh.bash import classify_command
from nuh.config import _merge_configs



def run(monkeypatch, capsys, *argv: str) -> tuple[int, str, str]:
    monkeypatch.setattr(sys, "argv", ["nuh", "system1", *argv])
    with pytest.raises(SystemExit) as done:
        cli.main()
    out, err = capsys.readouterr()
    return done.value.code, out, err


def _shipped() -> dict:
    with open(system1_def.SHIPPED, encoding="utf-8") as f:
        return json.load(f)


class TestExport:
    def test_default_is_the_user_file_and_round_trips(self, env, monkeypatch, capsys):
        target = os.path.join(env["config_dir"], system1_def.USER_FILE)
        code, out, _ = run(monkeypatch, capsys, "export")
        assert code == 0 and f"Wrote {target}" in out and "Note:" not in out
        assert system1_def.path({}) == target  # nuh now reads the exported file
        exported, shipped = system1_def.load({}), system1_def.parse(_shipped(), system1_def.SHIPPED)
        assert exported.source == target
        assert (exported.preamble, exported.context, exported.questions, exported.gates) == \
            (shipped.preamble, shipped.context, shipped.questions, shipped.gates)
        with open(target, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        assert data.pop("based_on") == __version__
        assert data == _shipped()

    def test_readable_yaml(self, env, monkeypatch, capsys):
        code, text, _ = run(monkeypatch, capsys, "export", "--stdout")
        assert code == 0
        lines = text.splitlines()
        assert lines[0].startswith("# ") and "nuh system1 check" in text and "replaces the shipped" in text
        top = [line.split(":")[0] for line in lines if line and line[0].isalpha()]
        assert top == ["based_on", *_shipped()]  # based_on first, then the shipped order
        assert "preamble: |-" in lines
        assert sum(line.strip() == "instructions: |-" for line in lines) == len(_shipped()["questions"])
        assert "\\n" not in text and "{" not in text.replace("{label}", "")  # block style throughout
        assert not os.listdir(env["config_dir"])  # --stdout writes nothing

    def test_text_spanning_lines_is_a_literal_block_wherever_it_is(self):
        data = {"preamble": "First line.\nSecond line.",
                "questions": {"q": {"type": "noul", "instructions": "Is it?", "explain": "one\ntwo"}},
                "tools": {"Bash": {"preamble": "Bash only.\n  indented"}}}
        text = system1_export.render(data, "9.9.9")
        assert "    explain: |-\n      one\n      two\n" in text
        loaded = yaml.safe_load(text)
        assert loaded.pop("based_on") == "9.9.9" and loaded == data

    def test_refuses_to_overwrite_without_force(self, env, monkeypatch, capsys):
        target = env["config_dir"] / system1_def.USER_FILE
        target.write_text("questions: {}\n")
        code, _, err = run(monkeypatch, capsys, "export")
        assert code == 1 and "exists; pass --force" in err
        assert target.read_text() == "questions: {}\n"
        assert run(monkeypatch, capsys, "export", "--force")[0] == 0
        assert yaml.safe_load(target.read_text())["based_on"] == __version__

    def test_path_elsewhere_says_nuh_does_not_read_it(self, env, tmp_path, monkeypatch, capsys):
        target = tmp_path / "mine" / "s1.yaml"
        code, out, _ = run(monkeypatch, capsys, "export", str(target))
        assert code == 0 and target.is_file()
        assert f"Note: nuh reads {system1_def.SHIPPED}" in out

    def test_default_follows_a_configured_definition(self, env, tmp_path, monkeypatch, capsys):
        target = tmp_path / "defs" / "system1.yaml"
        config._cached_config = _merge_configs({"llm": {"system1": {"definition": str(target)}}}, {})
        code, out, _ = run(monkeypatch, capsys, "export")
        assert code == 0 and target.is_file() and "Note:" not in out
        assert not os.listdir(env["config_dir"])

    @pytest.mark.parametrize("argv,message", [
        (["--stdout", "x.yaml"], "takes no PATH"),
        (["x.json"], "export writes YAML"),
    ])
    def test_usage_errors(self, env, monkeypatch, capsys, argv, message):
        code, _, err = run(monkeypatch, capsys, "export", *argv)
        assert code == 2 and message in err
        assert not os.listdir(env["config_dir"])


QUESTION = {"type": "noul", "instructions": "Does it send data out?", "block": "> 0.9"}


def _write(path, data: dict) -> str:
    path.write_text(yaml.safe_dump(data))
    return str(path)


class TestCheck:
    def test_shipped_default(self, env, monkeypatch, capsys):
        code, out, err = run(monkeypatch, capsys, "check")
        assert code == 0 and err == ""
        lines = out.splitlines()
        assert lines[0] == f"Checked {system1_def.SHIPPED}" and lines[-1] == "OK"
        assert "questions (5):" in lines and "gates: none" in lines
        assert any(line.split()[:3] == ["verdict", "choice", "the"] for line in lines)
        assert "context: action_tokens 1500, session_tokens 800, file_lines 30; sends user" in lines

    def test_exported_file_is_what_it_checks(self, env, monkeypatch, capsys):
        assert run(monkeypatch, capsys, "export")[0] == 0
        code, out, _ = run(monkeypatch, capsys, "check")
        assert code == 0 and out.startswith(f"Checked {env['config_dir'] / system1_def.USER_FILE}\n")
        assert out.endswith("OK\n")

    def test_configured_definition_is_the_default(self, env, tmp_path, monkeypatch, capsys):
        where = _write(tmp_path / "s1.yaml", {"questions": {"q": QUESTION}})
        config._cached_config = _merge_configs({"llm": {"system1": {"definition": where}}}, {})
        code, out, _ = run(monkeypatch, capsys, "check")
        assert code == 0 and out.startswith(f"Checked {where}\n")
        assert "  q  noul    decides: block > 0.9" in out

    def test_malformed_yaml(self, env, tmp_path, monkeypatch, capsys):
        where = tmp_path / "s1.yaml"
        where.write_text("questions: {q: {type: noul, instructions: Is it?, block: '> 0.9'}\n")
        code, out, err = run(monkeypatch, capsys, "check", str(where))
        assert code == 1 and out == "" and f"{where}: not YAML" in err

    def test_bad_question(self, env, tmp_path, monkeypatch, capsys):
        where = _write(tmp_path / "s1.yaml", {"questions": {"q": {**QUESTION, "type": "maybe"}}})
        code, out, err = run(monkeypatch, capsys, "check", where)
        assert code == 1 and out == "" and "questions.q.type must be noul, score or choice" in err

    @pytest.mark.parametrize("block,message", [
        ({"questions": {"q": {"block": "about 0.9"}}}, "tools.Bash: questions.q.block"),
        ({"based_on": "0.5.5"}, "tools.Bash: based_on cannot be set per tool"),
        ({"questions": {"q": None}}, "tools.Bash: questions must name at least one question"),
    ])
    def test_bad_tools_block(self, env, tmp_path, monkeypatch, capsys, block, message):
        where = _write(tmp_path / "s1.yaml", {"questions": {"q": QUESTION}, "tools": {"Bash": block}})
        code, out, err = run(monkeypatch, capsys, "check", where)
        assert code == 1 and out == "" and message in err

    def test_tools_blocks_are_resolved_and_warnings_shown_once(self, env, tmp_path, monkeypatch, capsys):
        data = {"context": {"prior_decisions": True},
                "questions": {"q": QUESTION, "r": {"type": "noul", "instructions": "Reads secrets?"}},
                "tools": {"Bash": {"questions": {"r": None}}, "Edit": {"context": {"file_lines": 0}},
                          "MultiEdit": {"context": {"user": False}}, "mcp__forge__*": {"context": {"cwd_rel": True}}}}
        code, out, err = run(monkeypatch, capsys, "check", _write(tmp_path / "s1.yaml", data))
        assert code == 0 and err == ""
        assert "tools.Bash:\n  questions: q\n" in out
        assert "tools.Edit (also NotebookEdit, apply_patch):\n" in out  # MultiEdit has its own block
        assert "file_lines 0; sends user\n" in out and "sends user, cwd_rel\n" in out
        assert out.count("context.prior_decisions is not supported yet") == 1

    def test_gates_compile(self, env, tmp_path, monkeypatch, capsys):
        gate = {"name": "send", "if": "q.p > 0.8", "then": "block"}
        where = _write(tmp_path / "s1.yaml", {"questions": {"q": QUESTION}, "gates": [gate]})
        code, out, _ = run(monkeypatch, capsys, "check", where)
        assert code == 0 and "gates (1):\n  send: if q.p > 0.8 then block\n" in out and "warning" not in out

    def test_bad_cel(self, env, tmp_path, monkeypatch, capsys):
        gate = {"name": "send", "if": "q.p >", "then": "block"}
        where = _write(tmp_path / "s1.yaml", {"questions": {"q": QUESTION}, "gates": [gate]})
        code, _, err = run(monkeypatch, capsys, "check", where)
        assert code == 1 and "gate send: bad CEL" in err

    def test_gate_reading_a_question_a_tool_removed(self, env, tmp_path, monkeypatch, capsys):
        gate = {"name": "send", "if": "q.p > 0.8 || r.p > 0.5", "then": "block"}
        data = {"questions": {"q": QUESTION, "r": {"type": "noul", "instructions": "Reads secrets?"}},
                "gates": [gate], "tools": {"Bash": {"questions": {"r": None}}}}
        code, out, _ = run(monkeypatch, capsys, "check", _write(tmp_path / "s1.yaml", data))
        assert code == 0 and "warning: tools.Bash: gate send reads r, not a question here" in out
        assert "warning: gate send" not in out  # the base has r


@pytest.mark.parametrize("command,admin", [
    ("nuh system1 check", False),
    ("nuh system1 check ./def.yaml", False),
    ("nuh system1 export", True),
    ("nuh system1 export --force", True),
    ("nuh system1 export ./def.yaml", True),
    ("python3 -m nuh system1 export", True),
    ("nuh system1 export -- --stdout", True),       # after --, --stdout is the PATH
    ("nuh system1 export --stdout -- x", True),     # any -- counts as writing
    ("nuh system1 export --stdout", False),
    ("nuh system1", False),
])
def test_only_a_writing_export_is_nuh_admin(command, admin):
    result = classify_command(command)
    assert ("nuh_admin" in result.floor_rules()) == admin
    assert result.final_decision == ("ask" if admin else "allow")
