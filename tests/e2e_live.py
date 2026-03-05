"""Live E2E test of the SDK against the real backend.

Run: set -a && source .env && set +a && uv run python tests/e2e_live.py

WARNING: This creates real GPU instances that cost money. Ensure all instances
are destroyed after the run. The cleanup handler at the bottom will attempt to
destroy any leftover instances on exit.
"""

from __future__ import annotations

import atexit
import os
import sys
import time
import traceback

# ── Setup ────────────────────────────────────────────────────────────────────

results: list[tuple[str, str, str]] = []  # (test_name, status, detail)
cleanup_ids: list[int] = []  # machine_ids to destroy on exit
destroyed_mid: int | None = None  # saved from section 6 for section 12 error-path testing


def record(name: str, status: str, detail: str = "") -> None:
    results.append((name, status, detail))
    icon = "✓" if status == "PASS" else "✗" if status == "FAIL" else "⚠"
    print(f"  {icon} {name}: {detail}" if detail else f"  {icon} {name}")


def section(name: str) -> None:
    print(f"\n{'═' * 60}")
    print(f"  {name}")
    print(f"{'═' * 60}")


def safe_destroy(c, mid: int) -> None:
    """Best-effort destroy. Never raises."""
    try:
        c.instances.destroy(mid)
        print(f"    🧹 Cleanup: destroyed {mid}")
    except Exception:
        print(f"    ⚠ Cleanup: failed to destroy {mid}")


# ── Import SDK ───────────────────────────────────────────────────────────────

try:
    from jarvislabs import APIError, AuthError, Client, NotFoundError, ValidationError
    from jarvislabs.exceptions import InsufficientBalanceError, JarvislabsError
except ImportError as e:
    print(f"Import failed: {e}")
    sys.exit(1)

# ── Cleanup handler ──────────────────────────────────────────────────────────

client: Client | None = None


def _cleanup() -> None:
    if not client or not cleanup_ids:
        return
    print("\n  === CLEANUP: destroying leftover instances ===")
    for mid in cleanup_ids:
        safe_destroy(client, mid)


atexit.register(_cleanup)

# ══════════════════════════════════════════════════════════════════════════════
# 1. CLIENT INITIALIZATION
# ══════════════════════════════════════════════════════════════════════════════

section("1. Client initialization")

# 1a. Normal init (token from env)
try:
    client = Client()
    record("Client()", "PASS", "Created with token from env/config")
except AuthError as e:
    record("Client()", "FAIL", str(e))
    print("\nCannot proceed without auth. Exiting.")
    sys.exit(1)

# 1b. Explicit token
try:
    token = os.environ.get("JL_API_KEY", "")
    explicit_client = Client(api_key=token)
    explicit_client.close()
    record("Client(api_key=explicit)", "PASS")
except Exception as e:
    record("Client(api_key=explicit)", "FAIL", str(e))

# 1c. Invalid token
try:
    bad_client = Client(api_key="invalid-token-12345")
    try:
        bad_client.account.balance()
        record("Client(bad token)", "FAIL", "should have raised AuthError")
    except AuthError:
        record("Client(bad token)", "PASS", "correctly raised AuthError")
    except Exception as e:
        record("Client(bad token)", "FAIL", f"wrong error: {type(e).__name__}: {e}")
    finally:
        bad_client.close()
except Exception as e:
    record("Client(bad token)", "FAIL", f"init error: {type(e).__name__}: {e}")

# 1d. Context manager
try:
    with Client() as ctx_client:
        ctx_bal = ctx_client.account.balance()
    record("with Client() as c:", "PASS", f"balance={ctx_bal.balance}")
except Exception as e:
    record("with Client() as c:", "FAIL", str(e))

# ══════════════════════════════════════════════════════════════════════════════
# 2. ACCOUNT ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

section("2. Account endpoints")

# Balance
try:
    bal = client.account.balance()
    assert isinstance(bal.balance, float), f"balance should be float, got {type(bal.balance)}"
    assert isinstance(bal.grants, float), f"grants should be float, got {type(bal.grants)}"
    record("account.balance()", "PASS", f"balance={bal.balance}, grants={bal.grants}")
except Exception as e:
    record("account.balance()", "FAIL", str(e))

# User info
try:
    info = client.account.user_info()
    assert info.user_id, "user_id should not be empty"
    record("account.user_info()", "PASS", f"user_id={info.user_id}, name={info.name}")
except Exception as e:
    record("account.user_info()", "FAIL", str(e))

# Resource metrics
try:
    metrics = client.account.resource_metrics()
    assert metrics.running_instances >= 0
    assert metrics.paused_instances >= 0
    assert metrics.running_vms >= 0
    assert metrics.paused_vms >= 0
    assert metrics.deployments >= 0
    assert metrics.filesystems >= 0
    record(
        "account.resource_metrics()",
        "PASS",
        f"running={metrics.running_instances}, paused={metrics.paused_instances}, "
        f"vms={metrics.running_vms}/{metrics.paused_vms}, "
        f"deployments={metrics.deployments}, filesystems={metrics.filesystems}",
    )
except Exception as e:
    record("account.resource_metrics()", "FAIL", str(e))

# Templates
try:
    templates = client.account.templates()
    assert len(templates) > 0, "should have at least 1 template"
    assert templates[0].id, "template should have an id"
    assert templates[0].title, "template should have a title"
    template_ids = [t.id for t in templates]
    record(
        "account.templates()",
        "PASS",
        f"count={len(templates)}, ids={template_ids[:5]}",
    )
    # Verify templates we depend on for later tests actually exist
    for required in ("pytorch", "fastai"):
        if required in template_ids:
            record(f"template '{required}' exists", "PASS")
        else:
            record(f"template '{required}' exists", "FAIL", f"not in {template_ids}")
except Exception as e:
    record("account.templates()", "FAIL", str(e))
    template_ids = []

# GPU availability
gpus = []
try:
    gpus = client.account.gpu_availability()
    assert len(gpus) > 0, "should have at least 1 GPU type"
    for g in gpus:
        assert g.gpu_type, "gpu_type should not be empty"
        assert g.region, "region should not be empty"
    record(
        "account.gpu_availability()",
        "PASS",
        f"count={len(gpus)}, types={sorted({g.gpu_type for g in gpus})}",
    )
except Exception as e:
    record("account.gpu_availability()", "FAIL", str(e))

# GPU prices
try:
    assert gpus, "gpus list is empty from previous failure"
    priced = [g for g in gpus if g.price_per_hour is not None and g.price_per_hour > 0]
    record("gpu_availability prices", "PASS", f"{len(priced)}/{len(gpus)} have prices")
except Exception as e:
    record("gpu_availability prices", "FAIL", str(e))

# ══════════════════════════════════════════════════════════════════════════════
# 3. SSH KEYS
# ══════════════════════════════════════════════════════════════════════════════

section("3. SSH Keys")

try:
    keys = client.ssh_keys.list()
    assert isinstance(keys, list)
    for k in keys:
        assert k.key_id, "key should have key_id"
        assert k.key_name, "key should have key_name"
    record("ssh_keys.list()", "PASS", f"count={len(keys)}")
except Exception as e:
    record("ssh_keys.list()", "FAIL", str(e))

# Add -> verify -> remove -> verify
TEST_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAITestKeyForE2E000000000000000000000000000 e2e-test@jl"
TEST_KEY_NAME = "e2e-test-key"
added_key_id = None

try:
    client.ssh_keys.add(TEST_KEY, TEST_KEY_NAME)
    keys_after = client.ssh_keys.list()
    found = [k for k in keys_after if k.key_name == TEST_KEY_NAME]
    assert found, "key not found in list after add"
    added_key_id = found[0].key_id
    record("ssh_keys.add() + verify", "PASS", f"key_id={added_key_id}")
except Exception as e:
    record("ssh_keys.add() + verify", "FAIL", str(e))

if added_key_id:
    try:
        client.ssh_keys.remove(added_key_id)
        keys_after_remove = client.ssh_keys.list()
        still_there = [k for k in keys_after_remove if k.key_id == added_key_id]
        assert not still_there, "key still in list after remove"
        record("ssh_keys.remove() + verify", "PASS")
    except Exception as e:
        record("ssh_keys.remove() + verify", "FAIL", str(e))

# Remove non-existent key
try:
    client.ssh_keys.remove("nonexistent-key-id-12345")
    record("ssh_keys.remove(invalid)", "PASS", "backend accepts no-op delete")
except Exception as e:
    record("ssh_keys.remove(invalid)", "WARN", f"unexpected: {type(e).__name__}: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# 4. INSTANCE LIST + GET
# ══════════════════════════════════════════════════════════════════════════════

section("4. Instance list & get")

try:
    instances = client.instances.list()
    assert isinstance(instances, list)
    record("instances.list()", "PASS", f"count={len(instances)}")
    for inst in instances:
        print(f"    -> {inst.machine_id}: {inst.name} [{inst.status}] {inst.gpu_type} region={inst.region}")
except Exception as e:
    record("instances.list()", "FAIL", str(e))
    instances = []

# get() on each existing instance
if instances:
    for inst in instances[:3]:
        try:
            fetched = client.instances.get(inst.machine_id)
            assert fetched.machine_id == inst.machine_id
            assert fetched.status == inst.status
            record(f"instances.get({inst.machine_id})", "PASS", f"status={fetched.status}")
        except Exception as e:
            record(f"instances.get({inst.machine_id})", "FAIL", str(e))

# get() with invalid ID
try:
    client.instances.get(999999999)
    record("instances.get(invalid)", "FAIL", "should have raised NotFoundError")
except NotFoundError:
    record("instances.get(invalid)", "PASS", "correctly raised NotFoundError")
except Exception as e:
    record("instances.get(invalid)", "FAIL", f"unexpected: {type(e).__name__}: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# 5. CLIENT-SIDE VALIDATION
# ══════════════════════════════════════════════════════════════════════════════

section("5. Client-side validation")

# gpu_type=None
try:
    client.instances.create(gpu_type=None)
    record("create(gpu_type=None)", "FAIL", "should have raised ValidationError")
except ValidationError:
    record("create(gpu_type=None)", "PASS", "ValidationError raised")
except Exception as e:
    record("create(gpu_type=None)", "FAIL", f"wrong error: {type(e).__name__}")

# Name too long
try:
    client.instances.create(gpu_type="RTX5000", name="x" * 50)
    record("create(name>40)", "FAIL", "should have raised ValidationError")
except ValidationError:
    record("create(name>40)", "PASS", "ValidationError raised")
except Exception as e:
    record("create(name>40)", "FAIL", f"wrong error: {type(e).__name__}")

# VM + non-H100 GPU
try:
    client.instances.create(gpu_type="L4", template="vm")
    record("create(vm+L4)", "FAIL", "should have raised ValidationError")
except ValidationError:
    record("create(vm+L4)", "PASS", "ValidationError raised")
except Exception as e:
    record("create(vm+L4)", "FAIL", f"wrong error: {type(e).__name__}")

# Region is now internal-only in SDK create(); these explicit region-negative
# checks are intentionally skipped.
record("create(vm+india)", "SKIP", "region is not user-facing in SDK create()")
record("create(europe+RTX5000)", "SKIP", "region is not user-facing in SDK create()")

# Europe + invalid GPU count
try:
    client.instances.create(gpu_type="H100", num_gpus=2)
    record("create(europe+2xH100)", "FAIL", "should have raised ValidationError")
except ValidationError:
    record("create(europe+2xH100)", "PASS", "ValidationError raised")
except Exception as e:
    record("create(europe+2xH100)", "FAIL", f"wrong error: {type(e).__name__}")

# Invalid template — should get a backend error, not crash
try:
    client.instances.create(gpu_type="RTX5000", template="nonexistent-template-xyz")
    record("create(bad template)", "FAIL", "should have raised error")
except (APIError, NotFoundError) as e:
    record("create(bad template)", "PASS", f"correctly raised {type(e).__name__}: {e}")
except Exception as e:
    record("create(bad template)", "FAIL", f"unexpected: {type(e).__name__}: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# 6. INSTANCE LIFECYCLE — INDIA-01 (create -> fields -> destroy running)
# ══════════════════════════════════════════════════════════════════════════════

section("6. Instance lifecycle — destroy running (india-01, RTX5000)")

machine_id = None

print("\n  Creating instance (RTX5000, pytorch, storage=20, name='e2e-test')...")
try:
    inst = client.instances.create(
        gpu_type="RTX5000",
        num_gpus=1,
        template="pytorch",
        storage=20,
        name="e2e-test",
    )
    machine_id = inst.machine_id
    cleanup_ids.append(machine_id)
    assert inst.status == "Running", f"expected Running, got {inst.status}"
    record(
        "create(RTX5000)",
        "PASS",
        f"id={inst.machine_id}, status={inst.status}, region={inst.region}",
    )
except Exception as e:
    record("create(RTX5000)", "FAIL", f"{type(e).__name__}: {e}")
    traceback.print_exc()

# ── Field accuracy: verify all requested params are reflected ────────────
if machine_id:
    try:
        fetched = client.instances.get(machine_id)
        assert fetched.machine_id == machine_id
        assert fetched.status == "Running"
        assert fetched.gpu_type == "RTX5000", f"gpu_type: expected RTX5000, got {fetched.gpu_type}"
        assert fetched.num_gpus == 1, f"num_gpus: expected 1, got {fetched.num_gpus}"
        assert fetched.template == "pytorch", f"template: expected pytorch, got {fetched.template}"
        assert fetched.name == "e2e-test", f"name: expected e2e-test, got {fetched.name}"
        assert fetched.storage_gb is not None and fetched.storage_gb >= 20, (
            f"storage: expected >=20, got {fetched.storage_gb}"
        )
        assert fetched.ssh_command, "should have ssh_command"
        assert fetched.url, "should have url"
        assert fetched.region, "should have region"
        assert fetched.cost is not None, "should have cost"
        assert fetched.disk_type, "should have disk_type"
        assert fetched.is_reserved is not None, "should have is_reserved"
        assert isinstance(fetched.status, str), "status should be str"
        record(
            "field accuracy",
            "PASS",
            f"gpu={fetched.gpu_type}, gpus={fetched.num_gpus}, tpl={fetched.template}, "
            f"name={fetched.name}, storage={fetched.storage_gb}GB, "
            f"cost={fetched.cost}, disk={fetched.disk_type}, reserved={fetched.is_reserved}",
        )
    except Exception as e:
        record("field accuracy", "FAIL", str(e))

# ── Verify it appears in list ────────────────────────────────────────────
if machine_id:
    try:
        all_instances = client.instances.list()
        found = [i for i in all_instances if i.machine_id == machine_id]
        assert found, f"machine_id {machine_id} not in list()"
        assert found[0].status == "Running"
        record("list() contains new", "PASS")
    except Exception as e:
        record("list() contains new", "FAIL", str(e))

# ── DESTROY RUNNING (not paused first) ──────────────────────────────────
if machine_id:
    print("\n  Destroying RUNNING instance directly (no pause)...")
    try:
        result = client.instances.destroy(machine_id)
        assert result is True
        record("destroy(running)", "PASS")
        cleanup_ids.remove(machine_id)
    except Exception as e:
        record("destroy(running)", "FAIL", f"{type(e).__name__}: {e}")
        traceback.print_exc()

    print("  Waiting 15s for destroy to settle...")
    time.sleep(15)
    try:
        client.instances.get(machine_id)
        record("get(after destroy running)", "WARN", "still visible")
    except NotFoundError:
        record("get(after destroy running)", "PASS", "correctly gone")
    except Exception as e:
        record("get(after destroy running)", "FAIL", str(e))

    destroyed_mid = machine_id  # save for error-path tests in section 12
    machine_id = None

# ── Different template — fastai (still india-01) ─────────────────────────

fastai_mid = None

print("\n  Creating instance (RTX5000, fastai)...")
try:
    fastai_inst = client.instances.create(
        gpu_type="RTX5000",
        num_gpus=1,
        template="fastai",
        storage=20,
        name="e2e-fastai",
    )
    fastai_mid = fastai_inst.machine_id
    cleanup_ids.append(fastai_mid)
    assert fastai_inst.status == "Running"
    assert fastai_inst.template == "fastai", f"template: expected fastai, got {fastai_inst.template}"
    record(
        "create(fastai)",
        "PASS",
        f"id={fastai_mid}, template={fastai_inst.template}",
    )
except Exception as e:
    record("create(fastai)", "FAIL", f"{type(e).__name__}: {e}")
    traceback.print_exc()

# Verify template field via get()
if fastai_mid:
    try:
        f_fetched = client.instances.get(fastai_mid)
        assert f_fetched.template == "fastai", f"expected fastai, got {f_fetched.template}"
        record("get(fastai template)", "PASS", f"template={f_fetched.template}")
    except Exception as e:
        record("get(fastai template)", "FAIL", str(e))

# Destroy
if fastai_mid:
    print("  Destroying fastai instance...")
    try:
        client.instances.destroy(fastai_mid)
        record("destroy(fastai)", "PASS")
        cleanup_ids.remove(fastai_mid)
    except Exception as e:
        record("destroy(fastai)", "FAIL", str(e))

# ══════════════════════════════════════════════════════════════════════════════
# 7. PAUSE + RESUME LIFECYCLE — INDIA-01 (with edge cases)
# ══════════════════════════════════════════════════════════════════════════════

section("7. Pause + resume lifecycle + edge cases (india-01)")

resume_mid = None

print("\n  Creating instance for pause/resume tests...")
try:
    inst2 = client.instances.create(
        gpu_type="RTX5000",
        num_gpus=1,
        template="pytorch",
        storage=20,
        name="e2e-resume",
    )
    resume_mid = inst2.machine_id
    cleanup_ids.append(resume_mid)
    record("create(for resume)", "PASS", f"id={resume_mid}")
except Exception as e:
    record("create(for resume)", "FAIL", f"{type(e).__name__}: {e}")
    traceback.print_exc()

# ── Resume a RUNNING instance — should error ────────────────────────────
if resume_mid:
    try:
        leaked = client.instances.resume(resume_mid)
        # Backend unexpectedly accepted — capture for cleanup to avoid GPU leak
        cleanup_ids.append(leaked.machine_id)
        record("resume(running)", "FAIL", f"should have raised error, got id={leaked.machine_id}")
    except (APIError, ValidationError) as e:
        record("resume(running)", "PASS", f"correctly raised {type(e).__name__}: {e}")
    except Exception as e:
        record("resume(running)", "FAIL", f"unexpected: {type(e).__name__}: {e}")

# ── Pause ────────────────────────────────────────────────────────────────
if resume_mid:
    print("  Pausing...")
    try:
        client.instances.pause(resume_mid)
        record("pause()", "PASS")
    except Exception as e:
        record("pause()", "FAIL", str(e))
        traceback.print_exc()

# ── Verify paused status ────────────────────────────────────────────────
pause_confirmed = False
if resume_mid:
    print("  Waiting 10s for status to settle...")
    time.sleep(10)
    try:
        paused_inst = client.instances.get(resume_mid)
        assert paused_inst.status == "Paused", f"expected Paused, got {paused_inst.status}"
        record("get(after pause)", "PASS", f"status={paused_inst.status}")
        pause_confirmed = True
    except Exception as e:
        record("get(after pause)", "FAIL", str(e))

# ── Double-pause — pause an already paused instance ─────────────────────
if resume_mid and pause_confirmed:
    try:
        client.instances.pause(resume_mid)
        record("double pause()", "WARN", "backend accepted double pause")
    except (APIError, NotFoundError) as e:
        record("double pause()", "PASS", f"correctly rejected: {type(e).__name__}: {e}")
    except Exception as e:
        record("double pause()", "FAIL", f"unexpected: {type(e).__name__}: {e}")

# ── Resume with parameter changes ───────────────────────────────────────
old_id = resume_mid
if resume_mid and pause_confirmed:
    print("  Resuming with name change and storage bump... ~1-3 min")
    try:
        resumed = client.instances.resume(
            resume_mid,
            name="e2e-resumed",
            storage=30,
        )
        assert resumed.status == "Running"
        resume_mid = resumed.machine_id
        cleanup_ids.append(resume_mid)
        record(
            "resume(with params)",
            "PASS",
            f"old_id={old_id}, new_id={resume_mid}",
        )

        # Verify machine_id changed
        if resumed.machine_id != old_id:
            record("resume() new machine_id", "PASS", f"{old_id} -> {resumed.machine_id}")
            if old_id in cleanup_ids:
                cleanup_ids.remove(old_id)
        else:
            record("resume() same machine_id", "PASS", f"id stayed {old_id}")

        # Verify params applied
        try:
            r_fetched = client.instances.get(resume_mid)
            assert r_fetched.name == "e2e-resumed", f"name: expected e2e-resumed, got {r_fetched.name}"
            assert r_fetched.storage_gb is not None and r_fetched.storage_gb >= 30, (
                f"storage: expected >=30, got {r_fetched.storage_gb}"
            )
            record(
                "resume param accuracy",
                "PASS",
                f"name={r_fetched.name}, storage={r_fetched.storage_gb}GB",
            )
        except Exception as e:
            record("resume param accuracy", "FAIL", str(e))

        # GET old id — should be gone (status "Resumed" = inactive)
        if resumed.machine_id != old_id:
            try:
                client.instances.get(old_id)
                record("get(old id after resume)", "WARN", "old id still visible")
            except NotFoundError:
                record("get(old id after resume)", "PASS", "old id correctly gone")
            except Exception as e:
                record("get(old id after resume)", "FAIL", str(e))

    except Exception as e:
        record("resume(with params)", "FAIL", f"{type(e).__name__}: {e}")
        traceback.print_exc()

# ── Double-destroy — destroy then destroy again ─────────────────────────
if resume_mid:
    print("\n  Destroying resumed instance...")
    try:
        client.instances.destroy(resume_mid)
        record("destroy(after resume)", "PASS")
        if resume_mid in cleanup_ids:
            cleanup_ids.remove(resume_mid)
    except Exception as e:
        record("destroy(after resume)", "FAIL", str(e))

    # Double-destroy — try destroying the same instance again
    print("  Waiting 15s, then attempting double-destroy...")
    time.sleep(15)
    try:
        client.instances.destroy(resume_mid)
        record("double destroy()", "WARN", "backend accepted double destroy")
    except NotFoundError:
        record("double destroy()", "PASS", "correctly raised NotFoundError")
    except Exception as e:
        record("double destroy()", "FAIL", f"unexpected: {type(e).__name__}: {e}")

    resume_mid = None

# ══════════════════════════════════════════════════════════════════════════════
# 8. AUTO-REGION ROUTING + EUROPE H100 FULL LIFECYCLE
# ══════════════════════════════════════════════════════════════════════════════

section("8. Auto-region routing + Europe H100 full lifecycle")

auto_mid = None

# Create H100 WITHOUT region param — SDK should auto-route to europe-01
# Also pass storage=20 — SDK should auto-bump to 100GB for europe
print("\n  Creating H100 with NO region param, storage=20 (should auto-route + auto-bump)...")
try:
    auto_inst = client.instances.create(
        gpu_type="H100",
        num_gpus=1,
        template="pytorch",
        storage=20,
        name="e2e-autoroute",
    )
    auto_mid = auto_inst.machine_id
    cleanup_ids.append(auto_mid)
    assert auto_inst.status == "Running"
    record(
        "create(H100 auto-route)",
        "PASS",
        f"id={auto_mid}, region={auto_inst.region}",
    )

    # Verify auto-routing picked europe-01
    if auto_inst.region == "europe-01":
        record("auto-route -> europe-01", "PASS", f"region={auto_inst.region}")
    else:
        record("auto-route -> europe-01", "WARN", f"routed to {auto_inst.region} instead")

    # Verify storage auto-bumped from 20 to >=100
    try:
        auto_fetched = client.instances.get(auto_mid)
        if auto_fetched.storage_gb is not None and auto_fetched.storage_gb >= 100:
            record("storage auto-bump", "PASS", f"requested 20, got {auto_fetched.storage_gb}GB")
        else:
            record("storage auto-bump", "FAIL", f"expected >=100, got {auto_fetched.storage_gb}")
    except Exception as e:
        record("storage auto-bump", "FAIL", str(e))

except Exception as e:
    record("create(H100 auto-route)", "FAIL", f"{type(e).__name__}: {e}")
    traceback.print_exc()

# ── Pause → Resume → Destroy (full lifecycle, reusing the same H100) ─────

if auto_mid:
    print("  Pausing H100...")
    try:
        client.instances.pause(auto_mid)
        record("pause(H100 europe)", "PASS")
    except Exception as e:
        record("pause(H100 europe)", "FAIL", str(e))

    print("  Waiting 10s for status to settle...")
    time.sleep(10)
    try:
        eu_paused = client.instances.get(auto_mid)
        record("get(H100 after pause)", "PASS", f"status={eu_paused.status}")
    except Exception as e:
        record("get(H100 after pause)", "FAIL", str(e))

# Resume
eu_old_id = auto_mid
if auto_mid:
    print("  Resuming H100... this will take ~3-5 min")
    try:
        eu_resumed = client.instances.resume(auto_mid)
        assert eu_resumed.status == "Running"
        auto_mid = eu_resumed.machine_id
        cleanup_ids.append(auto_mid)
        if eu_old_id in cleanup_ids:
            cleanup_ids.remove(eu_old_id)
        record(
            "resume(H100 europe)",
            "PASS",
            f"old_id={eu_old_id}, new_id={auto_mid}",
        )

        try:
            eu_check = client.instances.get(auto_mid)
            record("get(H100 new id)", "PASS", f"status={eu_check.status}")
        except Exception as e:
            record("get(H100 new id)", "FAIL", str(e))

    except Exception as e:
        record("resume(H100 europe)", "FAIL", f"{type(e).__name__}: {e}")
        traceback.print_exc()

# Destroy
if auto_mid:
    print("  Destroying H100...")
    try:
        client.instances.destroy(auto_mid)
        record("destroy(H100 europe)", "PASS")
        if auto_mid in cleanup_ids:
            cleanup_ids.remove(auto_mid)
    except Exception as e:
        record("destroy(H100 europe)", "FAIL", str(e))
        if eu_old_id and eu_old_id != auto_mid:
            safe_destroy(client, eu_old_id)

# ══════════════════════════════════════════════════════════════════════════════
# 9. EUROPE — H200 LIFECYCLE
# ══════════════════════════════════════════════════════════════════════════════

section("9. Europe region — H200 lifecycle")

h200_mid = None

print("\n  Creating H200 in europe-01... this will take ~3-5 min")
try:
    h200_inst = client.instances.create(
        gpu_type="H200",
        num_gpus=1,
        template="pytorch",
        storage=100,
        name="e2e-h200",
    )
    h200_mid = h200_inst.machine_id
    cleanup_ids.append(h200_mid)
    assert h200_inst.status == "Running"
    assert h200_inst.region == "europe-01"
    assert h200_inst.gpu_type == "H200"
    record(
        "create(H200 europe)",
        "PASS",
        f"id={h200_mid}, region={h200_inst.region}",
    )
except Exception as e:
    record("create(H200 europe)", "FAIL", f"{type(e).__name__}: {e}")
    traceback.print_exc()

# GET
if h200_mid:
    try:
        h200_fetched = client.instances.get(h200_mid)
        assert h200_fetched.gpu_type == "H200"
        assert h200_fetched.region == "europe-01"
        record("get(H200 europe)", "PASS", f"gpu={h200_fetched.gpu_type}, storage={h200_fetched.storage_gb}GB")
    except Exception as e:
        record("get(H200 europe)", "FAIL", str(e))

# Pause
if h200_mid:
    print("  Pausing H200...")
    try:
        client.instances.pause(h200_mid)
        record("pause(H200 europe)", "PASS")
    except Exception as e:
        record("pause(H200 europe)", "FAIL", str(e))

    print("  Waiting 10s for status to settle...")
    time.sleep(10)
    try:
        h200_paused = client.instances.get(h200_mid)
        record("get(H200 after pause)", "PASS", f"status={h200_paused.status}")
    except Exception as e:
        record("get(H200 after pause)", "FAIL", str(e))

# Destroy
if h200_mid:
    print("  Destroying H200...")
    try:
        client.instances.destroy(h200_mid)
        record("destroy(H200 europe)", "PASS")
        cleanup_ids.remove(h200_mid)
    except Exception as e:
        record("destroy(H200 europe)", "FAIL", str(e))

# ══════════════════════════════════════════════════════════════════════════════
# 10. VM TEMPLATE LIFECYCLE (europe-01, H100)
# ══════════════════════════════════════════════════════════════════════════════

section("10. VM template lifecycle (europe-01, H100)")

vm_mid = None

# VM requires SSH keys — check first
try:
    vm_ssh_keys = client.ssh_keys.list()
    if not vm_ssh_keys:
        record("VM test", "SKIP", "no SSH keys — skipping VM lifecycle")
    else:
        print(f"\n  Found {len(vm_ssh_keys)} SSH key(s), proceeding with VM test...")
        print("  Creating H100 VM in europe-01... this will take ~3-5 min")
        try:
            vm_inst = client.instances.create(
                gpu_type="H100",
                num_gpus=1,
                template="vm",
                storage=100,
                name="e2e-vm",
            )
            vm_mid = vm_inst.machine_id
            cleanup_ids.append(vm_mid)
            assert vm_inst.status == "Running"
            assert vm_inst.template == "vm", f"template: expected vm, got {vm_inst.template}"
            assert vm_inst.region == "europe-01"
            record(
                "create(VM H100)",
                "PASS",
                f"id={vm_mid}, template={vm_inst.template}, ssh={vm_inst.ssh_command}",
            )
        except Exception as e:
            record("create(VM H100)", "FAIL", f"{type(e).__name__}: {e}")
            traceback.print_exc()

        # Pause — uses templates/vm/pause endpoint
        if vm_mid:
            print("  Pausing VM...")
            try:
                client.instances.pause(vm_mid)
                record("pause(VM)", "PASS")
            except Exception as e:
                record("pause(VM)", "FAIL", str(e))

            print("  Waiting 10s for status to settle...")
            time.sleep(10)

        # Destroy — uses templates/vm/destroy endpoint
        if vm_mid:
            print("  Destroying VM...")
            try:
                client.instances.destroy(vm_mid)
                record("destroy(VM)", "PASS")
                if vm_mid in cleanup_ids:
                    cleanup_ids.remove(vm_mid)
            except Exception as e:
                record("destroy(VM)", "FAIL", str(e))
except Exception as e:
    record("VM test", "FAIL", f"unexpected: {type(e).__name__}: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# 11. AUTO-ROUTED L4 REGION — FULL LIFECYCLE
# ══════════════════════════════════════════════════════════════════════════════

section("11. Auto-routed L4 region — full lifecycle (create -> pause -> resume -> destroy)")

noida_mid = None
noida_region = None

print("\n  Creating L4 with auto-routing... this will take ~1-3 min")
try:
    noida_inst = client.instances.create(
        gpu_type="L4",
        num_gpus=1,
        template="pytorch",
        storage=20,
        name="e2e-noida",
    )
    noida_mid = noida_inst.machine_id
    noida_region = noida_inst.region
    cleanup_ids.append(noida_mid)
    assert noida_inst.status == "Running"
    record(
        "create(L4 auto-route)",
        "PASS",
        f"id={noida_mid}, region={noida_inst.region}",
    )
except Exception as e:
    record("create(L4 auto-route)", "FAIL", f"{type(e).__name__}: {e}")
    traceback.print_exc()

# GET + field verification
if noida_mid:
    try:
        noida_fetched = client.instances.get(noida_mid)
        assert noida_region is not None
        assert noida_fetched.region == noida_region
        assert noida_fetched.gpu_type == "L4", f"gpu_type: expected L4, got {noida_fetched.gpu_type}"
        assert noida_fetched.template == "pytorch"
        assert noida_fetched.is_reserved is not None
        assert noida_fetched.disk_type, "should have disk_type"
        record(
            "get(L4 noida)",
            "PASS",
            f"status={noida_fetched.status}, disk_type={noida_fetched.disk_type}, "
            f"is_reserved={noida_fetched.is_reserved}",
        )
    except Exception as e:
        record("get(L4 noida)", "FAIL", str(e))

# Pause
noida_paused_ok = False
if noida_mid:
    print("  Pausing L4...")
    try:
        client.instances.pause(noida_mid)
        record("pause(L4 noida)", "PASS")
    except Exception as e:
        record("pause(L4 noida)", "FAIL", str(e))

    print("  Waiting 10s for status to settle...")
    time.sleep(10)
    try:
        noida_paused = client.instances.get(noida_mid)
        assert noida_paused.status == "Paused", f"expected Paused, got {noida_paused.status}"
        record("get(L4 after pause)", "PASS", f"status={noida_paused.status}")
        noida_paused_ok = True
    except Exception as e:
        record("get(L4 after pause)", "FAIL", str(e))

# Resume — this tests V2 (synchronous) resume path
noida_old_id = noida_mid
if noida_mid and noida_paused_ok:
    print("  Resuming L4 in noida... ~1-3 min")
    try:
        noida_resumed = client.instances.resume(noida_mid)
        assert noida_resumed.status == "Running"
        assert noida_region is not None
        assert noida_resumed.region == noida_region, f"region: expected {noida_region}, got {noida_resumed.region}"
        noida_mid = noida_resumed.machine_id
        cleanup_ids.append(noida_mid)
        if noida_old_id in cleanup_ids:
            cleanup_ids.remove(noida_old_id)
        record(
            "resume(L4 noida)",
            "PASS",
            f"old_id={noida_old_id}, new_id={noida_mid}, region={noida_resumed.region}",
        )
    except Exception as e:
        record("resume(L4 noida)", "FAIL", f"{type(e).__name__}: {e}")
        traceback.print_exc()

# Destroy
if noida_mid:
    print("  Destroying L4...")
    try:
        client.instances.destroy(noida_mid)
        record("destroy(L4 noida)", "PASS")
        if noida_mid in cleanup_ids:
            cleanup_ids.remove(noida_mid)
    except Exception as e:
        record("destroy(L4 noida)", "FAIL", str(e))

# ══════════════════════════════════════════════════════════════════════════════
# 12. ERROR PATHS
# ══════════════════════════════════════════════════════════════════════════════

section("12. Error paths")

# Pause non-existent instance
try:
    client.instances.pause(999999999)
    record("pause(invalid)", "FAIL", "should have raised error")
except NotFoundError:
    record("pause(invalid)", "PASS", "NotFoundError raised")
except Exception as e:
    record("pause(invalid)", "FAIL", f"wrong error: {type(e).__name__}: {e}")

# Resume non-existent instance
try:
    client.instances.resume(999999999)
    record("resume(invalid)", "FAIL", "should have raised error")
except NotFoundError:
    record("resume(invalid)", "PASS", "NotFoundError raised")
except Exception as e:
    record("resume(invalid)", "FAIL", f"wrong error: {type(e).__name__}: {e}")

# Destroy non-existent instance
try:
    client.instances.destroy(999999999)
    record("destroy(invalid)", "FAIL", "should have raised error")
except NotFoundError:
    record("destroy(invalid)", "PASS", "NotFoundError raised")
except Exception as e:
    record("destroy(invalid)", "FAIL", f"wrong error: {type(e).__name__}: {e}")

# All exceptions inherit from JarvislabsError
try:
    assert issubclass(AuthError, JarvislabsError)
    assert issubclass(NotFoundError, JarvislabsError)
    assert issubclass(ValidationError, JarvislabsError)
    assert issubclass(APIError, JarvislabsError)
    assert issubclass(InsufficientBalanceError, JarvislabsError)
    record("exception hierarchy", "PASS", "all 5 inherit JarvislabsError")
except AssertionError as e:
    record("exception hierarchy", "FAIL", str(e))

# Pause a REAL destroyed instance (saved from section 6)
if destroyed_mid:
    try:
        client.instances.pause(destroyed_mid)
        record("pause(real destroyed)", "FAIL", f"should have raised error for {destroyed_mid}")
    except NotFoundError:
        record("pause(real destroyed)", "PASS", f"NotFoundError for {destroyed_mid}")
    except Exception as e:
        record("pause(real destroyed)", "FAIL", f"unexpected: {type(e).__name__}: {e}")
else:
    record("pause(real destroyed)", "SKIP", "no destroyed_mid from section 6")

# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════════

section("SUMMARY")

passed = sum(1 for _, s, _ in results if s == "PASS")
failed = sum(1 for _, s, _ in results if s == "FAIL")
warned = sum(1 for _, s, _ in results if s == "WARN")

print(f"  Total: {len(results)} tests")
print(f"  PASSED:  {passed}")
print(f"  FAILED:  {failed}")
if warned:
    print(f"  WARNED:  {warned}")

if failed:
    print("\n  Failed tests:")
    for name, status, detail in results:
        if status == "FAIL":
            print(f"    X {name}: {detail}")

if warned:
    print("\n  Warnings:")
    for name, status, detail in results:
        if status == "WARN":
            print(f"    ! {name}: {detail}")

print()

# Retry any IDs still in cleanup_ids (inline destroy failed for these)
if cleanup_ids:
    print(f"\n  ⚠ {len(cleanup_ids)} instance(s) still in cleanup_ids — retrying destroy...")
    for mid in list(cleanup_ids):
        safe_destroy(client, mid)
    cleanup_ids.clear()

client.close()
sys.exit(1 if failed else 0)
