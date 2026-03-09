# Codex Agent JL CLI Failures

## Scope

This note captures failures and friction discovered while using the current JL CLI as an actual research agent:

- creating and reusing real JarvisLabs GPU instances
- uploading folders and scripts
- starting detached managed runs
- polling run status and logs
- monitoring GPU usage during training
- stopping runs

The goal here is not to restate the design. It is to document what still feels rough or unreliable when an agent is driving the system for real.

## What worked well

- `jl instance exec --json` is a strong primitive for agents. It returns `exit_code`, `stdout`, and `stderr`, which makes it easy to script setup checks and diagnostics.
- The current split between `instance exec` and `run` is directionally right for agent workflows.
- Folder-based `jl run` with a stable remote path is the right substrate for iterative experiments.
- Detached managed runs are much easier for an agent to handle than tmux sessions.
- `jl run logs --tail N` is exactly the right polling shape for agents because it avoids dumping an entire training log into context every time.
- Monitoring with `nvidia-smi` during a live run works cleanly through `jl instance exec`.
- SSH hardening was missing, but after adding timeout / batch / keepalive options to the execution path, the basic agent control plane is much safer.

## Failures and friction found

### 1. Fresh `jl run --gpu ...` still has a setup gap

The biggest practical failure for real users and agents is still dependency bootstrap on fresh instances.

Example failure mode:

- `jl run /tmp/jl_lora_single.py --gpu RTX5000 ...`
- run starts correctly
- remote script fails with `ModuleNotFoundError` for `transformers` / `peft` / `accelerate`

Current workaround:

- resume or reuse the created instance
- run `jl instance exec <id> -- pip install ...`
- rerun with `--on <id>`

Why this matters:

- this breaks the “just run my script on a GPU” promise for fresh machines
- agents can recover, but the happy path is not self-sufficient yet

What would help:

- a simple `--setup "pip install -r requirements.txt"` on `jl run`
- or a narrower `--requirements requirements.txt`

### 2. `jl run list --refresh` is too slow for agent polling

Measured live runtime with accumulated local runs was about 29.6 seconds.

Why this happens:

- the refresh path is serial and does best-effort remote status checks across saved runs

Why this matters:

- agents should not use this in a polling loop
- humans will perceive it as sluggish once local run history grows

Current mitigation:

- default `jl run list` is local-only and fast
- `jl run status <run_id>` is the right precise check

Recommendation:

- keep telling agents to use `run status`, not `run list --refresh`
- later, parallelize refresh or cap the refreshed set

### 3. Re-following logs replays history from the beginning

Current `jl run logs <run_id> --follow` behavior prints the existing log from the top and then follows.

Observed effect:

- after detaching and reconnecting, the user sees the full historical log replayed again
- this is noisy for humans and wastes context for agents

Why this matters:

- agents usually want “what changed since last time?”, not “show me the full log again”
- long logs will become expensive to inspect repeatedly

Recommendation:

- keep current default `logs` behavior for full history
- make `--follow` start near the end by default
- or add an explicit `--from-start` if full replay is needed

### 4. Run stop/status can race near completion

Observed live behavior on run `r_00050e7b`:

- `jl run stop r_00050e7b` returned `stop_status: not-running`
- immediate `jl run status r_00050e7b` still reported `running`
- remote inspection showed the PID file existed, the process was gone, and the exit code file already contained `0`

Interpretation:

- the run had essentially finished
- local status and stop logic were briefly out of sync with the remote terminal state

Why this matters:

- agents need the state machine to be trustworthy near completion boundaries
- “not-running” plus “running” in back-to-back commands feels inconsistent

Recommendation:

- treat `exit_code` presence as stronger than stale liveness inference
- when stop sees no live PID, check exit code before reporting “not-running”

### 5. Fresh-run lifecycle after disconnect is still unresolved

This is a known design gap, but it remains a real failure mode.

If a fresh managed run is launched with a lifecycle policy and the client disconnects:

- the workload can keep running
- but post-completion pause/destroy is not guaranteed after the client disappears

Why this matters:

- the current model is good enough for v0 usage
- it is not yet fully trustworthy as an unattended “fire-and-forget” agent workflow for fresh instances

Recommendation:

- keep this explicit in docs and output
- do not overpromise automatic cleanup until there is a remote watchdog or backend support

### 6. Folder-run UX is much better with `rsync`, but repeated sync semantics still need to be watched

The switch from recursive `scp` to `rsync` for folder targets was the right move.

However, this introduces agent-relevant behavior to keep an eye on:

- `--delete` means remote-only files in the synced project tree can disappear
- users may not always realize the remote project tree is being made to match local contents

This is probably still the right default for the current product direction, but it is worth documenting clearly.

### 7. Run history is local-only and local-first

This is acceptable for v0, but it is an actual limitation.

Implications:

- `jl run list` only knows about runs started from the same local machine
- another agent or workstation cannot discover those runs unless it has the same local record store

Why this matters:

- for personal use, this is fine
- for multi-agent or multi-machine workflows, this is not enough

Recommendation:

- keep v0 local-only
- document it plainly
- treat backend/global run indexing as a later capability

## Agent workflow judgement

The current primitive set is fundamentally the right one for agents:

- local edit
- sync folder
- start detached run
- poll `status` and `logs --tail`
- use `instance exec` for side-channel diagnostics like `nvidia-smi`
- download artifacts

This is better than the older tmux-heavy skill pattern for agents because:

- there is less terminal-state ambiguity
- logs and status are addressable by run ID
- JSON output is available where it matters

So the main issue is not the core architecture. The main issue is a handful of sharp edges around setup, reconnection UX, and state transitions.

## Practical recommendations

If we want the next highest-impact improvements for agent workflows without turning the CLI into a circus:

1. Add a simple fresh-run setup hook (`--setup` or `--requirements`).
2. Make `logs --follow` resume from the end by default.
3. Tighten stop/status reconciliation around exit-code detection.
4. Keep telling agents to prefer `run status` and `logs --tail` over `run list --refresh`.
5. Be explicit that fresh-run lifecycle after disconnect is best-effort until backend or remote-watchdog support exists.

## Bottom line

The current JL CLI is already a better agent substrate than the old experiment skill.

The right model is in place:

- `instance exec` for immediate commands
- folder sync for project iteration
- managed detached runs for training
- run-oriented logs and status for reconnection

The failures found are mostly product polish and state-management issues, not evidence that the core architecture is wrong.
