"""SDK client — public Python API for JarvisLabs GPU cloud.

Usage:
    from jarvislabs import Client

    client = Client(api_key="...")
    instances = client.instances.list()
"""

from __future__ import annotations

import time

from jarvislabs.config import resolve_token
from jarvislabs.constants import (
    DEFAULT_POLL_TIMEOUT_S,
    DEFAULT_REGION,
    EUROPE_GPU_COUNTS,
    EUROPE_GPU_TYPES,
    EUROPE_MIN_STORAGE_GB,
    EUROPE_POLL_TIMEOUT_S,
    EUROPE_REGION,
    POLL_INTERVAL_S,
    REGION_URLS,
)
from jarvislabs.exceptions import APIError, AuthError, NotFoundError, ValidationError
from jarvislabs.models import (
    Balance,
    Instance,
    InstanceListResponse,
    ResourceMetrics,
    ServerMetaGPU,
    ServerMetaResponse,
    SSHKey,
    StatusResponse,
    Template,
    UserInfo,
)
from jarvislabs.transport import Transport


class Client:
    """Entry point for the JarvisLabs SDK."""

    def __init__(self, api_key: str | None = None) -> None:
        token = resolve_token(api_key)
        if not token:
            raise AuthError("No API key found. Set JL_API_KEY or run: jl login")
        self._transport = Transport(token)
        self.account = Account(self._transport)
        self.ssh_keys = SSHKeys(self._transport)
        self.instances = Instances(self._transport, self.ssh_keys)

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


# ── Account ──────────────────────────────────────────────────────────────────


class Account:
    """Balance, user info, and resource metrics."""

    def __init__(self, transport: Transport) -> None:
        self._t = transport

    def balance(self) -> Balance:
        resp = self._t.request("GET", "users/balance")
        return Balance(**resp)

    def user_info(self) -> UserInfo:
        resp = self._t.request("GET", "users/user_info")
        return UserInfo(**resp)

    def resource_metrics(self) -> ResourceMetrics:
        resp = self._t.request("GET", "misc/resource_metrics")
        return ResourceMetrics(**resp)

    def templates(self) -> list[Template]:
        resp = self._t.request("GET", "misc/frameworks")
        return [Template(**t) for t in resp.get("frameworks", [])]

    def gpu_availability(self) -> list[ServerMetaGPU]:
        """GPU types, pricing, and availability across regions."""
        resp = self._t.request("GET", "misc/server_meta")
        meta = ServerMetaResponse(**resp)
        return meta.server_meta


# ── SSH Keys ─────────────────────────────────────────────────────────────────


class SSHKeys:
    """Manage SSH keys."""

    def __init__(self, transport: Transport) -> None:
        self._t = transport

    def list(self) -> list[SSHKey]:
        resp = self._t.request("GET", "ssh/")
        return [SSHKey(**item) for item in resp]

    def add(self, ssh_key: str, key_name: str) -> bool:
        resp = self._t.request("POST", "ssh/", json={"ssh_key": ssh_key, "key_name": key_name})
        if not _normalize_success(resp):
            raise APIError(0, f"Failed to add SSH key: {resp}")
        return True

    def remove(self, key_id: str) -> bool:
        resp = self._t.request("DELETE", f"ssh/{key_id}")
        if not _normalize_success(resp):
            raise APIError(0, f"Failed to remove SSH key: {resp}")
        return True


# ── Instances ────────────────────────────────────────────────────────────────


class Instances:
    """Instance lifecycle: list, create, pause, resume, destroy."""

    def __init__(self, transport: Transport, ssh_keys: SSHKeys) -> None:
        self._t = transport
        self._ssh_keys = ssh_keys

    def list(self) -> list[Instance]:
        return _fetch_instances(self._t)

    def get(self, machine_id: int) -> Instance:
        return _get_instance(self._t, machine_id)

    def create(
        self,
        *,
        gpu_type: str | None = None,
        num_gpus: int = 1,
        template: str = "pytorch",
        storage: int = 40,
        name: str = "Name me",
        region: str | None = None,
        disk_type: str = "ssd",
        is_reserved: bool = True,
        http_ports: str = "",
        script_id: str | None = None,
        script_args: str = "",
        fs_id: int | None = None,
        arguments: str = "",
    ) -> Instance:
        # Resolve region if not explicitly provided
        if region is None:
            region = _resolve_region(self._t, gpu_type=gpu_type, num_gpus=num_gpus)

        # Auto-bump storage for Europe
        if region == EUROPE_REGION and storage < EUROPE_MIN_STORAGE_GB:
            storage = EUROPE_MIN_STORAGE_GB

        # Validation
        if not gpu_type:
            raise ValidationError("gpu_type is required (e.g. 'A100', 'H100', 'RTX5000')")
        if name and len(name) > 40:
            raise ValidationError("Instance name must be 40 characters or fewer")

        if region == EUROPE_REGION:
            _validate_europe(gpu_type, num_gpus, storage)

        if template == "vm":
            _preflight_vm(gpu_type, region, self._ssh_keys.list())

        # Build request payload
        payload: dict = {
            "gpu_type": gpu_type,
            "num_gpus": num_gpus,
            "hdd": storage,
            "region": region,
            "name": name,
            "is_reserved": is_reserved,
            "duration": "hour",
            "disk_type": disk_type,
            "http_ports": http_ports,
            "script_id": script_id,
            "script_args": script_args,
            "fs_id": fs_id,
            "arguments": arguments,
        }

        # Send create request to the resolved region
        base_url = _region_url(region)
        resp = self._t.request(
            "POST",
            f"templates/{template}/create",
            json=payload,
            base_url=base_url,
        )

        machine_id = resp.get("machine_id")
        if not machine_id:
            raise APIError(0, f"Create failed: {resp}")

        # Poll until running, then fetch full instance details
        _poll_until_running(self._t, machine_id, region)
        return _get_instance(self._t, machine_id)

    def pause(self, machine_id: int) -> bool:
        instance = _get_instance(self._t, machine_id)
        base_url = _region_url(instance.region)

        if instance.template == "vm":
            endpoint = "templates/vm/pause"
        else:
            endpoint = "misc/pause"

        resp = self._t.request(
            "POST",
            endpoint,
            params={"machine_id": machine_id},
            base_url=base_url,
        )
        if not _normalize_success(resp):
            raise APIError(0, f"Pause failed: {resp}")
        return True

    def resume(
        self,
        machine_id: int,
        *,
        gpu_type: str | None = None,
        num_gpus: int | None = None,
        storage: int | None = None,
        name: str | None = None,
        is_reserved: bool | None = None,
        script_id: str | None = None,
        script_args: str | None = None,
        fs_id: int | None = None,
    ) -> Instance:
        instance = _get_instance(self._t, machine_id)
        region = instance.region or DEFAULT_REGION
        base_url = _region_url(region)

        # Europe validation if changing GPU params
        effective_gpu = gpu_type or instance.gpu_type
        effective_num = num_gpus or instance.num_gpus or 1
        effective_storage = storage or instance.storage_gb or 40
        if region == EUROPE_REGION and effective_gpu:
            _validate_europe(effective_gpu, effective_num, effective_storage)

        payload: dict = {
            "machine_id": machine_id,
            "gpu_type": gpu_type or instance.gpu_type,
            "num_gpus": num_gpus or instance.num_gpus,
            "is_reserved": is_reserved if is_reserved is not None else instance.is_reserved,
            "hdd": storage or instance.storage_gb,
            "name": name or instance.name,
            "duration": "hour",
            "script_id": script_id,
            "script_args": script_args or "",
            "fs_id": fs_id or instance.fs_id,
            "arguments": "",
        }

        resp = self._t.request(
            "POST",
            f"templates/{instance.template}/resume",
            json=payload,
            base_url=base_url,
        )

        mid = resp.get("machine_id")
        if not mid:
            raise APIError(0, f"Resume failed: {resp}")

        _poll_until_running(self._t, mid, region)
        return _get_instance(self._t, mid)

    def destroy(self, machine_id: int) -> bool:
        instance = _get_instance(self._t, machine_id)
        base_url = _region_url(instance.region)

        if instance.template == "vm":
            endpoint = "templates/vm/destroy"
        else:
            endpoint = "misc/destroy"

        resp = self._t.request(
            "POST",
            endpoint,
            params={"machine_id": machine_id},
            base_url=base_url,
        )
        if not _normalize_success(resp):
            raise APIError(0, f"Destroy failed: {resp}")
        return True


# ── Helpers ──────────────────────────────────────────────────────────────────


def _normalize_success(data: dict) -> bool:
    """Handle "True" (str) vs True (bool) vs "sucess" typo."""
    val = data.get("success") or data.get("sucess")
    if isinstance(val, str):
        return val.lower() == "true"
    return bool(val)


def _resolve_region(transport: Transport, *, gpu_type: str | None, num_gpus: int) -> str:
    """Auto-route to the best region via server_meta. Hardcoded fallback if call fails."""
    fallback = EUROPE_REGION if gpu_type in EUROPE_GPU_TYPES else DEFAULT_REGION

    try:
        resp = transport.request("GET", "misc/server_meta")
        meta = ServerMetaResponse(**resp)
    except Exception:
        return fallback

    candidates = [s for s in meta.server_meta if s.gpu_type == gpu_type and s.region]
    if not candidates:
        return fallback

    for server in candidates:
        if server.num_free_devices >= num_gpus:
            return server.region

    return candidates[0].region or fallback


def _validate_europe(gpu_type: str, num_gpus: int, storage_gb: int) -> None:
    """Raise ValidationError if europe-01 constraints are violated."""
    if gpu_type not in EUROPE_GPU_TYPES:
        raise ValidationError(f"europe-01 supports only {sorted(EUROPE_GPU_TYPES)} GPUs")
    if num_gpus not in EUROPE_GPU_COUNTS:
        raise ValidationError(f"europe-01 requires num_gpus in {sorted(EUROPE_GPU_COUNTS)}")
    if storage_gb < EUROPE_MIN_STORAGE_GB:
        raise ValidationError(f"europe-01 requires at least {EUROPE_MIN_STORAGE_GB}GB storage")


def _preflight_vm(gpu_type: str, region: str, ssh_keys: list[SSHKey]) -> None:
    """Validate VM create constraints."""
    if gpu_type not in EUROPE_GPU_TYPES:
        raise ValidationError("VM template supports only H100/H200 GPUs")
    if region != EUROPE_REGION:
        raise ValidationError("VM template is only supported in europe-01")
    if not ssh_keys:
        raise ValidationError(
            "VM instances require at least one SSH key. Add one with: jl ssh-key add <pubkey-file> --name 'my-key'"
        )


def _region_url(region: str | None) -> str:
    """Get the backend URL for a region, falling back to default."""
    return REGION_URLS.get(region or DEFAULT_REGION, REGION_URLS[DEFAULT_REGION])


def _poll_until_running(transport: Transport, machine_id: int, region: str) -> None:
    """Poll /misc/status until Running or Failed. Raises on failure/timeout."""
    timeout = EUROPE_POLL_TIMEOUT_S if region == EUROPE_REGION else DEFAULT_POLL_TIMEOUT_S
    base_url = _region_url(region)
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        try:
            resp = transport.request(
                "GET",
                "misc/status",
                params={"machine_id": machine_id},
                base_url=base_url,
            )
            status = StatusResponse(**resp)
        except NotFoundError:
            # 404 = no ErrorLog row yet → still creating, keep polling
            time.sleep(POLL_INTERVAL_S)
            continue

        if status.status == "Running":
            return
        if status.status == "Failed":
            raise APIError(0, f"Instance creation failed: {status.error} (code={status.code})")

        time.sleep(POLL_INTERVAL_S)

    raise APIError(0, f"Timed out waiting for instance {machine_id} ({timeout}s)")


def _fetch_instances(transport: Transport) -> list[Instance]:
    """Fetch all instances from the default region (shared DB)."""
    resp = transport.request("GET", "users/fetch")
    parsed = InstanceListResponse(**resp)
    return parsed.instances


def _get_instance(transport: Transport, machine_id: int) -> Instance:
    """Fetch a specific instance by machine_id, or raise NotFoundError."""
    resp = transport.request("GET", f"users/fetch/{machine_id}")
    if not _normalize_success(resp):
        raise NotFoundError(f"Instance {machine_id} not found")
    return Instance(**resp["instance"])
