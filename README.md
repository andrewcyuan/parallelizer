# Parallelizer

Parallelizer is a tool that lets a coding agent spawn subagents that have their own coding environments.

Parallelizer also comes with an MCP server that you can easily add to codex or claude.

## Install

From this repo:

```bash
uv run scripts/install_plr.py
```

This installs the `plr` command with `uv tool install --editable`. If uv's tool bin directory is not on your `PATH`, the script will print the command to add it:

```bash
uv tool update-shell
```

To let the installer run that PATH update for you:

```bash
uv run scripts/install_plr.py --update-shell
```

Then initialize the global default agent once:

```bash
plr init
# or
plr init --agent claude
```

## Philosophy
1. __Parallelizer should not recreate your current tools. It should work seamlessly with them.__

And by "your current tools", i mean, of course, my current tools. So, `codex`, `claude`, git worktrees, `tmux`.

2. __Parallelizer should be project agnostic as much as possible.__

One of the great things about git and coding agents is that they work with every project. I have done my best to make parallelizer work with every project, by leaving the specifics of environment setup up to the user.

## Usage

### Add CLI instructions to a project
If you do not want to configure the MCP server, print the agent-facing CLI instructions and append them to your project instructions:

```bash
plr instructions >> AGENTS.md

plr instructions >> CLAUDE.md
```

### Create a setup script for the given repo. We'll use a node project as an example.
```bash
# .parallelizer/functions.sh
# Write a setup function.
setup_environment() {
	if [ -f "$PLR_SOURCE_REPO/.env" ] && [ ! -f ".env" ]; then
		cp "$PLR_SOURCE_REPO/.env" ".env"
	fi

	npm i

	npm run dev --port $((3000 + $1)) # first argument is the current worktree number
}

# Optional. Runs before `plr rm` removes the worktree.
cleanup_environment() {
	npm run stop-dev -- --port $((3000 + $1))
}
```

`setup_environment` and `cleanup_environment` run from the worktree. `$1` is the allocation number, `PLR_SOURCE_REPO` points to the original repo, and `PLR_WORKTREE` points to the current worktree. Parallelizer does not copy `.env` automatically because each project may need different secret handling, but the example above covers the common case.

You can also have an agent do this for you!

```bash
plr agent setup "use port offsets" # Opens an agent to create/update .parallelizer/functions.sh for this repo.
```


### Spawn a new subagent (or have your coding agent do it)
```bash
plr subagent worker "Complete task xyz."
# `plr sub` is the same command, just shorter.

# Creates new worktree
# cds into new worktree
# Calls setup_environment()
# Initializes coding agent

plr tree worker "Complete task xyz."
# Does the same thing as subagent, but doesn't start running an agent.

# Or call using a redirect! My favorite!
cat FEATURE_PLAN_1.md | plr sub "feature-1"

# Agent/MCP usage can run in the background.
plr sub worker "Complete task xyz." --background

# Optional model and extra agent args for codex/claude.
plr sub worker "Complete task xyz." --model gpt-5 --agent-arg=--search
```

### Monitor agents
```bash
plr ls # Basically just git worktree list + agent status (running, awaiting input, error, done)
plr cd [name] # starts a shell in the worktree; without the name, opens an interactive selector
```

__Tmux helpers__
```bash
plr open [worktree_name] # Leave blank to get a selector, like fzf
# Opens the worktree in a new tmux pane when run inside tmux.
```

### Worktree lifecycle
`plr` lifecycle management contains two parts: environment setup/teardown, and worktree management.

```bash
plr merge worker
plr merge worker --no-ff
plr merge worker --squash
# Merges the worktree branch into the current branch, then runs cleanup and removes the worktree.

plr rm worker
plr rm worker --force
# Runs cleanup_environment() if it exists, then removes the worktree.
# --force only continues after cleanup failure; Git still protects dirty worktrees.
```
`plr rm` refuses to remove worktrees with running background agents.


### Manager agent
With many parallel agents, it can be helpful to have a manager agent to keep them all chugging along!

```bash
plr agent manager <prompt> --interval <interval-seconds>
cat BIG_TASK_PLAN.md | plr agent manager 
# opens an agent with the instructions and tells it:
# Discuss/confirm with the user how to break the given task down using subagents.
# Check in on agents using plr ls every <interval> seconds using sleep calls, flag the user when there are agents that need help!
```


### Optional overrides for project. Mirrors the global config.

```json
// .parallelizer/local_config.json
{
	"default_coding_agent": "claude"
}
```


## Global config
Should be set up in a wizard upon installation. Or go through it again with:

```bash
plr init
```

```json
{
	"default_coding_agent": "codex",
	"worktree_root": "~/.parallelizer/worktrees"
}

```

## MCP Server usage
Features excluded from the mcp:
* The `plr agent` routes. These are meant to be used as top level. Middle management works badly enough in the human world.

```bash
# Codex
codex mcp add parallelizer -- python <PATH_TO_REPO>/mcp_server.py

# Claude
claude mcp add --transport stdio parallelizer -- python <PATH_TO_REPO>/mcp_server.py
```


## Future features

[ ] Make tmux helpers generic and then map to other multiplexers, such as kitty



