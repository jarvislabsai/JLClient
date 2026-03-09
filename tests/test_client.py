"""Unit tests for client.py — covers branches unreachable in E2E tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from jarvislabs.client import (
    Filesystems,
    Instances,
    Scripts,
    SSHKeys,
    _fetch_instances,
    _get_instance,
    _normalize_success,
    _poll_until_running,
    _preflight_vm,
    _region_url,
    _resolve_region,
    _validate_europe,
)
from jarvislabs.constants import (
    DEFAULT_REGION,
    EUROPE_MIN_STORAGE_GB,
    EUROPE_REGION,
    REGION_URLS,
)
from jarvislabs.exceptions import APIError, NotFoundError, ValidationError
from jarvislabs.models import SSHKey, StatusResponse

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_instances(mock_transport):
    return Instances(mock_transport, SSHKeys(mock_transport))


def _make_scripts(mock_transport):
    return Scripts(mock_transport)


def _make_filesystems(mock_transport):
    return Filesystems(mock_transport)


def _mock_existing_instance():
    m = MagicMock(
        machine_id=10,
        gpu_type="RTX5000",
        num_gpus=1,
        storage_gb=40,
        is_reserved=True,
        fs_id=None,
        template="pytorch",
        region="india-01",
        status="Paused",
    )
    # MagicMock.name is a special descriptor — must set via configure_mock
    m.configure_mock(name="old-name")
    return m


_INST_RESP = {
    "success": True,
    "instance": {"machine_id": 42, "status": "Running", "template": "pytorch"},
}

_DUMMY_KEY = SSHKey(ssh_key="ssh-ed25519 AAA", key_name="test", key_id="k1")


# ── _normalize_success ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"success": True}, True),
        ({"success": False}, False),
        ({"success": "True"}, True),
        ({"success": "true"}, True),
        ({"success": "False"}, False),
        ({"success": "false"}, False),
        ({"sucess": True}, True),
        ({"sucess": "True"}, True),
        ({}, False),
        ({"success": 0}, False),
        ({"success": 1}, True),
        ({"other_key": True}, False),
    ],
    ids=[
        "bool-true",
        "bool-false",
        "str-True",
        "str-true",
        "str-False",
        "str-false",
        "typo-bool",
        "typo-str",
        "empty",
        "int-zero",
        "int-one",
        "wrong-key",
    ],
)
def test_normalize_success(data, expected):
    assert _normalize_success(data) is expected


# ── _validate_europe ─────────────────────────────────────────────────────────


class TestValidateEurope:
    def test_valid_h100(self):
        _validate_europe("H100", 1, 100)

    def test_valid_h200_8gpu(self):
        _validate_europe("H200", 8, 200)

    def test_invalid_gpu(self):
        with pytest.raises(ValidationError, match="supports only"):
            _validate_europe("RTX5000", 1, 100)

    def test_invalid_count_2(self):
        with pytest.raises(ValidationError, match="GPUs per instance"):
            _validate_europe("H100", 2, 100)

    def test_invalid_count_4(self):
        with pytest.raises(ValidationError, match="GPUs per instance"):
            _validate_europe("H100", 4, 100)

    def test_storage_too_low(self):
        with pytest.raises(ValidationError, match="at least"):
            _validate_europe("H100", 1, 50)


# ── _preflight_vm ────────────────────────────────────────────────────────────


class TestPreflightVm:
    def test_valid(self):
        _preflight_vm("H100", EUROPE_REGION, [_DUMMY_KEY])

    def test_bad_gpu(self):
        with pytest.raises(ValidationError, match="H100/H200"):
            _preflight_vm("L4", EUROPE_REGION, [_DUMMY_KEY])

    def test_bad_region(self):
        with pytest.raises(ValidationError, match="only available in europe-01"):
            _preflight_vm("H100", "india-01", [_DUMMY_KEY])

    def test_empty_ssh_keys(self):
        with pytest.raises(ValidationError, match="SSH key"):
            _preflight_vm("H100", EUROPE_REGION, [])


# ── _region_url ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("region", "expected"),
    [
        ("india-01", REGION_URLS["india-01"]),
        ("india-noida-01", REGION_URLS["india-noida-01"]),
        ("europe-01", REGION_URLS["europe-01"]),
        ("unknown-region", REGION_URLS[DEFAULT_REGION]),
        (None, REGION_URLS[DEFAULT_REGION]),
    ],
    ids=["india", "noida", "europe", "unknown", "none"],
)
def test_region_url(region, expected):
    assert _region_url(region) == expected


# ── _resolve_region ──────────────────────────────────────────────────────────


class TestResolveRegion:
    def test_exception_fallback_europe_gpu(self, mock_transport):
        mock_transport.request.side_effect = Exception("network error")
        assert _resolve_region(mock_transport, gpu_type="H100", num_gpus=1) == EUROPE_REGION

    def test_exception_fallback_india_gpu(self, mock_transport):
        mock_transport.request.side_effect = Exception("network error")
        assert _resolve_region(mock_transport, gpu_type="RTX5000", num_gpus=1) == DEFAULT_REGION

    def test_empty_candidates(self, mock_transport):
        mock_transport.request.return_value = {"server_meta": []}
        assert _resolve_region(mock_transport, gpu_type="H100", num_gpus=1) == EUROPE_REGION

    def test_candidate_with_free_devices(self, mock_transport):
        mock_transport.request.return_value = {
            "server_meta": [
                {"gpu_type": "H100", "region": "europe-01", "num_free_devices": 3},
            ]
        }
        assert _resolve_region(mock_transport, gpu_type="H100", num_gpus=1) == "europe-01"

    def test_all_full_returns_first_candidate(self, mock_transport):
        mock_transport.request.return_value = {
            "server_meta": [
                {"gpu_type": "H100", "region": "europe-01", "num_free_devices": 0},
                {"gpu_type": "H100", "region": "europe-02", "num_free_devices": 0},
            ]
        }
        assert _resolve_region(mock_transport, gpu_type="H100", num_gpus=1) == "europe-01"

    def test_gpu_type_none(self, mock_transport):
        mock_transport.request.return_value = {
            "server_meta": [
                {"gpu_type": "H100", "region": "europe-01", "num_free_devices": 5},
            ]
        }
        assert _resolve_region(mock_transport, gpu_type=None, num_gpus=1) == DEFAULT_REGION


# ── _poll_until_running ──────────────────────────────────────────────────────


class TestPollUntilRunning:
    def test_running_immediately(self, mock_transport):
        mock_transport.request.return_value = {"status": "Running", "error": None, "code": None}
        _poll_until_running(mock_transport, machine_id=1, region="india-01")
        assert mock_transport.request.call_count == 1

    def test_status_none_strings_are_coerced(self):
        status = StatusResponse(status="Running", error="None", code="None")
        assert status.error is None
        assert status.code is None

    def test_404_then_running(self, mock_transport):
        mock_transport.request.side_effect = [
            NotFoundError("not found"),
            {"status": "Running", "error": None, "code": None},
        ]
        with patch("jarvislabs.client.time.sleep"):
            _poll_until_running(mock_transport, machine_id=1, region="india-01")
        assert mock_transport.request.call_count == 2

    def test_failed_raises(self, mock_transport):
        mock_transport.request.return_value = {"status": "Failed", "error": "out of memory", "code": 500}
        with pytest.raises(APIError, match="creation failed"):
            _poll_until_running(mock_transport, machine_id=1, region="india-01")

    def test_timeout_raises(self, mock_transport):
        mock_transport.request.return_value = {"status": "Creating", "error": None, "code": None}
        with (
            patch("jarvislabs.client.time.monotonic", side_effect=[0, 100, 200]),
            patch("jarvislabs.client.time.sleep"),
            pytest.raises(APIError, match=r"Timed out.*jl instance get 1"),
        ):
            _poll_until_running(mock_transport, machine_id=1, region="india-01")

    def test_europe_uses_300s_timeout(self, mock_transport):
        mock_transport.request.return_value = {"status": "Creating", "error": None, "code": None}
        with (
            patch("jarvislabs.client.time.monotonic", side_effect=[0, 250, 301]),
            patch("jarvislabs.client.time.sleep"),
            pytest.raises(APIError, match=r"Timed out.*jl instance get 1"),
        ):
            _poll_until_running(mock_transport, machine_id=1, region=EUROPE_REGION)


# ── _fetch_instances ─────────────────────────────────────────────────────────


class TestFetchInstances:
    def test_normal(self, mock_transport):
        mock_transport.request.return_value = {
            "success": True,
            "instances": [
                {"machine_id": 1, "status": "Running", "template": "pytorch"},
                {"machine_id": 2, "status": "Paused", "template": "fastai"},
            ],
        }
        result = _fetch_instances(mock_transport)
        assert len(result) == 2
        assert result[0].machine_id == 1
        assert result[1].status == "Paused"

    def test_empty(self, mock_transport):
        mock_transport.request.return_value = {"success": True, "instances": []}
        assert _fetch_instances(mock_transport) == []


# ── _get_instance ────────────────────────────────────────────────────────────


class TestGetInstance:
    def test_success(self, mock_transport):
        mock_transport.request.return_value = _INST_RESP
        assert _get_instance(mock_transport, 42).machine_id == 42

    def test_not_found(self, mock_transport):
        mock_transport.request.return_value = {"success": False}
        with pytest.raises(NotFoundError):
            _get_instance(mock_transport, 99)

    def test_retries_success_on_second(self, mock_transport):
        mock_transport.request.side_effect = [NotFoundError("lag"), _INST_RESP]
        with patch("jarvislabs.client.time.sleep"):
            assert _get_instance(mock_transport, 42, retries=2).machine_id == 42

    def test_retries_all_fail(self, mock_transport):
        mock_transport.request.side_effect = NotFoundError("gone")
        with patch("jarvislabs.client.time.sleep"), pytest.raises(NotFoundError):
            _get_instance(mock_transport, 42, retries=2)
        assert mock_transport.request.call_count == 3


# ── Scripts ───────────────────────────────────────────────────────────────────


class TestScripts:
    def test_list_parses_script_meta(self, mock_transport):
        mock_transport.request.return_value = {
            "success": True,
            "script_meta": [{"script_id": 11, "script_name": "bootstrap"}],
        }

        scripts = _make_scripts(mock_transport).list()
        assert len(scripts) == 1
        assert scripts[0].script_id == 11
        assert scripts[0].script_name == "bootstrap"

    def test_list_handles_empty(self, mock_transport):
        mock_transport.request.return_value = {"success": True, "script_meta": []}
        scripts = _make_scripts(mock_transport).list()
        assert scripts == []

    def test_add_uses_multipart_and_query_params(self, mock_transport):
        mock_transport.request.return_value = {"message": "Script added successfully."}
        _make_scripts(mock_transport).add(b"echo hi", name="init-script")

        kwargs = mock_transport.request.call_args.kwargs
        assert kwargs["params"] == {"name": "init-script"}
        filename, content, mime = kwargs["files"]["script"]
        assert filename == "startup.sh"
        assert content == b"echo hi"
        assert mime == "application/x-sh"

    def test_update_uses_multipart_and_script_id(self, mock_transport):
        mock_transport.request.return_value = {"message": "Script added successfully."}
        _make_scripts(mock_transport).update(7, b"echo updated")

        kwargs = mock_transport.request.call_args.kwargs
        assert kwargs["params"] == {"script_id": 7}
        filename, content, mime = kwargs["files"]["script"]
        assert filename == "startup.sh"
        assert content == b"echo updated"
        assert mime == "application/x-sh"

    def test_remove_uses_delete_query(self, mock_transport):
        mock_transport.request.return_value = {"message": "Script deleted successfully."}
        _make_scripts(mock_transport).remove(9)

        mock_transport.request.assert_called_with("DELETE", "scripts/", params={"script_id": 9})

    def test_add_rejects_empty_script(self, mock_transport):
        with pytest.raises(ValidationError, match="cannot be empty"):
            _make_scripts(mock_transport).add(b"   \n")


class TestFilesystems:
    def test_list_parses_filesystems(self, mock_transport):
        mock_transport.request.return_value = [
            {"fs_id": 1, "fs_name": "data", "storage": 100},
            {"fs_id": 2, "fs_name": "models", "storage": 200},
        ]

        items = _make_filesystems(mock_transport).list()
        assert len(items) == 2
        assert items[0].fs_id == 1
        assert items[0].fs_name == "data"
        assert items[0].storage == 100

    def test_create_uses_expected_payload(self, mock_transport):
        mock_transport.request.return_value = {"fs_id": 15}
        fs_id = _make_filesystems(mock_transport).create("data", 120)

        assert fs_id == 15
        mock_transport.request.assert_called_with(
            "POST",
            "filesystem/create",
            json={"fs_name": "data", "storage": 120},
        )

    def test_create_validates_inputs(self, mock_transport):
        with pytest.raises(ValidationError, match="cannot be empty"):
            _make_filesystems(mock_transport).create("", 100)
        with pytest.raises(ValidationError, match="30 characters or fewer"):
            _make_filesystems(mock_transport).create("x" * 31, 100)
        with pytest.raises(ValidationError, match="between 50GB and 2048GB"):
            _make_filesystems(mock_transport).create("data", 49)

    def test_edit_uses_expected_payload(self, mock_transport):
        mock_transport.request.return_value = {"message": "Filesystem updated successfully", "fs_id": 21}
        fs_id = _make_filesystems(mock_transport).edit(7, 180)

        assert fs_id == 21
        mock_transport.request.assert_called_with(
            "POST",
            "filesystem/edit",
            json={"fs_id": 7, "storage": 180},
        )

    def test_remove_uses_expected_query(self, mock_transport):
        mock_transport.request.return_value = {"success": True}
        ok = _make_filesystems(mock_transport).remove(9)
        assert ok is True
        mock_transport.request.assert_called_with("POST", "filesystem/delete", params={"fs_id": 9})

    def test_remove_raises_when_backend_reports_failure(self, mock_transport):
        mock_transport.request.return_value = {"success": False, "error": "busy"}
        with pytest.raises(APIError, match="Failed to remove filesystem"):
            _make_filesystems(mock_transport).remove(9)


# ── create() payload ─────────────────────────────────────────────────────────


class TestCreatePayload:
    @patch("jarvislabs.client._get_instance")
    @patch("jarvislabs.client._poll_until_running")
    def test_all_params(self, _poll, mock_get, mock_transport):
        mock_transport.request.side_effect = [
            {"server_meta": []},
            [{"fs_id": 7}],
            {"machine_id": 1},
        ]
        mock_get.return_value = MagicMock(machine_id=1)

        _make_instances(mock_transport).create(
            gpu_type="RTX5000",
            num_gpus=1,
            template="pytorch",
            storage=50,
            name="test",
            disk_type="ssd",
            http_ports="8080,9090",
            script_id="s1",
            script_args="--flag",
            fs_id=7,
            arguments="--arg",
        )

        payload = mock_transport.request.call_args.kwargs["json"]
        assert payload["gpu_type"] == "RTX5000"
        assert payload["hdd"] == 50
        assert payload["is_reserved"] is True
        assert payload["disk_type"] == "ssd"
        assert payload["http_ports"] == "8080,9090"
        assert payload["script_id"] == "s1"
        assert payload["script_args"] == "--flag"
        assert payload["fs_id"] == 7
        assert payload["arguments"] == "--arg"

    @patch("jarvislabs.client._resolve_region", return_value="india-01")
    def test_invalid_fs_id_raises(self, _region, mock_transport):
        mock_transport.request.return_value = [{"fs_id": 7}]

        with pytest.raises(ValidationError, match="Filesystem 999 not found"):
            _make_instances(mock_transport).create(gpu_type="RTX5000", fs_id=999)

        mock_transport.request.assert_called_once_with("GET", "filesystem/list")

    @patch("jarvislabs.client._get_instance")
    @patch("jarvislabs.client._poll_until_running")
    def test_europe_auto_bumps_storage(self, _poll, mock_get, mock_transport):
        mock_transport.request.return_value = {"machine_id": 1}
        mock_get.return_value = MagicMock(machine_id=1)

        _make_instances(mock_transport).create(gpu_type="H100", storage=20)
        assert mock_transport.request.call_args.kwargs["json"]["hdd"] >= EUROPE_MIN_STORAGE_GB


# ── resume() payload ─────────────────────────────────────────────────────────


class TestResumePayload:
    def _setup_resume(self, mock_transport):
        existing = _mock_existing_instance()
        mock_get = patch("jarvislabs.client._get_instance").start()
        patch("jarvislabs.client._poll_until_running").start()
        mock_get.side_effect = [existing, MagicMock(machine_id=11)]
        mock_transport.request.return_value = {"machine_id": 11}
        return _make_instances(mock_transport)

    def teardown_method(self):
        patch.stopall()

    def test_user_params_override(self, mock_transport):
        instances = self._setup_resume(mock_transport)
        instances.resume(10, gpu_type="A100", num_gpus=2, storage=100, name="new-name")

        payload = mock_transport.request.call_args.kwargs["json"]
        assert payload["gpu_type"] == "A100"
        assert payload["num_gpus"] == 2
        assert payload["hdd"] == 100
        assert payload["name"] == "new-name"

    def test_falls_back_to_instance_values(self, mock_transport):
        instances = self._setup_resume(mock_transport)
        instances.resume(10)

        payload = mock_transport.request.call_args.kwargs["json"]
        assert payload["gpu_type"] == "RTX5000"
        assert payload["num_gpus"] == 1
        assert payload["hdd"] == 40
        assert payload["name"] == "old-name"
        assert payload["is_reserved"] is True

    def test_script_args_defaults_to_empty(self, mock_transport):
        instances = self._setup_resume(mock_transport)
        instances.resume(10)
        assert mock_transport.request.call_args.kwargs["json"]["script_args"] == ""

    def test_resume_non_paused_raises(self, mock_transport):
        mock_get = patch("jarvislabs.client._get_instance").start()
        running = MagicMock(status="Running")
        mock_get.return_value = running
        with pytest.raises(ValidationError, match="Paused"):
            _make_instances(mock_transport).resume(10)

    def test_invalid_fs_id_raises(self, mock_transport):
        instances = self._setup_resume(mock_transport)
        mock_transport.request.side_effect = [[{"fs_id": 7}]]

        with pytest.raises(ValidationError, match="Filesystem 999 not found"):
            instances.resume(10, fs_id=999)

        mock_transport.request.assert_called_once_with("GET", "filesystem/list")


class TestRenameInstance:
    def test_rename_calls_machine_name_endpoint(self, mock_transport):
        mock_transport.request.return_value = {"success": True}
        _make_instances(mock_transport).rename(42, "renamed")

        mock_transport.request.assert_called_with(
            "PUT",
            "machines/machine_name",
            params={"machine_id": 42, "machine_name": "renamed"},
        )

    def test_rename_rejects_empty_name(self, mock_transport):
        with pytest.raises(ValidationError, match="cannot be empty"):
            _make_instances(mock_transport).rename(42, "  ")

    def test_rename_rejects_long_name(self, mock_transport):
        with pytest.raises(ValidationError, match="40 characters or fewer"):
            _make_instances(mock_transport).rename(42, "x" * 41)
