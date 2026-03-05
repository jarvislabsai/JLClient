#!/usr/bin/env bash

set -u
set -o pipefail

INCLUDE_H100_H200=0
KEEP_RESOURCES=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --include-h100-h200)
      INCLUDE_H100_H200=1
      shift
      ;;
    --keep-resources)
      KEEP_RESOURCES=1
      shift
      ;;
    --help|-h)
      cat <<'EOF'
Usage: tests/e2e_cli.sh [OPTIONS]

Run live end-to-end tests against the real JarvisLabs backend using the CLI.

Options:
  --include-h100-h200  Run heavy H100/H200 + VM tests (slow and costly)
  --keep-resources     Do not cleanup created resources at the end
  -h, --help           Show this help message

Notes:
  - Uses plain `jl` commands (no `uv run` prefix).
  - Requires you to be logged in (`jl login`) before running.
EOF
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 2
      ;;
  esac
done

JL_BIN="$(command -v jl || true)"
if [[ -z "$JL_BIN" && -x ".venv/bin/jl" ]]; then
  JL_BIN=".venv/bin/jl"
fi
if [[ -z "$JL_BIN" ]]; then
  echo "Error: jl command not found in PATH." >&2
  echo "Install in editable mode first, then retry." >&2
  exit 1
fi

jl() {
  command "$JL_BIN" "$@"
}

PASS=0
FAIL=0
WARN=0
SKIP=0

declare -a FAILURES=()
declare -a WARNINGS=()
declare -a SKIPS=()
declare -a CLEANUP_INSTANCES=()
declare -a CLEANUP_FILESYSTEMS=()
declare -a CLEANUP_SCRIPTS=()

LAST_OUTPUT=""
LAST_RC=0
TMP_DIR="$(mktemp -d -t jl-e2e-cli-XXXXXX)"
RUN_TAG="e2e-$(date +%Y%m%d%H%M%S)-$RANDOM"

SCRIPT_FILE_1="$TMP_DIR/startup-1.sh"
SCRIPT_FILE_2="$TMP_DIR/startup-2.sh"

cat >"$SCRIPT_FILE_1" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo "hello from e2e script v1" > /tmp/jl-e2e-script.log
EOF

cat >"$SCRIPT_FILE_2" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
echo "hello from e2e script v2" > /tmp/jl-e2e-script.log
EOF

remove_cleanup_instance() {
  local target="$1"
  local -a kept=()
  local value
  for value in "${CLEANUP_INSTANCES[@]}"; do
    if [[ "$value" != "$target" ]]; then
      kept+=("$value")
    fi
  done
  if [[ ${#kept[@]} -eq 0 ]]; then
    CLEANUP_INSTANCES=()
  else
    CLEANUP_INSTANCES=("${kept[@]}")
  fi
}

remove_cleanup_filesystem() {
  local target="$1"
  local -a kept=()
  local value
  for value in "${CLEANUP_FILESYSTEMS[@]}"; do
    if [[ "$value" != "$target" ]]; then
      kept+=("$value")
    fi
  done
  if [[ ${#kept[@]} -eq 0 ]]; then
    CLEANUP_FILESYSTEMS=()
  else
    CLEANUP_FILESYSTEMS=("${kept[@]}")
  fi
}

remove_cleanup_script() {
  local target="$1"
  local -a kept=()
  local value
  for value in "${CLEANUP_SCRIPTS[@]}"; do
    if [[ "$value" != "$target" ]]; then
      kept+=("$value")
    fi
  done
  if [[ ${#kept[@]} -eq 0 ]]; then
    CLEANUP_SCRIPTS=()
  else
    CLEANUP_SCRIPTS=("${kept[@]}")
  fi
}

record_pass() {
  PASS=$((PASS + 1))
  echo "  [PASS] $1"
}

record_fail() {
  FAIL=$((FAIL + 1))
  local line="$1: $2"
  FAILURES+=("$line")
  echo "  [FAIL] $line"
}

record_warn() {
  WARN=$((WARN + 1))
  local line="$1: $2"
  WARNINGS+=("$line")
  echo "  [WARN] $line"
}

record_skip() {
  SKIP=$((SKIP + 1))
  local line="$1: $2"
  SKIPS+=("$line")
  echo "  [SKIP] $line"
}

section() {
  echo
  echo "============================================================"
  echo "$1"
  echo "============================================================"
}

run_raw() {
  LAST_OUTPUT="$("$@" 2>&1)"
  LAST_RC=$?
}

run_expect_success() {
  local label="$1"
  shift
  run_raw "$@"
  if [[ $LAST_RC -eq 0 ]]; then
    record_pass "$label"
  else
    record_fail "$label" "exit=$LAST_RC output=$(echo "$LAST_OUTPUT" | tr '\n' ' ' | cut -c1-200)"
  fi
}

run_expect_failure() {
  local label="$1"
  shift
  run_raw "$@"
  if [[ $LAST_RC -ne 0 ]]; then
    record_pass "$label"
  else
    record_fail "$label" "expected failure but command succeeded"
  fi
}

assert_output_contains() {
  local label="$1"
  local needle="$2"
  if [[ "$LAST_OUTPUT" == *"$needle"* ]]; then
    record_pass "$label"
  else
    record_fail "$label" "missing '$needle'"
  fi
}

assert_output_contains_any() {
  local label="$1"
  local needle_a="$2"
  local needle_b="$3"
  if [[ "$LAST_OUTPUT" == *"$needle_a"* || "$LAST_OUTPUT" == *"$needle_b"* ]]; then
    record_pass "$label"
  else
    record_fail "$label" "missing both '$needle_a' and '$needle_b'"
  fi
}

json_read() {
  local expr="$1"
  local payload
  payload="$(json_payload 2>/dev/null || true)"
  if [[ -z "$payload" ]]; then
    return 1
  fi
  JL_JSON_PAYLOAD="$payload" JL_JSON_EXPR="$expr" python3 - <<'PY'
import json
import os
import sys

expr = os.environ["JL_JSON_EXPR"]
data = json.loads(os.environ["JL_JSON_PAYLOAD"])
value = eval(expr, {"__builtins__": {}}, {"data": data})
if value is None:
    raise SystemExit(1)
if isinstance(value, (dict, list)):
    print(json.dumps(value))
else:
    print(value)
PY
}

json_payload() {
  JL_JSON_TEXT="$LAST_OUTPUT" python3 - <<'PY'
import json
import os
import sys

text = os.environ.get("JL_JSON_TEXT", "")
decoder = json.JSONDecoder()

for i, ch in enumerate(text):
    if ch not in "[{":
        continue
    try:
        obj, _ = decoder.raw_decode(text[i:])
    except Exception:
        continue
    print(json.dumps(obj))
    raise SystemExit(0)

raise SystemExit(1)
PY
}

find_script_id_by_name() {
  local name="$1"
  local payload
  payload="$(json_payload 2>/dev/null || true)"
  if [[ -z "$payload" ]]; then
    return 1
  fi
  JL_JSON_PAYLOAD="$payload" JL_SCRIPT_NAME="$name" python3 - <<'PY'
import json
import os

target = os.environ["JL_SCRIPT_NAME"]
for item in json.loads(os.environ["JL_JSON_PAYLOAD"]):
    if item.get("script_name") == target:
        print(item.get("script_id", ""))
        raise SystemExit(0)
print("")
PY
}

find_ssh_key_id_by_name() {
  local name="$1"
  local payload
  payload="$(json_payload 2>/dev/null || true)"
  if [[ -z "$payload" ]]; then
    return 1
  fi
  JL_JSON_PAYLOAD="$payload" JL_SSH_KEY_NAME="$name" python3 - <<'PY'
import json
import os

target = os.environ["JL_SSH_KEY_NAME"]
for item in json.loads(os.environ["JL_JSON_PAYLOAD"]):
    if item.get("key_name") == target:
        print(item.get("key_id", ""))
        raise SystemExit(0)
print("")
PY
}

filesystem_exists() {
  local fs_id="$1"
  local payload
  payload="$(json_payload 2>/dev/null || true)"
  if [[ -z "$payload" ]]; then
    return 1
  fi
  JL_JSON_PAYLOAD="$payload" JL_FS_ID="$fs_id" python3 - <<'PY'
import json
import os

target = int(os.environ["JL_FS_ID"])
found = any(int(item.get("fs_id", -1)) == target for item in json.loads(os.environ["JL_JSON_PAYLOAD"]))
print("yes" if found else "no")
PY
}

choose_standard_gpu() {
  local exclude_gpu="${1:-}"
  local payload
  payload="$(json_payload 2>/dev/null || true)"
  if [[ -z "$payload" ]]; then
    return 1
  fi
  JL_JSON_PAYLOAD="$payload" JL_EXCLUDE_GPU="$exclude_gpu" python3 - <<'PY'
import json
import os

prefs = ["RTX5000", "A100", "L4", "A5000Pro", "A6000", "A5000", "A100-80GB"]
exclude = os.environ.get("JL_EXCLUDE_GPU", "")
data = json.loads(os.environ["JL_JSON_PAYLOAD"])

candidates: dict[str, int] = {}
for item in data:
    gpu = str(item.get("gpu_type") or "").strip()
    if not gpu or gpu in {"H100", "H200"} or gpu == exclude:
        continue
    free_raw = item.get("num_free_devices", 0)
    try:
        free = int(str(free_raw).strip() or "0")
    except ValueError:
        free = 0
    if free > 0 and gpu not in candidates:
        candidates[gpu] = free

for preferred in prefs:
    if preferred in candidates:
        print(preferred)
        raise SystemExit(0)

if candidates:
    print(sorted(candidates.items(), key=lambda kv: (-kv[1], kv[0]))[0][0])
    raise SystemExit(0)

raise SystemExit(1)
PY
}

wait_for_instance_status() {
  local machine_id="$1"
  local target="$2"
  local timeout_sec="$3"
  local elapsed=0
  local interval=3

  while [[ $elapsed -lt $timeout_sec ]]; do
    run_raw jl --json instance get "$machine_id"
    if [[ $LAST_RC -eq 0 ]]; then
      local status
      status="$(json_read 'data.get("status")' 2>/dev/null || true)"
      if [[ "$status" == "$target" ]]; then
        return 0
      fi
    fi
    sleep "$interval"
    elapsed=$((elapsed + interval))
  done

  return 1
}

cleanup() {
  if [[ $KEEP_RESOURCES -eq 1 ]]; then
    echo
    echo "Cleanup skipped because --keep-resources was set."
    rm -rf "$TMP_DIR"
    return
  fi

  echo
  echo "Running cleanup..."

  local id
  local i

  for (( i=${#CLEANUP_INSTANCES[@]}-1; i>=0; i-- )); do
    id="${CLEANUP_INSTANCES[$i]}"
    jl --yes --json instance destroy "$id" >/dev/null 2>&1 || true
  done

  for (( i=${#CLEANUP_FILESYSTEMS[@]}-1; i>=0; i-- )); do
    id="${CLEANUP_FILESYSTEMS[$i]}"
    jl --yes --json filesystem remove "$id" >/dev/null 2>&1 || true
  done

  for (( i=${#CLEANUP_SCRIPTS[@]}-1; i>=0; i-- )); do
    id="${CLEANUP_SCRIPTS[$i]}"
    jl --yes --json scripts remove "$id" >/dev/null 2>&1 || true
  done

  rm -rf "$TMP_DIR"
}

trap cleanup EXIT

section "0. Preflight"

run_expect_success "jl --version" jl --version
run_raw jl status
if [[ $LAST_RC -eq 0 ]]; then
  record_pass "jl status (auth check)"
elif [[ "${LAST_OUTPUT:-}" == *"Invalid token"* && -n "${JL_API_KEY:-}" ]]; then
  record_warn "auth fallback" "status failed with JL_API_KEY set; retrying without env token"
  unset JL_API_KEY
  run_raw jl status
  if [[ $LAST_RC -eq 0 ]]; then
    record_pass "jl status (auth check, fallback)"
  else
    record_fail "jl status (auth check, fallback)" \
      "exit=$LAST_RC output=$(echo "$LAST_OUTPUT" | tr '\n' ' ' | cut -c1-200)"
  fi
else
  record_fail "jl status (auth check)" "exit=$LAST_RC output=$(echo "$LAST_OUTPUT" | tr '\n' ' ' | cut -c1-200)"
fi

if [[ $LAST_RC -ne 0 ]]; then
  echo
  echo "Authentication failed."
  echo "Run \`jl login\` or set a valid JL_API_KEY, then rerun this script."
  exit 1
fi
run_expect_success "jl --json status" jl --json status
if [[ $LAST_RC -eq 0 ]]; then
  user_id="$(json_read 'data.get("user", {}).get("user_id")' 2>/dev/null || true)"
  if [[ -n "${user_id:-}" ]]; then
    record_pass "status JSON has user_id"
  else
    record_fail "status JSON has user_id" "missing user.user_id"
  fi
fi

section "1. Help + Table Rendering"

run_expect_success "jl --help" jl --help
assert_output_contains "help lists scripts" "scripts"
assert_output_contains "help lists filesystem" "filesystem"

run_expect_success "jl instance --help" jl instance --help
assert_output_contains "instance help lists create" "create"
assert_output_contains "instance help lists resume" "resume"

run_expect_success "table: status" jl status
assert_output_contains "status has Balance" "Balance"

run_expect_success "table: gpus" jl gpus
assert_output_contains "gpus output has GPU" "GPU"

run_expect_success "table: templates" jl templates
assert_output_contains "templates output has Template" "Template"

run_expect_success "table: instance list" jl instance list
assert_output_contains "instance list has ID header" "ID"

run_expect_success "table: ssh-key list" jl ssh-key list
assert_output_contains "ssh-key list has Key" "Key"

run_expect_success "table: scripts list" jl scripts list
assert_output_contains_any "scripts list has expected output" "Script" "No startup scripts found."

run_expect_success "table: filesystem list" jl filesystem list
assert_output_contains_any "filesystem list has expected output" "ID" "No filesystems found."

section "2. Error Paths"

run_expect_failure "instance get invalid id fails" jl instance get 999999999
run_expect_failure "instance pause invalid id fails" jl --yes instance pause 999999999
run_expect_failure "instance resume invalid id fails" jl --yes instance resume 999999999
run_expect_failure "instance destroy invalid id fails" jl --yes instance destroy 999999999
run_expect_failure "instance ssh invalid id fails" jl instance ssh 999999999 --print-command

run_expect_failure "validation: vm template with L4 fails" jl --yes instance create --gpu L4 --template vm
run_expect_failure "validation: filesystem create below min storage fails" \
  jl --yes filesystem create --name "${RUN_TAG}-fs-bad" --storage 10
touch "$TMP_DIR/empty.pub"
run_expect_failure "validation: ssh-key add empty file fails" \
  jl --yes ssh-key add "$TMP_DIR/empty.pub" --name "${RUN_TAG}-empty-key"

section "3. SSH Key Lifecycle"

TEMP_KEY_NAME="${RUN_TAG}-ssh-key"
TEMP_KEY_PATH="$TMP_DIR/e2e-ed25519-key"
TEMP_KEY_ID=""

run_expect_success "ssh-keygen temp keypair" \
  ssh-keygen -t ed25519 -N "" -f "$TEMP_KEY_PATH" -C "$TEMP_KEY_NAME"
run_expect_success "ssh-key add" jl --yes ssh-key add "$TEMP_KEY_PATH.pub" --name "$TEMP_KEY_NAME"
run_expect_success "ssh-key list (json)" jl --json ssh-key list
if [[ $LAST_RC -eq 0 ]]; then
  TEMP_KEY_ID="$(find_ssh_key_id_by_name "$TEMP_KEY_NAME" 2>/dev/null || true)"
  if [[ -n "$TEMP_KEY_ID" ]]; then
    record_pass "ssh-key appears in list with id=$TEMP_KEY_ID"
  else
    record_fail "ssh-key appears in list" "could not find key '$TEMP_KEY_NAME'"
  fi
fi
if [[ -n "$TEMP_KEY_ID" ]]; then
  run_expect_success "ssh-key remove" jl --yes ssh-key remove "$TEMP_KEY_ID"
  run_expect_success "ssh-key list after remove (json)" jl --json ssh-key list
  if [[ $LAST_RC -eq 0 ]]; then
    after_remove_id="$(find_ssh_key_id_by_name "$TEMP_KEY_NAME" 2>/dev/null || true)"
    if [[ -z "$after_remove_id" ]]; then
      record_pass "ssh-key removed successfully"
    else
      record_fail "ssh-key removed successfully" "key still present with id=$after_remove_id"
    fi
  fi
fi

section "4. Script Lifecycle"

SCRIPT_NAME="${RUN_TAG}-script"
SCRIPT_ID=""

run_expect_success "scripts add" jl --yes scripts add "$SCRIPT_FILE_1" --name "$SCRIPT_NAME"
run_expect_success "scripts list (json)" jl --json scripts list

if [[ $LAST_RC -eq 0 ]]; then
  SCRIPT_ID="$(find_script_id_by_name "$SCRIPT_NAME" 2>/dev/null || true)"
  if [[ -n "$SCRIPT_ID" ]]; then
    CLEANUP_SCRIPTS+=("$SCRIPT_ID")
    record_pass "script appears in list with id=$SCRIPT_ID"
  else
    record_fail "script appears in list" "could not find script '$SCRIPT_NAME'"
  fi
fi

if [[ -n "$SCRIPT_ID" ]]; then
  run_expect_success "scripts update" jl --yes scripts update "$SCRIPT_ID" "$SCRIPT_FILE_2"
fi

section "5. Filesystem Lifecycle"

FS_NAME="${RUN_TAG}-fs"
FS_ID=""

run_expect_success "filesystem create (json)" jl --yes --json filesystem create --name "$FS_NAME" --storage 50
if [[ $LAST_RC -eq 0 ]]; then
  FS_ID="$(json_read 'data.get("fs_id")' 2>/dev/null || true)"
  if [[ -n "$FS_ID" ]]; then
    CLEANUP_FILESYSTEMS+=("$FS_ID")
    record_pass "filesystem created with fs_id=$FS_ID"
  else
    record_fail "filesystem create returns fs_id" "missing fs_id in JSON output"
  fi
fi

if [[ -n "$FS_ID" ]]; then
  run_expect_success "filesystem edit +10GB (json)" jl --yes --json filesystem edit "$FS_ID" --storage 60
  if [[ $LAST_RC -eq 0 ]]; then
    NEW_FS_ID="$(json_read 'data.get("fs_id")' 2>/dev/null || true)"
    if [[ -n "$NEW_FS_ID" && "$NEW_FS_ID" != "$FS_ID" ]]; then
      remove_cleanup_filesystem "$FS_ID"
      CLEANUP_FILESYSTEMS+=("$NEW_FS_ID")
      FS_ID="$NEW_FS_ID"
      record_pass "filesystem edit returned new fs_id=$FS_ID"
    else
      record_pass "filesystem edit kept same fs_id=$FS_ID"
    fi
  fi

  run_expect_success "filesystem list (json)" jl --json filesystem list
  if [[ $LAST_RC -eq 0 ]]; then
    fs_found="$(filesystem_exists "$FS_ID" 2>/dev/null || true)"
    if [[ "$fs_found" == "yes" ]]; then
      record_pass "filesystem present in list"
    else
      record_fail "filesystem present in list" "fs_id=$FS_ID not found"
    fi
  fi
fi

section "6. Instance Lifecycle (Standard GPU)"

INSTANCE_ID=""
RESUMED_ID=""
INSTANCE_NAME="${RUN_TAG}-instance"
RENAMED_NAME="${RUN_TAG}-renamed"
RESUMED_NAME="${RUN_TAG}-resumed"
TEST_GPU="RTX5000"

run_expect_success "gpus list (json, lifecycle gpu selection)" jl --json gpus
if [[ $LAST_RC -eq 0 ]]; then
  selected_gpu="$(choose_standard_gpu 2>/dev/null || true)"
  if [[ -n "$selected_gpu" ]]; then
    TEST_GPU="$selected_gpu"
    record_pass "selected lifecycle gpu=$TEST_GPU"
  else
    record_warn "selected lifecycle gpu" "could not auto-pick available GPU, using fallback RTX5000"
  fi
fi

if [[ -z "$SCRIPT_ID" || -z "$FS_ID" ]]; then
  record_fail "instance lifecycle prerequisites" "need both script_id and fs_id"
else
  run_raw \
    jl --yes --json instance create \
      --gpu "$TEST_GPU" \
      --template pytorch \
      --storage 40 \
      --name "$INSTANCE_NAME" \
      --num-gpus 1 \
      --script-id "$SCRIPT_ID" \
      --script-args "--from-e2e --hello world" \
      --fs-id "$FS_ID"
  if [[ $LAST_RC -eq 0 ]]; then
    record_pass "instance create (json, gpu=$TEST_GPU)"
  elif [[ "$LAST_OUTPUT" == *"not available"* ]]; then
    record_warn "instance create capacity" "gpu=$TEST_GPU unavailable; retrying with another available GPU"
    run_raw jl --json gpus
    retry_gpu="$(choose_standard_gpu "$TEST_GPU" 2>/dev/null || true)"
    if [[ -n "$retry_gpu" ]]; then
      TEST_GPU="$retry_gpu"
      run_raw \
        jl --yes --json instance create \
          --gpu "$TEST_GPU" \
          --template pytorch \
          --storage 40 \
          --name "$INSTANCE_NAME" \
          --num-gpus 1 \
          --script-id "$SCRIPT_ID" \
          --script-args "--from-e2e --hello world" \
          --fs-id "$FS_ID"
      if [[ $LAST_RC -eq 0 ]]; then
        record_pass "instance create retry succeeded (gpu=$TEST_GPU)"
      else
        record_fail "instance create retry (gpu=$TEST_GPU)" \
          "exit=$LAST_RC output=$(echo \"$LAST_OUTPUT\" | tr '\n' ' ' | cut -c1-220)"
      fi
    else
      record_fail "instance create retry candidate" "no alternate non-H100/H200 GPU with free devices"
    fi
  else
    record_fail "instance create (json, gpu=$TEST_GPU)" \
      "exit=$LAST_RC output=$(echo \"$LAST_OUTPUT\" | tr '\n' ' ' | cut -c1-220)"
  fi

  if [[ $LAST_RC -eq 0 ]]; then
    INSTANCE_ID="$(json_read 'data.get("machine_id")' 2>/dev/null || true)"
    status="$(json_read 'data.get("status")' 2>/dev/null || true)"
    if [[ -n "$INSTANCE_ID" ]]; then
      CLEANUP_INSTANCES+=("$INSTANCE_ID")
      record_pass "instance create returned machine_id=$INSTANCE_ID"
    else
      record_fail "instance create returns machine_id" "missing machine_id in JSON output"
    fi
    if [[ "$status" == "Running" ]]; then
      record_pass "instance status is Running after create"
    else
      record_warn "instance status after create" "expected Running, got '$status'"
    fi
  fi

  if [[ -n "$INSTANCE_ID" ]]; then
    run_expect_success "instance get (table)" jl instance get "$INSTANCE_ID"
    assert_output_contains "instance get includes name" "$INSTANCE_NAME"
    run_expect_success "instance ssh --print-command" jl instance ssh "$INSTANCE_ID" --print-command
    assert_output_contains "instance ssh command contains ssh" "ssh "
    run_expect_success "instance ssh --json" jl --json instance ssh "$INSTANCE_ID"
    if [[ $LAST_RC -eq 0 ]]; then
      ssh_cmd="$(json_read 'data.get("ssh_command")' 2>/dev/null || true)"
      if [[ "$ssh_cmd" == ssh* ]]; then
        record_pass "instance ssh json has ssh_command"
      else
        record_fail "instance ssh json has ssh_command" "missing/invalid ssh_command"
      fi
    fi

    run_expect_success "instance rename" jl --yes instance rename "$INSTANCE_ID" --name "$RENAMED_NAME"
    run_expect_success "instance get renamed (json)" jl --json instance get "$INSTANCE_ID"
    if [[ $LAST_RC -eq 0 ]]; then
      got_name="$(json_read 'data.get("name")' 2>/dev/null || true)"
      if [[ "$got_name" == "$RENAMED_NAME" ]]; then
        record_pass "instance renamed correctly"
      else
        record_fail "instance renamed correctly" "expected '$RENAMED_NAME', got '$got_name'"
      fi
    fi

    run_expect_success "instance pause" jl --yes instance pause "$INSTANCE_ID"
    if wait_for_instance_status "$INSTANCE_ID" "Paused" 120; then
      record_pass "instance reached Paused status"
    else
      record_warn "instance reached Paused status" "status did not settle to Paused within timeout"
    fi

    run_expect_success "instance resume (json)" \
      jl --yes --json instance resume "$INSTANCE_ID" --name "$RESUMED_NAME" --storage 60 --fs-id "$FS_ID"
    if [[ $LAST_RC -eq 0 ]]; then
      RESUMED_ID="$(json_read 'data.get("machine_id")' 2>/dev/null || true)"
      resumed_status="$(json_read 'data.get("status")' 2>/dev/null || true)"
      if [[ -n "$RESUMED_ID" ]]; then
        if [[ "$RESUMED_ID" != "$INSTANCE_ID" ]]; then
          remove_cleanup_instance "$INSTANCE_ID"
          CLEANUP_INSTANCES+=("$RESUMED_ID")
          record_pass "resume returned new machine_id=$RESUMED_ID"
        else
          record_warn "resume machine_id behavior" "machine_id did not change ($RESUMED_ID)"
        fi
      else
        record_fail "resume returns machine_id" "missing machine_id in JSON output"
      fi
      if [[ "$resumed_status" == "Running" ]]; then
        record_pass "resume returns Running status"
      else
        record_warn "resume returns Running status" "got '$resumed_status'"
      fi
    fi

    if [[ -n "$RESUMED_ID" ]]; then
      run_expect_success "instance get resumed (json)" jl --json instance get "$RESUMED_ID"
      if [[ $LAST_RC -eq 0 ]]; then
        resumed_name="$(json_read 'data.get("name")' 2>/dev/null || true)"
        if [[ "$resumed_name" == "$RESUMED_NAME" ]]; then
          record_pass "resumed instance name updated"
        else
          record_warn "resumed instance name updated" "expected '$RESUMED_NAME', got '$resumed_name'"
        fi
      fi

      run_expect_success "instance destroy" jl --yes instance destroy "$RESUMED_ID"
      remove_cleanup_instance "$RESUMED_ID"
      run_expect_failure "instance get after destroy fails" jl instance get "$RESUMED_ID"
    fi
  fi
fi

section "7. Cleanup Validation"

if [[ -n "$FS_ID" ]]; then
  run_expect_success "filesystem remove" jl --yes filesystem remove "$FS_ID"
  remove_cleanup_filesystem "$FS_ID"
fi

if [[ -n "$SCRIPT_ID" ]]; then
  run_expect_success "scripts remove" jl --yes scripts remove "$SCRIPT_ID"
  remove_cleanup_script "$SCRIPT_ID"
fi

section "8. Optional H100/H200 + VM"

if [[ $INCLUDE_H100_H200 -ne 1 ]]; then
  record_skip "heavy H100/H200 tests" "run with --include-h100-h200 to enable"
else
  H100_ID=""
  H200_ID=""
  VM_ID=""

  run_expect_success "create H100 instance (json)" \
    jl --yes --json instance create --gpu H100 --template pytorch --storage 100 --name "${RUN_TAG}-h100"
  if [[ $LAST_RC -eq 0 ]]; then
    H100_ID="$(json_read 'data.get("machine_id")' 2>/dev/null || true)"
    if [[ -n "$H100_ID" ]]; then
      CLEANUP_INSTANCES+=("$H100_ID")
      record_pass "H100 machine_id=$H100_ID"
      run_expect_success "pause H100" jl --yes instance pause "$H100_ID"
      if wait_for_instance_status "$H100_ID" "Paused" 180; then
        record_pass "H100 reached Paused"
      else
        record_warn "H100 reached Paused" "timeout"
      fi
      run_expect_success "destroy H100" jl --yes instance destroy "$H100_ID"
      remove_cleanup_instance "$H100_ID"
    fi
  fi

  run_expect_success "create H200 instance (json)" \
    jl --yes --json instance create --gpu H200 --template pytorch --storage 100 --name "${RUN_TAG}-h200"
  if [[ $LAST_RC -eq 0 ]]; then
    H200_ID="$(json_read 'data.get("machine_id")' 2>/dev/null || true)"
    if [[ -n "$H200_ID" ]]; then
      CLEANUP_INSTANCES+=("$H200_ID")
      record_pass "H200 machine_id=$H200_ID"
      run_expect_success "destroy H200" jl --yes instance destroy "$H200_ID"
      remove_cleanup_instance "$H200_ID"
    fi
  fi

  run_expect_success "ssh-key list for VM precheck (json)" jl --json ssh-key list
  if [[ $LAST_RC -eq 0 ]]; then
    HAS_KEYS="$(json_read 'len(data)' 2>/dev/null || echo 0)"
    if [[ "${HAS_KEYS:-0}" -gt 0 ]]; then
      run_expect_success "create H100 VM (json)" \
        jl --yes --json instance create --gpu H100 --template vm --storage 100 --name "${RUN_TAG}-vm"
      if [[ $LAST_RC -eq 0 ]]; then
        VM_ID="$(json_read 'data.get("machine_id")' 2>/dev/null || true)"
        if [[ -n "$VM_ID" ]]; then
          CLEANUP_INSTANCES+=("$VM_ID")
          record_pass "VM machine_id=$VM_ID"
          run_expect_success "destroy VM" jl --yes instance destroy "$VM_ID"
          remove_cleanup_instance "$VM_ID"
        fi
      fi
    else
      record_skip "VM H100 path" "no SSH keys available for VM create"
    fi
  fi
fi

section "Summary"

echo "Total checks: $((PASS + FAIL + WARN + SKIP))"
echo "PASS: $PASS"
echo "FAIL: $FAIL"
echo "WARN: $WARN"
echo "SKIP: $SKIP"

if [[ ${#FAILURES[@]} -gt 0 ]]; then
  echo
  echo "Failures:"
  printf '  - %s\n' "${FAILURES[@]}"
fi

if [[ ${#WARNINGS[@]} -gt 0 ]]; then
  echo
  echo "Warnings:"
  printf '  - %s\n' "${WARNINGS[@]}"
fi

if [[ ${#SKIPS[@]} -gt 0 ]]; then
  echo
  echo "Skipped:"
  printf '  - %s\n' "${SKIPS[@]}"
fi

if [[ $FAIL -gt 0 ]]; then
  exit 1
fi
