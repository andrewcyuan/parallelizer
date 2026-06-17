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
   If your tool environment supports sandbox escalation, request/use escalation for `plr sub`, `plr tree`, `plr merge`, and `plr rm` so worktree creation, setup, and cleanup do not fail on sandbox restrictions.
3. Use clear, specific worktree names.
4. Check progress with `plr ls`.
5. Use shell sleep commands to wait between monitoring rounds, for example `sleep {interval}`, unless the user asks for a different cadence.
6. When an agent is done, inspect its worktree and log path before summarizing.
7. If an agent is blocked, errored, or needs input, bring that to the user with the worktree name and relevant path.
8. Do not merge or remove worktrees unless the user explicitly asks.
9. When the user asks to merge completed work, use `plr merge <name>` from the target branch.
10. If a merge fails, report the Git error and leave the worktree for the user or another agent to resolve.
11. When the user asks to discard or clean up a worktree without merging, use `plr rm <name>`.

Keep the user focused on decisions and blockers rather than raw logs."""


def setup_plr_prompt(user_prompt: str) -> str:
    extra = user_prompt.strip() or "No additional setup guidance was provided."
    return f"""You are setting up Parallelizer for this repository.

Inspect the project and create or update `.parallelizer/functions.sh` so new worktrees can be prepared automatically.

Requirements:
- Define a shell function named `setup_environment`.
- `setup_environment` receives the worktree allocation number as its first argument.
- Optionally define `cleanup_environment` when setup creates side effects that need teardown.
- `cleanup_environment` receives the same allocation number as its first argument.
- Keep cleanup idempotent where practical; `plr rm` and successful `plr merge` run it before removing the worktree.
- Keep the setup project-specific and minimal.
- Install dependencies and start only the services this project normally needs for subagent work.
- Use the allocation number for ports or other per-worktree resources when useful.
- Use `PLR_SOURCE_REPO` for files from the original repo, such as copying `.env` into the worktree when needed.
- `PLR_WORKTREE` is also available and points to the current worktree path.
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
- `plr cd [name]`: start a shell in a worktree.
- `plr open [name]`: open a worktree in a new tmux pane.
- `plr rm NAME`: run optional cleanup and remove a Parallelizer worktree.
- `plr rm NAME --force`: continue removal if cleanup fails; Git still protects dirty worktrees.
- `plr merge NAME`: merge the worktree branch into the current branch, then remove the worktree.
- `plr merge NAME --no-ff`: force a merge commit.
- `plr merge NAME --squash`: squash the worktree branch into the current branch.
- `plr agent manager [prompt]`: start a manager agent in the current repo to coordinate subagents.
- `plr agent setup [instructions]`: start an agent that creates or updates `.parallelizer/functions.sh`.
- `plr instructions`: print these project instructions as markdown.

Subagent coordination:
- When running as a manager, spawn workers with `plr sub <name> "<prompt>" --background`.
- If your tool environment supports sandbox escalation, request/use escalation for worktree lifecycle commands such as `plr sub`, `plr tree`, `plr merge`, and `plr rm`; these commands create external worktrees and run setup/cleanup.
- Monitor workers periodically with `plr ls`, then use shell sleep commands such as `sleep 10` between monitoring rounds.
- Inspect completed workers by checking their worktree and log path before summarizing results.

Environment setup:
- If `.parallelizer/functions.sh` exists, `plr` sources it inside each new worktree.
- That file must define `setup_environment()`.
- `setup_environment` receives the allocated worktree number as `$1`.
- Use that number for per-worktree resources such as ports, database names, or temp directories.
- Setup runs from the new worktree.
- `PLR_SOURCE_REPO` points to the original repo path.
- `PLR_WORKTREE` points to the current worktree path.
- Use `PLR_SOURCE_REPO` to copy local files such as `.env` when the project needs them.
- `plr` runs `setup_environment` after creating the worktree and before starting the agent.
- If the function is missing or returns a non-zero status, setup fails and the worktree is marked as an error.
- The same file may optionally define `cleanup_environment()`.
- `cleanup_environment` receives the same allocation number as `$1`.
- Cleanup runs from the worktree and receives the same `PLR_SOURCE_REPO` and `PLR_WORKTREE` variables.
- `plr rm` and successful `plr merge` run `cleanup_environment` before removing the worktree.
- If cleanup fails, removal stops unless `--force` was passed.
"""
