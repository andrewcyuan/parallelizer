# Parallelizer Technical Spec

## Product Shape
Parallelizer is a thin orchestration layer around tools the user already uses: git worktrees, coding-agent CLIs, optional shell setup scripts, and later tmux-style multiplexers. It should not become a code review system, merge manager, task tracker, or replacement UI for Codex/Claude.

The first implementation slice is intentionally local-first:
- A `plr` CLI for manual and agent-driven use.
- A FastMCP stdio server exposing the same core lifecycle behavior to coding agents.
- Git worktrees as the isolation primitive.
- JSON config/state files for simple durability and inspectability.

## Package And Runtime
- The project is packaged as a Python application with a `plr` console script.
- Python requirement is `>=3.10` because the MCP/FastMCP dependency requires Python 3.10+.
- CLI parsing uses `typer`.
- MCP server uses `mcp.server.fastmcp.FastMCP` and must run over stdio.
- Prefer stdlib for implementation details: `pathlib`, `subprocess` with argument lists, `json`, `hashlib`, and `datetime`.
- Keep command execution boring and explicit. Avoid shell strings except where shell semantics are required, such as sourcing `.parallelizer/functions.sh`.

## Config
Parallelizer reads config from two JSON files, in order:
1. `~/.parallelizer/global_config.json`
2. `<repo>/.parallelizer/local_config.json`

Local config overrides global config. Nested dictionaries merge recursively so local agent overrides do not erase unrelated defaults.

Supported v1 keys:
- `default_coding_agent`: defaults to `codex`.
- `worktree_root`: defaults to `~/.parallelizer/worktrees`.
- `agents`: maps agent names to `interactive` and `background` command templates.

Default command templates:
- Codex interactive: `["codex", "--cd", "{worktree}", "{prompt}"]`
- Codex background: `["codex", "exec", "--cd", "{worktree}", "{prompt}"]`
- Claude interactive: `["claude", "{prompt}"]` with cwd set to the worktree.
- Claude background: `["claude", "-p", "{prompt}"]` with cwd set to the worktree.

Template variables:
- `{worktree}`: absolute worktree path.
- `{prompt}`: full prompt text.

## Project Identity And Paths
Parallelizer must be runnable from anywhere inside a git repository. It resolves the source repo with `git rev-parse --show-toplevel`.

Worktrees are stored outside the project by default:
- `~/.parallelizer/worktrees/<project-slug>/<tree-name>`

The project slug is:
- sanitized source repo basename
- plus a short hash of the source repo absolute path

This avoids collisions when multiple repos have the same basename.

State is stored per project:
- `~/.parallelizer/state/<project-slug>.json`

Logs are stored per project and tree:
- `~/.parallelizer/logs/<project-slug>/<tree-name>.log`

## Worktree Naming And Branches
Tree names may be explicitly provided or auto-allocated.

Rules:
- Explicit names are sanitized to filesystem/branch-safe characters.
- Empty sanitized names are rejected.
- Duplicate names for the same project are rejected.
- Auto names use `worker-1`, `worker-2`, etc.
- Each tree gets a project-local allocation number from state.
- Git branches use `plr/<tree-name>`.

Creation uses:
- `git worktree add -b plr/<tree-name> <path> HEAD`

If worktree creation fails, surface the git error directly enough for a user to fix it.

## Setup Hook
After creating a worktree, Parallelizer looks for:
- `<worktree>/.parallelizer/functions.sh`

If missing:
- setup is skipped.
- the record remains usable with `setup_status = "skipped"`.

If present:
- source the file with bash.
- require a shell function named `setup_environment`.
- call `setup_environment <allocation-number>`.

Expected command shape:
- `bash -lc 'source .parallelizer/functions.sh; ...; setup_environment "$1"' parallelizer-setup <number>`

Failure behavior:
- If the file exists but `setup_environment` is not declared, fail with a descriptive error.
- If setup exits nonzero, fail with stderr/stdout context.
- Preserve the created worktree for inspection.
- Persist metadata with `setup_status = "error"` and `status = "error"`.

## State Model
State is a single JSON object with:
- `next_number`: next project-local allocation number.
- `trees`: object keyed by tree name.

Each tree record stores:
- `name`
- `source_repo`
- `worktree_path`
- `branch`
- `allocation_number`
- `prompt_summary`
- `agent`
- `mode`
- `pid`
- `log_path`
- `status`
- `setup_status`
- `setup_error`
- `created_at`
- `updated_at`
- `exit_code`

Timestamps are UTC ISO strings.

JSON state should remain human-inspectable. Atomic writes should use a temporary file followed by replace.

## Agent Modes
Manual CLI usage and agent/MCP usage have different execution needs.

Interactive mode:
- Used by direct `plr sub` / `plr subagent` unless `--background` is passed. These are aliases for the same command.
- Create worktree.
- Run setup.
- Record selected agent and mode.
- `chdir`/exec into the configured interactive agent command.
- The current terminal becomes the agent session.

Background mode:
- Used by MCP and explicit `--background`.
- Create worktree.
- Run setup.
- Start a detached Parallelizer runner process.
- Runner starts the configured agent command with cwd set to the worktree.
- Runner redirects stdout/stderr to the tree log file.
- Runner updates state with final exit code and status.

The background process recorded in state is the runner pid, not necessarily the final agent pid. This gives `plr ls` a process that can be polled and lets the runner record reliable completion status.

## Status Semantics
`plr ls` combines persisted state with `git worktree list --porcelain` and process polling.

Statuses:
- `running`: pid exists and process is alive.
- `done`: background runner recorded exit code `0`.
- `error`: setup failed, spawn failed, runner recorded nonzero exit, or pid disappeared without an exit code.
- `no-agent`: worktree exists but no agent has been started.
- `missing`: state exists but the worktree path no longer exists and git no longer lists it.

Do not delete worktrees or state automatically in v1.

## CLI Contract
Implemented commands:
- `plr tree [name] [prompt...]`
- `plr sub [name] [prompt...]`
- `plr subagent [name] [prompt...]` as an alias for `plr sub`
- `plr ls`
- `plr cd [name]`

Prompt handling:
- If prompt arguments are present, join them with spaces.
- If no prompt arguments are present and stdin is piped, read stdin.
- `sub` and its `subagent` alias require a non-empty prompt.
- `tree` may accept an empty prompt.

`plr tree`:
- Creates and sets up a worktree.
- Does not start an agent.
- Prints the worktree path.

`plr sub` / `plr subagent`:
- Are the same command. `sub` is the short form, `subagent` is the descriptive form.
- Creates and sets up a worktree.
- Starts the selected agent.
- Defaults to interactive mode.
- Supports `--background` for agent/MCP-style background execution.
- Supports `--agent` to override the configured default agent.

`plr ls`:
- Prints a compact table with name, status, agent, pid, branch, path, and log.

`plr cd [name]`:
- Prints a worktree path suitable for command substitution, e.g. `cd "$(plr cd worker-1)"`.
- If no name is provided, v1 may use a simple numbered terminal selector.

Deferred commands should exist as explicit placeholders where helpful:
- `plr open`
- `plr agent manager`
- `plr agent setup_plr`

Placeholders should fail clearly with "not implemented yet" messaging.

## MCP Contract
The MCP server must expose only core lifecycle tools in v1. It should not expose manager-agent routes.

Tools:
- `create_subagent(prompt, name=None, agent=None)`
- `create_tree(prompt=None, name=None)`
- `list_worktrees()`
- `open_worktree_info(name)`

MCP `create_subagent` always uses background mode. It returns structured JSON including name, path, branch, pid, status, and log path.

MCP `open_worktree_info` does not open a terminal. It returns information an agent can use, including the path and a recommended `cd` command.

## Testing Expectations
Test the shared service layer first so CLI and MCP behavior stay aligned.

Minimum scenarios:
- Config precedence and recursive merge.
- Project slug and name allocation.
- Successful worktree creation in a temporary git repo.
- Setup skipped when no setup file exists.
- Setup success with `setup_environment <number>`.
- Setup failure when `setup_environment` is missing.
- Background agent success updates status to `done`.
- Background agent nonzero exit updates status to `error`.
- `plr ls` status refresh behavior.
- MCP tool handlers call the same service layer as CLI commands.

Use temporary git repositories for integration tests. Keep test worktree roots and HOME redirected into temporary directories.

## Deferred Work
These are intentionally outside the v1 implementation:
- Config wizard.
- Manager agent workflow.
- `plr agent setup_plr`.
- Tmux helpers via `plr open`.
- Model/settings flags for agent invocation.
- Generic multiplexer abstraction.
- Automatic cleanup of merged/deleted worktrees.
- Rich interactive selector for `plr cd`.
