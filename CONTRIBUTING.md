# Contributing to nuh

Contributions are welcome! Before submitting a pull request, please review the following.

## License

nuh is MIT-licensed (see LICENSE and NOTICE). Contributions are accepted under the same license:
by opening a pull request, you agree that your contribution is licensed under the MIT License.

## Development setup

```bash
git clone https://github.com/justsaynuh/nuh.git
cd nuh
pip install -e ".[dev]"
```

## Running tests

The shell-parity tests compare nuh's reading of a command with what real `bash` and `zsh` do, so both must be
on `PATH` (`apt install zsh`, `brew install zsh`); without zsh those tests fail at setup.

```bash
nuh test "pytest"    # classify it first if you have nuh installed
pytest               # run the test suite
```

## Pull request guidelines

- Create a feature branch from `main`
- Keep changes focused — one feature or fix per PR
- Add tests for new behavior
- Run `pytest` before submitting
- `main` is protected — all changes require a PR

## Code conventions

- Python 3.10+; PyYAML and cel-python are the only dependencies, imported lazily where used (a hook call with no config file and no gate runs on the stdlib alone)
- No silent pass-through: don't swallow exceptions. When a fallback is justified, a comment says why the
  failure is expected, what the fallback does, and why surfacing the error would be worse
- Use `nuh test "..."` for testing commands, never `python -m nuh`
