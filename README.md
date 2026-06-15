# Parallelizer

Parallelizer is a tool that lets a coding agent spawn subagents that have their own coding environments.

Parallelizer also comes with an MCP server that you can easily add to codex or claude.

## Philosophy
1. __Parallelizer should not recreate your current tools. It should work seamlessly with them.__

And by "your current tools", i mean, of course, my current tools. So, `codex`, `claude`, git worktrees, tmux.

2. __Parallelizer should be project agnostic as much as possible.__

One of the great things about git and coding agents is that they work with every project. I have done my best to make parallelizer work with every project, by leaving the specifics of environment setup up to the user.

## Usage

### Create a setup script for the given repo. We'll use a node project as an example.
```bash
# .parallelizer/functions.sh
# Write a setup function.
setup_environment() {
	npm i

	npm run dev --port $((3000 + $1)) # first argument is the current worktree number
}
```

### Spawn a new subagent (or have your coding agent do it)
```bash
plr subagent worker <prompt> <tree_name>
# Creates new worktree
# cds into new worktree
# Calls setup_environment()
# Initializes coding agent

plr tree <prompt> <tree_name>
# Does the same thing as subagent, but doesn't start running an agent.

# Or call using a redirect! My favorite!
cat FEATURE_PLAN_1.md | plr sub "feature-1"
```

### Monitor agents
```bash
plr ls # Basically just git worktree list + agent status (running, awaiting input, error, done)
plr cd [name] # without the name, will give you an interactive list to pick from like fzf
```

### Managing agents' work
`plr` deliberately does not provide special ways to manage agents' work. We do not want to replicate or the existing coding agent. Instead, simply manage your worktrees (or tell your agent to do it). `plr` will kill agents when the tree has been merged.

What `plr` does have is some handy shortcuts.

```bash
plr agent manager <prompt> --interval <interval-seconds>
cat BIG_TASK_PLAN.md | plr agent manager 
# opens an agent with the instructions and tells it:
# Discuss/confirm with the user how to break the given task down using subagents.
# Check in on agents using plr ls every <interval> seconds using sleep calls, flag the user when there are agents that need help!

plr agent setup_plr # Opens an agent to set up the per repo environment config.
```

__Tmux helpers__
```bash
plr open [worktree_name] # Leave blank to get a selector, like fzf
# Opens the worktree in a new tmux pane! Really only makes sense for agent usage; otherwise you would just use plr cd
```


Optional overrides for project. Mirrors the global config.

```json
// .parallelizer/local_config.json
{
	"default_coding_agent": "claude"
}
```


## Global config
Should be set up in a wizard upon installation. Or go through it again with:
``

```json
# ~/.parallelizer/global_config.json

{
	"default_coding_agent": "codex", //codex, claude supported
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

[ ] Call the subagent with model settings: `plr sub "complete task xyz" --model 'claude-fable-fast'`
[ ] Make tmux helpers generic and then map to other multiplexers, such as kitty












