"""Pydantic models for parsing backend responses.

Request payloads are built as plain dicts in client.py — no Pydantic models
for outbound data. Validation lives as simple if-checks in the client layer.
"""

from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

# ── Account ──────────────────────────────────────────────────────────────────


class Balance(BaseModel):
    balance: float
    grants: float


class UserInfo(BaseModel):
    user_id: str
    name: str | None = None
    address1: str | None = None
    address2: str | None = None
    city: str | None = None
    country: str | None = None
    phone_number: str | None = None
    state: str | None = None
    zip_code: str | None = None
    tax_id: str | None = None


class ResourceMetrics(BaseModel):
    running_instances: int
    paused_instances: int
    running_vms: int
    paused_vms: int
    deployments: int
    filesystems: int


# ── SSH Keys ─────────────────────────────────────────────────────────────────


class SSHKey(BaseModel):
    ssh_key: str
    key_name: str
    key_id: str
    user_id: str | None = None


# ── Scripts ──────────────────────────────────────────────────────────────────


class StartupScript(BaseModel):
    script_id: int
    script_name: str | None = None


# ── Filesystems ──────────────────────────────────────────────────────────────


class Filesystem(BaseModel):
    fs_id: int
    fs_name: str | None = None
    storage: int | None = None


# ── Templates ─────────────────────────────────────────────────────────────────


class Template(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    title: str
    description: str | None = None
    category: str | None = None
    versions: str | None = None


# ── Server Meta (for auto-routing + GPU availability) ─────────────────────────


class ServerMetaGPU(BaseModel):
    model_config = ConfigDict(extra="allow")

    gpu_type: str
    region: str
    num_free_devices: int = 0
    price_per_hour: float | None = None
    vram: str | None = None
    arc: str | None = None
    cpus_per_gpu: int | None = None
    ram_per_gpu: int | None = None

    @field_validator("num_free_devices", mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> int:
        if v is None or (isinstance(v, str) and not v.strip()):
            return 0
        return int(v)


class ServerMetaResponse(BaseModel):
    server_meta: list[ServerMetaGPU] = Field(default_factory=list)


# ── Instance ─────────────────────────────────────────────────────────────────


class Instance(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    machine_id: int
    cost: float = 0.0
    runtime: str | int = Field(default=0, validation_alias=AliasChoices("runtime", "duration"))
    gpu_type: str | None = None
    ram: int | None = None
    storage_gb: int | None = Field(default=None, validation_alias=AliasChoices("storage_gb", "hdd"))
    cores: int | None = None
    template: str = Field(validation_alias=AliasChoices("template", "framework"))
    framework_id: str | None = None
    version: str | None = None
    fs_id: int | None = None
    num_gpus: int | None = None
    url: str | None = None
    ssh_command: str | None = Field(default=None, validation_alias=AliasChoices("ssh_command", "ssh_str"))
    status: str
    paused_image_size: float | None = Field(default=None, validation_alias=AliasChoices("paused_image_size", "v_size"))
    endpoints: list[str] | None = None
    name: str | None = Field(default=None, validation_alias=AliasChoices("name", "instance_name"))
    is_reserved: bool | None = None
    billing_frequency: str | None = Field(default=None, validation_alias=AliasChoices("billing_frequency", "frequency"))
    vs_url: str | None = None
    deployment_id: str | None = None
    user_id: str | None = None
    disk_type: str | None = None
    public_ip: str | None = None
    http_ports: str | None = None
    region: str | None = None

    @field_validator("ram", "storage_gb", "cores", mode="before")
    @classmethod
    def _coerce_int(cls, v: Any) -> int | None:
        if v is None:
            return None
        if isinstance(v, str):
            v = v.strip()
            return int(v) if v else None
        return int(v)


# ── Response wrappers ────────────────────────────────────────────────────────


class InstanceListResponse(BaseModel):
    success: str | bool
    instances: list[Instance] = Field(default_factory=list)


class StatusResponse(BaseModel):
    status: str
    error: str | None = None
    code: int | str | None = None

    @field_validator("error", mode="before")
    @classmethod
    def _coerce_error_none(cls, v: Any) -> str | None:
        if v is None:
            return None
        if isinstance(v, str):
            raw = v.strip()
            if raw.lower() in {"none", "null", ""}:
                return None
            return raw
        return str(v)

    @field_validator("code", mode="before")
    @classmethod
    def _coerce_code_none(cls, v: Any) -> int | str | None:
        if v is None:
            return None
        if isinstance(v, str):
            raw = v.strip()
            if raw.lower() in {"none", "null", ""}:
                return None
            return raw
        return v
