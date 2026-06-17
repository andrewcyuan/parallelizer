from __future__ import annotations


def manager_prompt(task_prompt: str, interval: int) -> str:
    task = task_prompt.strip() or "No task prompt was provided. Ask the user what to coordinate."
    return f"""You are running as the Parallelizer manager agent.

Your job is to coordinate subagents for this repository without replacing the user's coding tools.

Task:
{task}

Workflow:
1. Discuss and confirm a decomposition with the user before spawning subagents.
2. Spawn subagents with `plr sub <name> "<prompt>" --background`.
3. Use clear, specific worktree names.
4. Check progress with `plr ls`.
5. Sleep for {interval} seconds between monitoring rounds unless the user asks for a different cadence.
6. When an agent is done, inspect its worktree and log path before summarizing.
7. If an agent is blocked, errored, or needs input, bring that to the user with the worktree name and relevant path.
8. Do not merge or delete worktrees unless the user explicitly asks.

Keep the user focused on decisions and blockers rather than raw logs."""


def setup_plr_prompt(user_prompt: str) -> str:
    extra = user_prompt.strip() or "No additional setup guidance was provided."
    return f"""You are setting up Parallelizer for this repository.

Inspect the project and create or update `.parallelizer/functions.sh` so new worktrees can be prepared automatically.

Requirements:
- Define a shell function named `setup_environment`.
- `setup_environment` receives the worktree allocation number as its first argument.
- Keep the setup project-specific and minimal.
- Install dependencies and start only the services this project normally needs for subagent work.
- Use the allocation number for ports or other per-worktree resources when useful.
- Create `.parallelizer/local_config.json` only if this repo genuinely needs local overrides.
- Do not change `~/.parallelizer/global_config.json`; that is handled by `plr init`.

Additional user guidance:
{extra}

After editing, explain exactly what setup does and any assumptions the user should verify."""


def plr_instructions_markdown() -> str:
    return """## Parallelizer (`plr`)

Use `plr` to create isolated git worktrees for coding agents. Prompts can be passed as command arguments or piped on stdin.

Commands:
- `plr init`: initialize the global default coding agent (`codex` or `claude`).
- `plr tree [name] [prompt]`: create a git worktree without starting an agent.
- `plr sub [name] [prompt]`: create a worktree, run setup, then start a coding agent.
- `plr subagent [name] [prompt]`: same as `plr sub`.
- `plr sub ... --background`: start the agent in the background and write its log path.
- `plr sub ... --model MODEL --agent-arg ARG`: pass a model or extra raw argument to `codex`/`claude`.
- `plr ls`: list Parallelizer worktrees and their agent status.
- `plr cd [name]`: print a worktree path, suitable for `cd $(plr cd name)`.
- `plr open [name]`: open a worktree in a new tmux pane.
- `plr agent manager [prompt]`: start a manager agent in the current repo to coordinate subagents.
- `plr agent setup_plr [guidance]`: start an agent that creates or updates `.parallelizer/functions.sh`.
- `plr instructions`: print these project instructions as markdown.

Environment setup:
- If `.parallelizer/functions.sh` exists, `plr` sources it inside each new worktree.
- That file must define `setup_environment()`.
- `setup_environment` receives the allocated worktree number as `$1`.
- Use that number for per-worktree resources such as ports, database names, or temp directories.
- `plr` runs `setup_environment` after creating the worktree and before starting the agent.
- If the function is missing or returns a non-zero status, setup fails and the worktree is marked as an error.
"""
