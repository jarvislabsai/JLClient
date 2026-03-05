# JarvisLabs SDK & CLI Documentation

Python SDK and CLI for [JarvisLabs.ai](https://jarvislabs.ai) GPU cloud. Manage GPU instances, SSH keys, startup scripts, and persistent filesystems.

**Package:** `jarvislabs` | **CLI command:** `jl` | **Python:** 3.11+

---

## Table of Contents

- [Installation](#installation)
- [CLI](#cli)
  - [Authentication](#authentication)
  - [Quick Start](#quick-start)
  - [Getting Help](#getting-help)
  - [Global Flags](#global-flags)
  - [Account Commands](#account-commands)
  - [Resource Commands](#resource-commands)
  - [Instance Commands](#instance-commands)
  - [SSH Key Commands](#ssh-key-commands)
  - [Startup Script Commands](#startup-script-commands)
  - [Filesystem Commands](#filesystem-commands)
- [Python SDK](#python-sdk)
  - [Authentication](#authentication-1)
  - [Quick Start](#quick-start-1)
  - [Client](#client)
  - [Account](#account)
  - [Instances](#instances)
  - [SSH Keys](#ssh-keys)
  - [Startup Scripts](#startup-scripts)
  - [Filesystems](#filesystems)
  - [Error Handling](#error-handling)

---

## Installation

```bash
uv pip install jarvislabs
```

From source:

```bash
git clone https://github.com/jarvislabsai/jlclient.git
cd jlclient
uv pip install -e .
```

After installation, the `jl` command is available in your terminal and `from jarvislabs import Client` works in Python.

---

## CLI

### Authentication

Get your API token from [jarvislabs.ai/settings/api-keys](https://jarvislabs.ai/settings/api-keys).

```bash
jl login                    # Interactive prompt
jl login --token YOUR_TOKEN # Non-interactive
```

Or set an environment variable:

```bash
export JL_API_KEY="YOUR_TOKEN"
```

### Quick Start

```bash
# 1. Login
jl login

# 2. See available GPUs and pricing
jl gpus

# 3. Create an instance
jl instance create --gpu A100 --name "my-instance"

# 4. SSH into it
jl instance ssh 12345

# 5. When done, pause to stop billing (storage persists)
jl instance pause 12345

# 6. Resume later — optionally with different hardware
jl instance resume 12345 --gpu L4

# 7. Destroy when you no longer need it
jl instance destroy 12345
```

See [Instance Commands](#instance-commands), [SSH Key Commands](#ssh-key-commands), [Startup Script Commands](#startup-script-commands), and [Filesystem Commands](#filesystem-commands) below for the full command reference.

---

### Getting Help

Every command supports `--help`:

```bash
jl --help                  # All top-level commands
jl instance --help         # All instance subcommands
jl instance create --help  # Options for creating an instance
```

---

### Global Flags

These are root-level flags and **must be placed before the command**:


| Flag              | Description                                   |
| ----------------- | --------------------------------------------- |
| `--json`          | Output as machine-readable JSON (to stdout)   |
| `--yes`           | Skip all confirmation prompts                 |
| `--token API_KEY` | Override stored API token for this invocation |
| `--version`       | Print version and exit                        |


> **Note:** `jl --json instance list` works, but `jl instance list --json` does not. Global flags must come first.

Examples:

```bash
jl --json instance list           # Instance list as JSON
jl --json gpus                    # GPU availability as JSON
jl --yes instance create --gpu A100     # Skip confirmation prompt
jl --yes instance destroy 12345   # Skip "are you sure?" prompt
```

---

### Account Commands

#### `jl login`

Save your API token.


| Option    | Short | Description                     |
| --------- | ----- | ------------------------------- |
| `--token` | `-t`  | API token (prompted if omitted) |


If already logged in, you'll be asked to confirm re-authentication. After first login, enable tab completion with `jl --install-completion`.

#### `jl logout`

Remove the saved API token.

#### `jl status`

Show account info: name, user ID, balance, grants, and resource counts.

---

### Resource Commands

#### `jl gpus`

Show GPU types with availability, VRAM, RAM, CPUs, and hourly pricing.

#### `jl templates`

List available framework templates that can be used with `--template` when creating instances.

---

### Instance Commands

All instance commands live under `jl instance`.

#### `jl instance list`

List all instances with ID, name, status, GPU type, GPU count, storage, cost, and template.

#### `jl instance get <machine_id>`

Show details of a specific instance.

#### `jl instance create`

Create a new GPU instance. Blocks until the instance is running.


| Option          | Short | Default      | Description                             |
| --------------- | ----- | ------------ | --------------------------------------- |
| `--gpu`         | `-g`  | *(required)* | GPU type (run `jl gpus` to see options) |
| `--template`    | `-t`  | `pytorch`    | Framework template (run `jl templates`) |
| `--storage`     | `-s`  | `40`         | Storage in GB                           |
| `--name`        | `-n`  | `"Name me"`  | Instance name (max 40 characters)       |
| `--num-gpus`    |       | `1`          | Number of GPUs                          |
| `--script-id`   |       |              | Startup script ID to run on launch      |
| `--script-args` |       |              | Arguments passed to the startup script  |
| `--fs-id`       |       |              | Filesystem ID to attach                 |


Prompts for confirmation. Use `jl --yes instance create ...` to skip.

#### `jl instance rename <machine_id>`


| Option   | Short | Description                            |
| -------- | ----- | -------------------------------------- |
| `--name` | `-n`  | New name (required, max 40 characters) |


Prompts for confirmation.

#### `jl instance pause <machine_id>`

Pause a running instance. Compute billing stops; storage billing continues. Prompts for confirmation.

#### `jl instance resume <machine_id>`

Resume a paused instance. Optionally change GPU, expand storage, rename, or attach a different script/filesystem. Blocks until running.


| Option          | Short | Description                              |
| --------------- | ----- | ---------------------------------------- |
| `--gpu`         | `-g`  | Resume with a different GPU type         |
| `--num-gpus`    |       | Change number of GPUs                    |
| `--storage`     | `-s`  | Expand storage in GB (can only increase) |
| `--name`        | `-n`  | Rename instance on resume                |
| `--script-id`   |       | Startup script ID to run                 |
| `--script-args` |       | Arguments for the startup script         |
| `--fs-id`       |       | Filesystem ID to attach                  |


Prompts for confirmation.

#### `jl instance destroy <machine_id>`

Permanently delete an instance. This cannot be undone. Prompts for confirmation.

#### `jl instance ssh <machine_id>`

SSH into a running instance.


| Option            | Short | Description                                           |
| ----------------- | ----- | ----------------------------------------------------- |
| `--print-command` | `-p`  | Print the SSH command to stdout instead of connecting |


---

### SSH Key Commands

SSH keys are required for VM template instances.

#### `jl ssh-key list`

#### `jl ssh-key add <pubkey-file>`


| Option   | Short | Description                  |
| -------- | ----- | ---------------------------- |
| `--name` | `-n`  | Name for this key (required) |


#### `jl ssh-key remove <key_id>`

Prompts for confirmation.

---

### Startup Script Commands

Startup scripts run automatically when an instance launches or resumes.

#### `jl scripts list`

#### `jl scripts add <script-file>`

The filename (without extension) is used as the script name unless `--name` is provided.


| Option   | Short | Description                             |
| -------- | ----- | --------------------------------------- |
| `--name` | `-n`  | Script name (defaults to filename stem) |


#### `jl scripts update <script_id> <script-file>`

Replace the contents of an existing startup script.

#### `jl scripts remove <script_id>`

Prompts for confirmation.

---

### Filesystem Commands

Persistent filesystems survive instance pauses and can be shared across instances.

#### `jl filesystem list`

#### `jl filesystem create`


| Option      | Short | Description                                   |
| ----------- | ----- | --------------------------------------------- |
| `--name`    | `-n`  | Filesystem name (required, max 30 characters) |
| `--storage` | `-s`  | Storage in GB (required, 50–2048)             |


Prompts for confirmation.

#### `jl filesystem edit <fs_id>`

Expand storage. Can only increase.


| Option      | Short | Description                                |
| ----------- | ----- | ------------------------------------------ |
| `--storage` | `-s`  | New storage size in GB (required, 50–2048) |


Prompts for confirmation.

#### `jl filesystem remove <fs_id>`

Prompts for confirmation.

---

## Python SDK

### Authentication

Get your API token from [jarvislabs.ai/settings/api-keys](https://jarvislabs.ai/settings/api-keys).

```python
from jarvislabs import Client

# Option 1: Pass directly
client = Client(api_key="YOUR_TOKEN")

# Option 2: Uses JL_API_KEY env var or saved config (jl login)
client = Client()
```

### Quick Start

```python
from jarvislabs import Client

with Client() as client:
    # Create a GPU instance (blocks until running)
    inst = client.instances.create(gpu_type="A100", name="my-run")
    print(f"SSH: {inst.ssh_command}")
    print(f"Notebook: {inst.url}")

    # When done, pause to stop billing
    client.instances.pause(inst.machine_id)

    # Resume later — optionally with different hardware
    inst = client.instances.resume(inst.machine_id, gpu_type="H100")

    # Destroy when you no longer need it
    client.instances.destroy(inst.machine_id)
```

See [Instances](#instances), [SSH Keys](#ssh-keys), [Startup Scripts](#startup-scripts), and [Filesystems](#filesystems) below for the full API reference.

---

### Client

Entry point for all SDK operations. Supports context manager for automatic cleanup.

```python
from jarvislabs import Client

with Client(api_key="...") as client:
    ...

# Or without context manager
client = Client()
client.instances.list()
client.close()
```

**Namespaces:**


| Attribute            | Description                                     |
| -------------------- | ----------------------------------------------- |
| `client.account`     | Balance, user info, GPU availability, templates |
| `client.ssh_keys`    | SSH key management                              |
| `client.scripts`     | Startup script management                       |
| `client.filesystems` | Persistent filesystem management                |
| `client.instances`   | Instance lifecycle                              |


---

### Account

```python
client.account.balance()           # -> Balance (balance, grants)
client.account.user_info()         # -> UserInfo (user_id, name, country, ...)
client.account.resource_metrics()  # -> ResourceMetrics (running/paused counts)
client.account.templates()         # -> list[Template] (id, title, category)
client.account.gpu_availability()  # -> list[ServerMetaGPU] (gpu_type, price, vram, availability)
client.account.currency()          # -> "INR" or "USD"
```

---

### Instances

#### `list() -> list[Instance]`

```python
instances = client.instances.list()
running = [i for i in instances if i.status == "Running"]
```

#### `get(machine_id: int) -> Instance`

Raises `NotFoundError` if the instance doesn't exist.

#### `create(...) -> Instance`

Create a new GPU instance. Blocks until running.


| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `gpu_type` | `str` | *(required)* | GPU type (see `client.account.gpu_availability()` or run `jl gpus`) |
| `num_gpus` | `int` | `1` | Number of GPUs |
| `template` | `str` | `"pytorch"` | Framework template |
| `storage` | `int` | `40` | Storage in GB |
| `name` | `str` | `"Name me"` | Instance name (max 40 chars) |
| `script_id` | `str` or `None` | `None` | Startup script ID |
| `script_args` | `str` | `""` | Script arguments |
| `fs_id` | `int` or `None` | `None` | Filesystem ID to attach |


#### `pause(machine_id: int) -> bool`

Pause a running instance. Compute billing stops; storage billing continues.

#### `resume(machine_id: int, ...) -> Instance`

Resume a paused instance. Blocks until running. All parameters except `machine_id` are optional — omitted values keep the current configuration.

> **Note:** Resume may return an instance with a new `machine_id`. Always use the returned instance's ID for subsequent operations.


| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `gpu_type` | `str` or `None` | `None` | Change GPU type |
| `num_gpus` | `int` or `None` | `None` | Change GPU count |
| `storage` | `int` or `None` | `None` | Expand storage (GB) |
| `name` | `str` or `None` | `None` | Rename |
| `script_id` | `str` or `None` | `None` | Startup script ID |
| `script_args` | `str` or `None` | `None` | Script arguments |
| `fs_id` | `int` or `None` | `None` | Filesystem ID |


#### `destroy(machine_id: int) -> bool`

Permanently delete an instance. Cannot be undone.

#### `rename(machine_id: int, name: str) -> bool`

Rename an instance. Name must be 1–40 characters.

---

### SSH Keys

```python
client.ssh_keys.list()                                              # -> list[SSHKey]
client.ssh_keys.add(ssh_key="ssh-rsa AAAA...", key_name="laptop")   # -> bool
client.ssh_keys.remove(key_id="abc123")                             # -> bool
```

---

### Startup Scripts

```python
client.scripts.list()                                          # -> list[StartupScript]
client.scripts.add(script=b"#!/bin/bash\n...", name="setup")   # -> bool
client.scripts.update(script_id=42, script=b"...")             # -> bool
client.scripts.remove(script_id=42)                            # -> bool
```

`script` accepts `str`, `bytes`, or `bytearray`.

---

### Filesystems

```python
client.filesystems.list()                                 # -> list[Filesystem]
client.filesystems.create(fs_name="data", storage=100)    # -> int (fs_id)
client.filesystems.edit(fs_id=7, storage=200)             # -> int (fs_id)
client.filesystems.remove(fs_id=7)                        # -> bool
```

Name: max 30 characters. Storage: 50–2048 GB.

> **Note:** `create()` and `edit()` may return a new `fs_id`. Always use the returned value for subsequent operations.

---

### Error Handling

All exceptions inherit from `JarvislabsError`:

```python
from jarvislabs import (
    JarvislabsError,           # Base class — catch-all
    AuthError,                 # Invalid or missing API token
    NotFoundError,             # Instance or resource not found
    InsufficientBalanceError,  # Not enough balance
    ValidationError,           # Invalid parameters (bad GPU, name too long, etc.)
    APIError,                  # Other backend errors
)
```

```python
from jarvislabs import Client, AuthError, NotFoundError, APIError

try:
    with Client() as client:
        inst = client.instances.create(gpu_type="A100")
except AuthError:
    print("Check your API token")
except NotFoundError:
    print("Resource not found")
except APIError as e:
    print(f"API error: {e}")
```

