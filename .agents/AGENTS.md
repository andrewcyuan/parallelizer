# Agent rules

## App design and philosophy
Refer to the README.md.

## Coding rules
- No more than 100 lines per function unless the function has a prompt.
- Use pathlib for building paths rather than raw strings.
- Avoid terminal magic. Again, this should be a pretty simple app that doesn't need anything crazy in that department.
- Assume commands will fail and keep that in mind with control flow.
- Use shared helpers whenever possible; this helps reduce the code used for potential error handling.
- use listed arguments in subprocess rather than raw terminal strings when possible
- Use minimal, directed logs to help the user and developer.

## Project management
- Keep a simple global checklist of tasks in APP_TODO_LIST.md.


## Packages
- Use `typer` for the argument parsing.
- Use `fastMCP` for the mcp server and make sure it builds to a stdio server.
