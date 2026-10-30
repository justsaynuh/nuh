"""Output options and mode-dependent writes must obey the project boundary."""

import os

import pytest

from nuh import file_operands, taxonomy
from nuh.bash import classify_command


@pytest.mark.parametrize("template", [
    "sed -i 's/a/b/' @ ./input",
    "sed -i x @ ./input",
    "sed --in-place 's/a/b/' @ ./input",
    "sed --in-place=.bak 's/a/b/' @ ./input",
    "sed -i.bak 's/a/b/' @ ./input",
    "sed -iSUFFIX 's/a/b/' @ ./input",
    "sed -ni 's/a/b/p' @ ./input",
    "sed -i -e 's/a/b/' @ ./input",
    "sed -i -f ./script @ ./input",
    "sed -i @ -e 's/a/b/' ./input",
    "sed -i @ --expression='s/a/b/' ./input",
    "sort -o @ ./input",
    "sort --output=@ ./input",
    "sort -o@ ./input",
    "sort -T @ ./input",
    "diff --output=@ a b",
    "uniq ./input @",
    "uniq - @",
    "tree -o @ .",
    "tar -cf @ ./input",
    "tar cf @ ./input",
    "tar cfv @ ./input",
    "tar cvfz @ ./input",
    "tar -c -Vtext -f @ ./input",
    "tar -rf @ ./input",
    "tar -uf @ ./input",
    "tar --create --file=@ ./input",
    "tar --append --file=@ ./input",
    "tar --update --file=@ ./input",
    "tar -C @ -xf ./archive.tar",
    "tar -xf ./archive.tar -C @ ./member",
    "tar -xf ./archive.tar @ ./member",
    "patch @ ./change.patch",
    "patch -o @ ./input ./change.patch",
    "patch --output=@ ./input ./change.patch",
    "patch -d @ ./input ./change.patch",
    "patch --directory=@ ./input ./change.patch",
    "patch -r @ ./input ./change.patch",
    "patch -D NAME @ ./change.patch",
    "patch -V simple @ ./change.patch",
    "mkfifo @ ./fifo",
    "mknod @ p",
    "mknod @ c 1 3",
    "awk -i inplace '{print}' @ ./input",
    "gawk --include=inplace '{print}' @ ./input",
    "truncate -s 0 @ ./input",
    "dd of=@ if=./input",
    "install -t @ ./input",
    "install --target-directory=@ ./input",
    "cp --target-directory=@ ./input",
    "mv --target-directory=@ ./input",
    "ln -f ./input @",
    "tee -a @ ./input",
    "wget -O @ http://localhost/input",
    "wget --output-document=@ http://localhost/input",
    "curl -o @ http://localhost/input",
    "curl --output=@ http://localhost/input",
    "curl --cookie-jar=@ http://localhost/input",
    "curl -so@ http://localhost/input",
    "gzip @ ./input",
    "gunzip @ ./input.gz",
    "xz @ ./input",
    "xz -d @ ./input.xz",
    "bzip2 @ ./input",
    "bzip2 -d @ ./input.bz2",
    "zip @ ./input",
    "zip ./original.zip --out @ ./input",
    "zip ./original.zip --output-file=@ ./input",
    "zip ./original.zip -O=@ ./input",
    "zip ./original.zip -b=@ ./input",
    "rsync -a localhost:input @",
    "rsync -a ./input @",
    "scp localhost:input @",
    "scp ./input @",
    "touch -r ./reference @ ./input",
])
@pytest.mark.parametrize("outside", [True, False], ids=["outside-asks", "inside-allows"])
def test_written_files_obey_boundary(env: dict, template: str, outside: bool) -> None:
    command = template.replace("@", "~/outside" if outside else "./outside")
    result = classify_command(command)
    assert result.final_decision == (taxonomy.ASK if outside else taxonomy.ALLOW), result
    if outside:
        assert "outside project" in result.reason, result


@pytest.mark.parametrize("command", [
    "ln -f ~/outside ./inside",
    "ln -s ./input ~/outside ./inside",
    "cp --target-directory=./out ~/outside",
    "install -t ./out ~/outside",
    "touch -r ~/outside ./inside",
    "truncate -r ~/outside -s 0 ./inside",
    "sort -o ./out ~/outside",
    "uniq ~/outside ./out",
    "tree -o ./out ~/outside",
    "tar -cf ./out.tar ~/outside",
    "tar -xf ~/outside",
    "patch ./input ~/outside",
    "split ~/outside",
    "csplit ~/outside /pattern/",
    "dd if=~/outside of=./out",
    "zip ./out.zip ~/outside",
    "unzip -l ~/outside",
    "sed 's/a/b/' ~/outside",
    "sort ~/outside",
    "diff ~/outside ./input",
    "uniq ~/outside",
    "tree ~/outside",
    "gzip -c ~/outside",
    "gunzip -t ~/outside",
    "xz --stdout ~/outside",
    "bzip2 -t ~/outside",
    "curl -o - http://localhost/input",
    "wget -O - http://localhost/input",
    "dd if=~/outside",
])
def test_read_inputs_and_stdout_are_not_writes(env: dict, command: str) -> None:
    result = classify_command(command)
    assert result.final_decision == taxonomy.ALLOW, result


@pytest.mark.parametrize("command", [
    "sort --output=$HOME/outside ./input",
    "diff --output=$HOME/outside a b",
    "tree -o ${HOME}/outside .",
])
def test_output_options_expand_home_variables(env: dict, command: str) -> None:
    result = classify_command(command)
    assert result.final_decision == taxonomy.ASK
    assert "outside project" in result.reason


@pytest.mark.parametrize("command", [
    "sed -i'/opt/backup/*' 's/a/b/' ./input",
    "sed --in-place=/opt/backup 's/a/b/' ./input",
    "sed --in-p 's/a/b/' ~/outside ./input",
    "sort --out=~/outside ./input",
    "sort --compress-program=evil -o ./out ./input",
    "awk -i unknown '{print}' ./input",
    "awk -i inplace 'BEGIN {system(\"echo hello\")}' ./input",
    "awk -i inplace -e'BEGIN {system(\"echo hello\")}' ./input",
    "awk -i inplace --source='BEGIN {system(\"echo hello\")}' ./input",
    "awk -i inplace 'BEGIN {system (\"echo hello\")}' ./input",
    "gawk -i inplace 'BEGIN {print \"x\" > \"/opt/external\"} {print}' -e harmless ./input",
    "awk -i inplace -f ./program ./input",
    "awk -i inplace -p~/outside '{print}' ./input",
    "perl -pi -e 's/a/b/' ~/outside ./input",
    "perl -i.bak -pe 's/a/b/' ./input",
    "split --filter='cat > ~/outside' ./input ./part",
    "tar -cf ./out.tar --files-from ./list",
    "tar -cf ./out.tar --remove-files ~/outside",
    "tar -xf ./in.tar --transform s/a/b/",
    "tar -xf ./in.tar --direc=~/outside",
    "tar -cf ./out.tar --checkpoint-act=exec=evil ./input",
    "patch -d ./out -i ./change.patch",
    "patch -r ./reject -i ./change.patch",
    "patch -B ~/backup ./input ./change.patch",
    "tree -L 1 -R ./input",
    "zip -m ./out.zip ~/outside",
    "zip ./out.zip ./input --logfile-path=~/outside",
    "unzip -: ./input.zip -d ./out",
    "gzip -N ./input.gz",
    "xz --files=./list",
    "unxz --files=./list",
    "xz -Flzma ~/outside",
    "unxz -F lzma ./input",
    "gunzip -z ./input",
    "curl --config ./options http://localhost/input",
    "curl -w '%output{/opt/out}data' http://localhost/input",
    "wget -i ./urls",
    "wget --save-cookies=~/outside -O ./out http://localhost/input",
])
def test_indirect_or_executable_write_modes_ask(env: dict, command: str) -> None:
    assert classify_command(command).final_decision == taxonomy.ASK


@pytest.mark.parametrize("command", [
    "curl -o ./out https://unknown.invalid/input",
    "wget -O ./out https://unknown.invalid/input",
    "rsync other:input ./out",
    "scp other:input ./out",
    "curl -X POST -o ./out http://localhost/input",
])
def test_local_outputs_do_not_bypass_host_policy(env: dict, command: str) -> None:
    assert classify_command(command).final_decision == taxonomy.ASK


def test_tar_extraction_uses_cwd_not_archive(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(env["home"])
    assert classify_command("tar -xf ./archive.tar").final_decision == taxonomy.ASK


@pytest.mark.parametrize("command", ["wget http://localhost/input", "dd of=-", "mkfifo -", "mknod - p"])
def test_implicit_or_literal_output_outside_cwd_asks(
    env: dict, monkeypatch: pytest.MonkeyPatch, command: str,
) -> None:
    monkeypatch.chdir(env["home"])
    assert classify_command(command).final_decision == taxonomy.ASK


def test_tar_explicit_directory_can_enter_project(
    env: dict, project_root: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(env["home"])
    result = classify_command(f"tar -xf ./archive.tar -C {project_root}")
    assert result.final_decision == taxonomy.ALLOW


def test_tar_directory_changes_accumulate(env: dict) -> None:
    result = classify_command("tar -xf ./archive.tar -C ./out -C ../../escape ./member")
    assert result.final_decision == taxonomy.ASK


def test_patch_relative_output_is_rebased(env: dict) -> None:
    result = classify_command("patch -d ./out -o ../../escape ./input ./change.patch")
    assert result.final_decision == taxonomy.ASK


def test_rsync_source_removal_checks_sources(env: dict) -> None:
    result = classify_command("rsync --remove-source-files ~/outside localhost:output")
    assert result.final_decision == taxonomy.ASK


@pytest.mark.parametrize("command", ["gzip ./input", "xz ./input", "bzip2 ./input", "zip ./input ./source"])
def test_generated_output_symlink_cannot_escape(env: dict, command: str) -> None:
    suffix = {"gzip": ".gz", "xz": ".xz", "bzip2": ".bz2", "zip": ".zip"}[command.split()[0]]
    os.symlink(os.path.join(env["home"], "outside"), "input" + suffix)
    assert classify_command(command).final_decision == taxonomy.ASK


@pytest.mark.parametrize("tokens,expected", [
    (["sed", "-iSUFFIX", "x", "a", "b"], ["a", "b"]),
    (["sed", "-i", "-e", "x", "a", "b"], ["a", "b"]),
    (["awk", "-i", "inplace", "{print}", "a", "b"], ["a", "b"]),
    (["uniq", "-", "out"], ["out"]),
    (["mknod", "fifo", "p"], ["fifo"]),
    (["dd", "of=out", "if=in"], ["out"]),
    (["tar", "cf", "out.tar", "in"], ["out.tar"]),
    (["tar", "xf", "in.tar"], ["."]),
    (["patch", "-d", "dir", "file", "input.patch"], ["dir", "dir/file"]),
    (["scp", "host:in", "out"], ["out"]),
    (["scp", "in", "host:out"], []),
])
def test_mode_selectors(tokens: list[str], expected: list[str]) -> None:
    assert file_operands.modified_files(tokens) == expected


@pytest.mark.parametrize("prefix", ["/opt/outside", "./part"])
@pytest.mark.parametrize("cluster", ["-dl", "-edl"])
def test_split_boolean_cluster_keeps_output_prefix(env: dict, prefix: str, cluster: str) -> None:
    tokens = ["split", cluster, "10", "README.md", prefix]
    assert file_operands.modified_files(tokens) == [prefix]
    assert classify_command(" ".join(tokens)).final_decision == taxonomy.ASK
    assert classify_command("split -dl 10 README.md").final_decision == taxonomy.ALLOW


@pytest.mark.parametrize("template", [
    "split ./input @",
    "split -l 10 ./input @",
    "split -d ./input @",
    "split -e ./input @",
    "csplit -f @ ./input /pattern/",
    "csplit --prefix=@ ./input /pattern/",
    "unzip -d @ ./input.zip",
    "unzip ./input.zip -d@",
    "unzip -Outf8 ./input.zip -d @",
    "unzip -Iutf8 ./input.zip -d @",
])
@pytest.mark.parametrize("target", ["~/outside", "./inside"])
def test_uncertain_output_templates_ask(env: dict, template: str, target: str) -> None:
    # Prefix families and archive members cannot prove the outputs.
    assert classify_command(template.replace("@", target)).final_decision == taxonomy.ASK


@pytest.mark.parametrize("command", [
    "split -a 1 -n 1 README.md ./id_rs",
    "split -d -a 1 README.md ./part --additional-suffix=a",
    "split -x README.md ./part",
    "split README.md x",
    "split README.md /opt/outside",
    "csplit -f ./id_rs -b a -n 1 README.md 1",
    "csplit --prefix=./part --suffix-format=%01d --digits=1 README.md 1",
    "csplit -f xx README.md 1",
    "csplit -f /opt/outside README.md 1",
])
def test_explicit_split_prefixes_ask(env: dict, command: str) -> None:
    assert classify_command(command).final_decision == taxonomy.ASK


@pytest.mark.parametrize("command", ["split README.md", "split -d README.md", "csplit README.md 1"])
@pytest.mark.parametrize("outside", [False, True])
def test_default_split_prefix_uses_cwd(
    env: dict, monkeypatch: pytest.MonkeyPatch, command: str, outside: bool,
) -> None:
    if outside:
        monkeypatch.chdir(env["home"])
    assert classify_command(command).final_decision == (taxonomy.ASK if outside else taxonomy.ALLOW)


@pytest.mark.parametrize("tool", ["gzip", "gunzip", "xz", "unxz", "bzip2", "bunzip2", "zstd"])
@pytest.mark.parametrize("flag", ["-r", "--recursive"])
@pytest.mark.parametrize("directory", ["./src", "~/outside"])
def test_recursive_compressors_ask(env: dict, tool: str, flag: str, directory: str) -> None:
    assert classify_command(f"{tool} {flag} {directory}").final_decision == taxonomy.ASK


@pytest.mark.parametrize("command", ["gzip -fr ./src", "gunzip -kr ./src", "gzip -rc ./src"])
def test_recursive_compressor_clusters_ask(env: dict, command: str) -> None:
    assert classify_command(command).final_decision == taxonomy.ASK


@pytest.mark.parametrize("tool", ["gzip", "gunzip", "xz", "unxz", "bzip2", "bunzip2"])
def test_nonrecursive_compressors_keep_project_boundary(env: dict, tool: str) -> None:
    assert classify_command(f"{tool} ./input").final_decision == taxonomy.ALLOW
    assert classify_command(f"{tool} ~/outside").final_decision == taxonomy.ASK


@pytest.mark.parametrize("destination", ["./unpacked", "/opt/outside"])
def test_unzip_extraction_asks(env: dict, destination: str) -> None:
    assert classify_command(f"unzip -o ./archive.zip -d {destination}").final_decision == taxonomy.ASK


@pytest.mark.parametrize("flag", ["-l", "-t", "-Z", "-p", "-c", "-z", "-v"])
def test_unzip_read_modes_stay_reads(env: dict, flag: str) -> None:
    assert classify_command(f"unzip {flag} ./archive.zip").final_decision == taxonomy.ALLOW
    assert classify_command(f"unzip {flag} /opt/archive.zip").final_decision == taxonomy.ALLOW


@pytest.mark.parametrize("operand", ["nonsense=1", "nonsense=./input", "nonsense=/opt/outside", "input"])
@pytest.mark.parametrize("files", ["", "if=./input of=./out", "if=/opt/outside of=./out"])
def test_dd_unknown_operands_ask(env: dict, operand: str, files: str) -> None:
    assert classify_command(f"dd {files} {operand}").final_decision == taxonomy.ASK


@pytest.mark.parametrize("command", [
    "POSIXLY_CORRECT=1 touch ./inside -d /dev/sda",
    "POSIXLY_CORRECT=1 touch -r /dev/sda ./inside -d /dev/sda",
    "POSIXLY_CORRECT=1 touch -r/dev/sda ./inside -d /dev/sda",
])
def test_unattributed_operand_keeps_catastrophic_check(env: dict, command: str) -> None:
    assert classify_command(command).final_decision == taxonomy.BLOCK


@pytest.mark.parametrize("source", ["./input", "/opt/outside"])
def test_dd_documented_operands_keep_reads(env: dict, source: str) -> None:
    command = (f"dd if={source} ibs=1 obs=1 bs=1 cbs=1 skip=0 seek=0 count=1 "
               "conv=notrunc iflag=fullblock oflag=sync status=none")
    assert classify_command(command).final_decision == taxonomy.ALLOW
    assert classify_command(command + " of=./out").final_decision == taxonomy.ALLOW


@pytest.mark.parametrize("template", [
    "touch ./inside -d @",
    "touch ./inside -r @",
    "truncate -s 1 ./inside -r @",
    "cp ./input ./out -S @",
    "install @ ./x -d",
])
@pytest.mark.parametrize("path", ["./reference", "/opt/outside"])
def test_late_writer_option_values_ask(env: dict, template: str, path: str) -> None:
    # Both argv readings are bounded: project-only filenames can be allowed.
    expected = taxonomy.ALLOW if path == "./reference" else taxonomy.ASK
    assert classify_command("POSIXLY_CORRECT=1 " + template.replace("@", path)).final_decision == expected


@pytest.mark.parametrize("command", [
    "touch -d now ./inside",
    "touch -r ./reference ./inside",
    "truncate -r ./reference -s 1 ./inside",
    "cp -S suffix ./input ./out",
])
def test_writer_options_before_operands_keep_allow(env: dict, command: str) -> None:
    assert classify_command(command).final_decision == taxonomy.ALLOW
