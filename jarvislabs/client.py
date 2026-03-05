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
    FETCH_RETRY_INTERVAL_S,
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
    StartupScript,
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
        self.scripts = Scripts(self._transport)
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
        """GPU types, pricing, and current availability."""
        resp = self._t.request("GET", "misc/server_meta")
        meta = ServerMetaResponse(**resp)
        return meta.server_meta

    def currency(self) -> str:
        """Return 'INR' or 'USD' based on user's payment location."""
        resp = self._t.request("GET", "misc/")
        return "INR" if _normalize_success(resp) else "USD"


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
            raise APIError(0, f"Failed to add SSH key: {_backend_msg(resp)}")
        return True

    def remove(self, key_id: str) -> bool:
        resp = self._t.request("DELETE", f"ssh/{key_id}")
        if not _normalize_success(resp):
            raise APIError(0, f"Failed to remove SSH key: {_backend_msg(resp)}")
        return True


class Scripts:
    """Manage startup scripts used during instance create/resume."""

    def __init__(self, transport: Transport) -> None:
        self._t = transport

    def list(self) -> list[StartupScript]:
        resp = self._t.request("GET", "scripts/")
        if not isinstance(resp, dict):
            raise APIError(0, "Failed to fetch scripts: unexpected response")
        if ("success" in resp or "sucess" in resp) and not _normalize_success(resp):
            raise APIError(0, f"Failed to fetch scripts: {_backend_msg(resp)}")
        return [StartupScript(**item) for item in resp.get("script_meta", [])]

    def add(self, script: bytes | bytearray | str, name: str = "") -> bool:
        content = _coerce_script_bytes(script)
        params = {"name": name} if name else None
        self._t.request(
            "POST",
            "scripts/add",
            params=params,
            files={"script": ("startup.sh", content, "application/x-sh")},
        )
        return True

    def update(self, script_id: int, script: bytes | bytearray | str) -> bool:
        content = _coerce_script_bytes(script)
        self._t.request(
            "POST",
            "scripts/update",
            params={"script_id": script_id},
            files={"script": ("startup.sh", content, "application/x-sh")},
        )
        return True

    def remove(self, script_id: int) -> bool:
        self._t.request("DELETE", "scripts/", params={"script_id": script_id})
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
        disk_type: str = "ssd",
        is_reserved: bool = True,
        http_ports: str = "",
        script_id: str | None = None,
        script_args: str = "",
        fs_id: int | None = None,
        arguments: str = "",
    ) -> Instance:
        if not gpu_type:
            raise ValidationError("gpu_type is required (e.g. 'A100', 'H100', 'RTX5000')")
        if name and len(name) > 40:
            raise ValidationError("Instance name must be 40 characters or fewer")

        # Region is intentionally not user-facing. SDK auto-routes based on server meta.
        region = _resolve_region(self._t, gpu_type=gpu_type, num_gpus=num_gpus)

        storage = _apply_europe_constraints(gpu_type, num_gpus, storage, region)

        if template == "vm":
            _preflight_vm(gpu_type, region, self._ssh_keys.list())

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

        base_url = _region_url(region)
        resp = self._t.request(
            "POST",
            f"templates/{template}/create",
            json=payload,
            base_url=base_url,
        )

        machine_id = resp.get("machine_id")
        if not machine_id:
            raise APIError(0, f"Instance creation failed: {_backend_msg(resp)}")

        # Poll until running, then fetch full instance details
        _poll_until_running(self._t, machine_id, region)
        return _get_instance(self._t, machine_id, retries=3)

    def pause(self, machine_id: int) -> bool:
        instance = _get_instance(self._t, machine_id)
        base_url = _region_url(instance.region or DEFAULT_REGION)

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
            raise APIError(0, f"Failed to pause instance: {_backend_msg(resp)}")
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
        if instance.status != "Paused":
            raise ValidationError(f"Can only resume a Paused instance (current status: {instance.status})")

        # Resume is region-locked — backend always uses instance's original region
        region = instance.region or DEFAULT_REGION
        base_url = _region_url(region)

        # Warn early if the requested GPU isn't available in this region
        if gpu_type and gpu_type != instance.gpu_type:
            _check_gpu_in_region(self._t, gpu_type, num_gpus or instance.num_gpus or 1, region)

        effective_gpu = gpu_type or instance.gpu_type
        effective_num = num_gpus or instance.num_gpus or 1
        effective_storage = storage or instance.storage_gb or 40
        effective_storage = _apply_europe_constraints(effective_gpu, effective_num, effective_storage, region)
        if effective_storage != (storage or instance.storage_gb or 40):
            storage = effective_storage

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
            raise APIError(0, f"Failed to resume instance: {_backend_msg(resp)}")

        _poll_until_running(self._t, mid, region)
        return _get_instance(self._t, mid, retries=3)

    def destroy(self, machine_id: int) -> bool:
        instance = _get_instance(self._t, machine_id)
        base_url = _region_url(instance.region or DEFAULT_REGION)

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
            raise APIError(0, f"Failed to destroy instance: {_backend_msg(resp)}")
        return True


# ── Helpers ──────────────────────────────────────────────────────────────────


def _backend_msg(resp: dict) -> str:
    """Extract a human-readable error from an API response dict."""
    for key in ("message", "error", "detail"):
        if resp.get(key):
            return str(resp[key])
    return "unexpected error"


def _coerce_script_bytes(script: bytes | bytearray | str) -> bytes:
    if isinstance(script, bytes):
        content = script
    elif isinstance(script, bytearray):
        content = bytes(script)
    elif isinstance(script, str):
        content = script.encode("utf-8")
    else:
        raise ValidationError("script must be bytes, bytearray, or str")

    if not content.strip():
        raise ValidationError("Script content cannot be empty")
    return content


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


def _apply_europe_constraints(gpu_type: str | None, num_gpus: int, storage: int, region: str) -> int:
    """Auto-bump storage and validate Europe constraints. Returns adjusted storage."""
    if region != EUROPE_REGION:
        return storage
    if storage < EUROPE_MIN_STORAGE_GB:
        storage = EUROPE_MIN_STORAGE_GB
    if gpu_type:
        _validate_europe(gpu_type, num_gpus, storage)
    return storage


def _validate_europe(gpu_type: str, num_gpus: int, storage_gb: int) -> None:
    if gpu_type not in EUROPE_GPU_TYPES:
        raise ValidationError(f"europe-01 supports only {sorted(EUROPE_GPU_TYPES)} GPUs, got {gpu_type}")
    if num_gpus not in EUROPE_GPU_COUNTS:
        raise ValidationError(f"europe-01 supports {sorted(EUROPE_GPU_COUNTS)} GPUs per instance, got {num_gpus}")
    if storage_gb < EUROPE_MIN_STORAGE_GB:
        raise ValidationError(f"europe-01 requires at least {EUROPE_MIN_STORAGE_GB}GB storage")


def _check_gpu_in_region(transport: Transport, gpu_type: str, num_gpus: int, region: str) -> None:
    """Raise early if the requested GPU isn't available for the paused instance."""
    try:
        resp = transport.request("GET", "misc/server_meta")
        meta = ServerMetaResponse(**resp)
    except Exception:
        return  # Can't check — let the backend decide

    if not meta.server_meta:
        return  # No data — let the backend decide

    in_region = [s for s in meta.server_meta if s.gpu_type == gpu_type and s.region == region]
    if not in_region:
        raise ValidationError(
            f"{gpu_type} is not available in {region}. Paused instances can only resume in their original region."
        )

    free = any(s.num_free_devices >= num_gpus for s in in_region)
    if not free:
        raise ValidationError(f"No free {gpu_type} GPUs in {region} right now. Try again later.")


def _preflight_vm(gpu_type: str, region: str, ssh_keys: list[SSHKey]) -> None:
    if gpu_type not in EUROPE_GPU_TYPES:
        raise ValidationError("VM template supports only H100/H200 GPUs")
    if region != EUROPE_REGION:
        raise ValidationError("VM template is only available in europe-01")
    if not ssh_keys:
        raise ValidationError(
            "VM instances require at least one SSH key. Add one with: jl ssh-key add <pubkey-file> --name 'my-key'"
        )


def _region_url(region: str | None) -> str:
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
            code_hint = f" (code={status.code})" if status.code else ""
            raise APIError(0, f"Instance creation failed: {status.error or 'unknown error'}{code_hint}")

        time.sleep(POLL_INTERVAL_S)

    raise APIError(0, f"Timed out after {timeout}s waiting for instance {machine_id} to start. Try again later.")


def _fetch_instances(transport: Transport) -> list[Instance]:
    """Fetch all instances from the default region (shared DB)."""
    resp = transport.request("GET", "users/fetch")
    if not _normalize_success(resp):
        raise APIError(0, f"Failed to fetch instances: {_backend_msg(resp)}")
    parsed = InstanceListResponse(**resp)
    return parsed.instances


def _get_instance(transport: Transport, machine_id: int, *, retries: int = 0) -> Instance:
    """Fetch a specific instance by machine_id, or raise NotFoundError.

    retries > 0 is used after create/resume to handle DB replication lag —
    the instance exists but replicas may not have it yet.
    """
    for attempt in range(retries + 1):
        try:
            resp = transport.request("GET", f"users/fetch/{machine_id}")
            if not _normalize_success(resp):
                raise NotFoundError(f"Instance {machine_id} not found")
            instance_data = resp.get("instance")
            if not instance_data:
                raise NotFoundError(f"Instance {machine_id} not found")
            return Instance(**instance_data)
        except NotFoundError as err:
            if attempt < retries:
                time.sleep(FETCH_RETRY_INTERVAL_S)
                continue
            raise NotFoundError(f"Instance {machine_id} not found. Check the ID with: jl instance list") from err
