# jarvislabs

CLI and Python SDK for managing JarvisLabs GPU instances.

## Installation

From source:

```bash
uv pip install -e .
```

As a package:

```bash
uv pip install jarvislabs
```

## Authentication

```bash
jl login
```

Or set an env var:

```bash
export JL_API_KEY="<your_api_key>"
```

## CLI Quick Start

Show top-level help and command groups:

```bash
jl --help
jl instance --help
jl scripts --help
jl filesystem --help
```

Common commands:

```bash
jl status
jl gpus
jl templates
jl instance list
jl scripts list
jl filesystem list
```

Instance lifecycle:

```bash
jl instance create --gpu RTX5000 --storage 40 --name my-instance
jl instance pause <machine_id>
jl instance resume <machine_id>
jl instance destroy <machine_id>
```

Script and filesystem integration:

```bash
jl scripts add ./startup.sh --name setup-script
jl filesystem create --name data --storage 120
jl instance create --gpu RTX5000 --script-id <script_id> --fs-id <fs_id>
```

## SDK Quick Start

```python
from jarvislabs import Client

with Client() as client:
    instances = client.instances.list()
    print([i.machine_id for i in instances])
```

Create an instance:

```python
from jarvislabs import Client

with Client() as client:
    inst = client.instances.create(
        gpu_type="RTX5000",
        num_gpus=1,
        template="pytorch",
        storage=40,
        name="my-instance",
    )
    print(inst.machine_id, inst.status)
```

## Current Behavior Notes

- Region is internal and auto-resolved in SDK/CLI.
- `create`/`resume` are reserved-only (`is_reserved=True`).
- CLI command naming uses `list` consistently (`instance list`, `scripts list`, `filesystem list`).

## Development

```bash
uv run ruff format .
uv run ruff check --fix .
uv run pytest
```
