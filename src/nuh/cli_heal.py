"""``nuh heal-log``: what the ``llm.self_heal`` workers did (llm_heal.py), newest last.

Read only: one line per worker run from ``~/.local/state/nuh/llm-heal.jsonl``,
or the raw JSON lines with ``--json``.
"""

from __future__ import annotations

import argparse
import json

from nuh import llm_heal, llm_heal_worker

_WHAT = {
    llm_heal_worker.HEALED: "loaded the model",
    llm_heal_worker.NOT_LOADED: "replayed, model still not loaded",
    llm_heal_worker.HEAL_FAILED: "replay failed",
    llm_heal_worker.ALREADY_LOADED: "model loaded but timing out; not a warm-up problem",
    llm_heal_worker.MODEL_MISSING: "model not on the server",
    llm_heal_worker.DOWN: "server down",
    llm_heal_worker.NOT_OLLAMA: "not an Ollama server",
    llm_heal_worker.BUSY: "another worker was running",
    llm_heal_worker.COOLDOWN: f"another attempt running or ended under {llm_heal.HEAL_CAP_S / 60:g} minutes ago",
    llm_heal_worker.ERROR: "worker error",
}


def cmd_heal_log(args: argparse.Namespace) -> None:
    directory = llm_heal.state_dir()
    records = llm_heal_worker.read_log(directory)
    if args.limit > 0:
        records = records[-args.limit:]
    if args.json:
        for record in records:
            print(json.dumps(record))
        return
    if not records:
        print(f"No self-heal runs logged ({directory}/{llm_heal.LOG_NAME}).")
        return
    for record in records:
        print(_line(record))


def _line(record: dict) -> str:
    outcome = str(record.get("outcome", ""))
    parts = [str(record.get("ts", "")), str(record.get("provider", "")), str(record.get("model", "")),
             str(record.get("host", "")), f"{outcome}: {_WHAT.get(outcome, outcome)}"]
    if isinstance(record.get("load_ms"), int):
        parts.append(f"load {record['load_ms'] / 1000:.1f} s")
    if record.get("error"):
        parts.append(f"error: {record['error']}")
    return "  ".join(parts)
