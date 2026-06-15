# App TODO List

## Implemented in v1
- [x] Package the project with a `plr` console script.
- [x] Create git worktrees under `~/.parallelizer/worktrees/<project-slug>/`.
- [x] Load global and local JSON config.
- [x] Run `.parallelizer/functions.sh` via `setup_environment <number>`.
- [x] Start background subagents through a runner that records exit status.
- [x] Provide `plr tree`, `plr sub`, `plr subagent`, `plr ls`, and `plr cd`.
- [x] Provide FastMCP tools for core lifecycle operations.
- [x] Add tests for config, setup, background status, and setup errors.
- [x] Add a uv-runnable installer script for putting `plr` on PATH.
- [x] Add `plr init` for one-time global default-agent config.
- [x] Implement `plr agent manager` as an interactive coordinator agent.
- [x] Implement `plr agent setup_plr` as an interactive repo setup agent.
- [x] Implement `plr open` tmux split-pane helper.
- [x] Add model and passthrough flags for agent invocation.
- [x] Add fzf-backed selector with numbered fallback for `plr cd` and `plr open`.
- [x] Isolate tmux behavior behind helper code so non-tmux multiplexers can be added later.

## Future
- [ ] Add non-tmux multiplexer backends, such as kitty.
- [ ] Add automatic cleanup for merged/deleted worktrees.
- [ ] Add richer status detection for agents awaiting input.
