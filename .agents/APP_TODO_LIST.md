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

## Deferred
- [ ] Config wizard.
- [ ] `plr agent manager`.
- [ ] `plr agent setup_plr`.
- [ ] `plr open` tmux helper.
- [ ] Model/settings flags for agent invocation.
- [ ] Generic multiplexer support beyond tmux.
- [ ] More polished interactive selector for `plr cd`.
