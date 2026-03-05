"""Static configuration — region URLs, GPU types, Europe isolation, defaults.

Single source of truth. When a region is added/removed or GPU types change,
only this file needs updating.
"""

# ── Regions ──────────────────────────────────────────────────────────────────

DEFAULT_REGION = "india-01"

REGION_URLS: dict[str, str] = {
    "india-01": "https://backendprod.jarvislabs.net/",
    "india-noida-01": "https://backendn.jarvislabs.net/",
    "europe-01": "https://backendeu.jarvislabs.net/",
}

# ── Europe isolation (removable when GPUs move to Noida) ─────────────────────

EUROPE_REGION = "europe-01"
EUROPE_GPU_TYPES: frozenset[str] = frozenset({"H100", "H200"})
EUROPE_GPU_COUNTS: frozenset[int] = frozenset({1, 8})
EUROPE_MIN_STORAGE_GB = 100
EUROPE_POLL_TIMEOUT_S = 300  # 5 min — Nebius is slower

# ── Timeouts & Polling ───────────────────────────────────────────────────────

DEFAULT_POLL_TIMEOUT_S = 180  # 3 min for India regions
POLL_INTERVAL_S = 3
FETCH_RETRY_INTERVAL_S = 2  # DB replication lag retry — much shorter than poll interval
HTTP_TIMEOUT_CONNECT_S = 10
HTTP_TIMEOUT_READ_S = 120  # V2/Nebius pause/destroy are synchronous and slow
MAX_RETRIES = 3
RETRY_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})

# ── CLI Defaults ─────────────────────────────────────────────────────────────

DEFAULT_TEMPLATE = "pytorch"
DEFAULT_GPU_TYPE = "RTX5000"
DEFAULT_NUM_GPUS = 1
DEFAULT_STORAGE_GB = 40  # auto-bumped to EUROPE_MIN_STORAGE_GB for europe
DEFAULT_INSTANCE_NAME = "Name me"

# ── GPU types (for validation / help text) ───────────────────────────────────

GPU_TYPES: frozenset[str] = frozenset(
    {
        "RTX5000",
        "A5000",
        "A5000Pro",
        "A6000",
        "A100",
        "A100-80GB",
        "RTX6000Ada",
        "H100",
        "H200",
        "L4",
    }
)
