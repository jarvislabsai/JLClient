from __future__ import annotations

import pytest

from jarvislabs.exceptions import SSHError, ValidationError
from jarvislabs.ssh import SSHInfo, build_remote_shell_command, build_scp_command, parse_ssh_command, split_ssh_command


def test_split_ssh_command_valid():
    parts = split_ssh_command("ssh root@example.com -p 2222")
    assert parts == ["ssh", "root@example.com", "-p", "2222"]


@pytest.mark.parametrize("value", ["", "scp root@example.com:/tmp/file .", "root@example.com -p 2222"])
def test_split_ssh_command_rejects_non_ssh(value):
    with pytest.raises(SSHError, match="Cannot parse SSH command"):
        split_ssh_command(value)


def test_split_ssh_command_rejects_malformed_quotes():
    with pytest.raises(SSHError, match="Cannot parse SSH command"):
        split_ssh_command("ssh -o 'StrictHostKeyChecking=no")


def test_parse_ssh_command_with_user_host_and_port():
    info = parse_ssh_command("ssh root@example.com -p 2222")
    assert info == SSHInfo(user="root", host="example.com", port=2222)


def test_parse_ssh_command_with_backend_style_option_prefix():
    info = parse_ssh_command("ssh -o StrictHostKeyChecking=no -p 2222 root@example.com")
    assert info == SSHInfo(user="root", host="example.com", port=2222)


def test_parse_ssh_command_with_dash_l_user():
    info = parse_ssh_command("ssh -l ubuntu -p 2200 example.com")
    assert info == SSHInfo(user="ubuntu", host="example.com", port=2200)


def test_parse_ssh_command_defaults_to_root_and_22():
    info = parse_ssh_command("ssh example.com")
    assert info == SSHInfo(user="root", host="example.com", port=22)


def test_parse_ssh_command_rejects_missing_port_value():
    with pytest.raises(SSHError, match="Missing port"):
        parse_ssh_command("ssh root@example.com -p")


def test_parse_ssh_command_rejects_missing_option_value():
    with pytest.raises(SSHError, match="Missing SSH option value"):
        parse_ssh_command("ssh -o")


def test_build_remote_shell_command_quotes_command_and_prefixes():
    command = build_remote_shell_command(
        ["python", "train.py", "--name", "hello world"],
        cwd="/workspace/my project",
        env={"WANDB_MODE": "offline", "MODEL_NAME": "hello world"},
    )
    assert command == (
        "sh -lc 'cd '\"'\"'/workspace/my project'\"'\"' && export WANDB_MODE=offline && "
        "export MODEL_NAME='\"'\"'hello world'\"'\"' && python train.py --name '\"'\"'hello world'\"'\"''"
    )


def test_build_remote_shell_command_rejects_empty_command():
    with pytest.raises(ValidationError, match="command cannot be empty"):
        build_remote_shell_command([])


def test_build_remote_shell_command_rejects_invalid_env_name():
    with pytest.raises(ValidationError, match="Invalid environment variable name"):
        build_remote_shell_command(["python", "train.py"], env={"bad-key": "1"})


def test_build_scp_command_for_upload_preserves_ssh_options():
    command = build_scp_command(
        "ssh -o StrictHostKeyChecking=no -p 2222 root@example.com",
        source="/tmp/train.py",
        dest="~/train.py",
        upload=True,
    )
    assert command == [
        "scp",
        "-o",
        "StrictHostKeyChecking=no",
        "-P",
        "2222",
        "-o",
        "ConnectTimeout=15",
        "/tmp/train.py",
        "root@example.com:~/train.py",
    ]


def test_build_scp_command_for_download_recursive():
    command = build_scp_command(
        "ssh -o StrictHostKeyChecking=no -o ConnectTimeout=20 -p 2222 root@example.com",
        source="/root/output",
        dest="output",
        upload=False,
        recursive=True,
    )
    assert command == [
        "scp",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "ConnectTimeout=20",
        "-P",
        "2222",
        "-r",
        "root@example.com:/root/output",
        "output",
    ]
