"""Quick manual check: does profile: minimal flow through classify_command?"""
from nuh import paths, config
from nuh.config import reset_config, NuhConfig
from nuh.bash import classify_command
import tempfile

with tempfile.TemporaryDirectory() as tmp:
    paths.set_project_root(tmp)
    reset_config()
    config._cached_config = NuhConfig(profile="minimal")

    tests = [
        ("python -c 'print(1)'", "lang_exec in full, unknown in minimal"),
        ("npm install react", "package_install in full, unknown in minimal"),
        ("cargo test", "package_run in full, unknown in minimal"),
        ("psql -c 'SELECT 1'", "db_write in full, unknown in minimal"),
        ("rm file.txt", "filesystem_delete in both"),
        ("curl example.com", "network_outbound in both"),
        ("git status", "git_safe in both"),
    ]

    for cmd, desc in tests:
        r = classify_command(cmd)
        st = r.stages[0]
        print(f"{cmd:30s} → {st.action_type:25s} decision={r.final_decision:6s}  ({desc})")
