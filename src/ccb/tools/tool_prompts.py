"""Tool description prompts — extracted from official claude-code builtin-tools/*/prompt.ts."""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Bash
# ---------------------------------------------------------------------------
BASH_PROMPT = """\
Executes a given bash command and returns its output.

The working directory persists between commands, but shell state does not. The shell environment is initialized from the user's profile (bash or zsh).

IMPORTANT: Avoid using this tool to run cat, head, tail, sed, awk, or echo commands, unless explicitly instructed or after you have verified that a dedicated tool cannot accomplish your task. Instead, use the appropriate dedicated tool as this will provide a much better experience for the user:

 - File search: Use glob (NOT find or ls)
 - Content search: Use grep (NOT grep or rg)
 - Read files: Use file_read (NOT cat/head/tail)
 - Edit files: Use file_edit (NOT sed/awk)
 - Write files: Use file_write (NOT echo >/cat <<EOF)
 - Communication: Output text directly (NOT echo/printf)

While the bash tool can do similar things, it's better to use the built-in tools as they provide a better user experience and make it easier to review tool calls and give permission.

# Instructions
 - If your command will create new directories or files, first use this tool to run ls to verify the parent directory exists and is the correct location.
 - Always quote file paths that contain spaces with double quotes in your command (e.g., cd "path with spaces/file.txt")
 - Try to maintain your current working directory throughout the session by using absolute paths and avoiding usage of cd. You may use cd if the User explicitly requests it.
 - You may specify an optional timeout in milliseconds (up to 600000ms / 10 minutes). By default, your command will timeout after 120000ms (2 minutes).
 - When issuing multiple commands:
   - If the commands are independent and can run in parallel, make multiple bash tool calls in a single message.
   - If the commands depend on each other and must run sequentially, use a single bash call with '&&' to chain them together.
   - Use ';' only when you need to run commands sequentially but don't care if earlier commands fail.
   - DO NOT use newlines to separate commands (newlines are ok in quoted strings).
 - For git commands:
   - Prefer to create a new commit rather than amending an existing commit.
   - Before running destructive operations (e.g., git reset --hard, git push --force, git checkout --), consider whether there is a safer alternative. Only use destructive operations when they are truly the best approach.
   - Never skip hooks (--no-verify) or bypass signing (--no-gpg-sign) unless the user has explicitly asked for it.
 - Avoid unnecessary sleep commands:
   - Do not sleep between commands that can run immediately — just run them.
   - If your command is long running and you would like to be notified when it finishes — use run_in_background. No sleep needed.
   - Do not retry failing commands in a sleep loop — diagnose the root cause.

# Committing changes with git

Only create commits when requested by the user. If unclear, ask first. When the user asks you to create a new git commit, follow these steps carefully:

Git Safety Protocol:
- NEVER update the git config
- NEVER run destructive git commands (push --force, reset --hard, checkout ., restore ., clean -f, branch -D) unless the user explicitly requests these actions
- NEVER skip hooks (--no-verify, --no-gpg-sign, etc) unless the user explicitly requests it
- NEVER run force push to main/master, warn the user if they request it
- CRITICAL: Always create NEW commits rather than amending, unless the user explicitly requests a git amend. When a pre-commit hook fails, the commit did NOT happen — so --amend would modify the PREVIOUS commit, which may result in destroying work
- When staging files, prefer adding specific files by name rather than using "git add -A" or "git add .", which can accidentally include sensitive files (.env, credentials) or large binaries
- NEVER commit changes unless the user explicitly asks you to

1. Run the following bash commands in parallel:
   - Run a git status command to see all untracked files. IMPORTANT: Never use the -uall flag.
   - Run a git diff command to see both staged and unstaged changes.
   - Run a git log command to see recent commit messages, so that you can follow this repository's commit message style.
2. Analyze all staged changes and draft a commit message:
   - Summarize the nature of the changes (new feature, enhancement, bug fix, refactoring, test, docs, etc.).
   - Do not commit files that likely contain secrets (.env, credentials.json, etc).
   - Draft a concise (1-2 sentences) commit message that focuses on the "why" rather than the "what".
3. Run the following commands:
   - Add relevant untracked files to the staging area.
   - Create the commit with a message.
   - Run git status after the commit completes to verify success.
4. If the commit fails due to pre-commit hook: fix the issue and create a NEW commit.

Important notes:
- NEVER run additional commands to read or explore code, besides git bash commands
- DO NOT push to the remote repository unless the user explicitly asks you to do so
- IMPORTANT: Never use git commands with the -i flag (like git rebase -i or git add -i) since they require interactive input which is not supported.
- If there are no changes to commit, do not create an empty commit
- In order to ensure good formatting, ALWAYS pass the commit message via a HEREDOC:
  git commit -m "$(cat <<'EOF'
  Commit message here.
  EOF
  )"

# Creating pull requests
Use the gh command via the Bash tool for ALL GitHub-related tasks including working with issues, pull requests, checks, and releases. If given a Github URL use the gh command to get the information needed.

IMPORTANT: When the user asks you to create a pull request:
1. Run git status, git diff, check remote tracking, and git log + git diff [base-branch]...HEAD in parallel.
2. Analyze ALL commits that will be included in the PR, and draft a title and summary.
3. Create branch if needed, push, and create PR using gh pr create.

Important:
- Return the PR URL when you're done, so the user can see it

# Other common operations
- View comments on a Github PR: gh api repos/foo/bar/pulls/123/comments"""


# ---------------------------------------------------------------------------
# FileRead
# ---------------------------------------------------------------------------
FILE_READ_PROMPT = """\
Reads a file from the local filesystem. You can access any file directly by using this tool.
Assume this tool is able to read all files on the machine. If the User provides a path to a file assume that path is valid. It is okay to read a file that does not exist; an error will be returned.

Usage:
- The file_path parameter must be an absolute path, not a relative path
- By default, it reads up to 2000 lines starting from the beginning of the file
- You can optionally specify a line offset and limit (especially handy for long files), but it's recommended to read the whole file by not providing these parameters
- Results are returned using cat -n format, with line numbers starting at 1
- This tool allows reading images (eg PNG, JPG, etc). When reading an image file the contents are presented visually.
- This tool can read Jupyter notebooks (.ipynb files) and returns all cells with their outputs, combining code, text, and visualizations.
- This tool can only read files, not directories. To read a directory, use an ls command via the bash tool.
- You will regularly be asked to read screenshots. If the user provides a path to a screenshot, ALWAYS use this tool to view the file at the path.
- If you read a file that exists but has empty contents you will receive a system reminder warning in place of file contents."""


# ---------------------------------------------------------------------------
# FileEdit
# ---------------------------------------------------------------------------
FILE_EDIT_PROMPT = """\
Performs exact string replacements in files.

Usage:
- You must use your file_read tool at least once in the conversation before editing. This tool will error if you attempt an edit without reading the file.
- When editing text from Read tool output, ensure you preserve the exact indentation (tabs/spaces) as it appears AFTER the line number prefix. The line number prefix format is: spaces + line number + arrow. Everything after that is the actual file content to match. Never include any part of the line number prefix in the old_string or new_string.
- ALWAYS prefer editing existing files in the codebase. NEVER write new files unless explicitly required.
- Only use emojis if the user explicitly requests it. Avoid adding emojis to files unless asked.
- The edit will FAIL if old_string is not unique in the file. Either provide a larger string with more surrounding context to make it unique or use replace_all to change every instance of old_string.
- Use replace_all for replacing and renaming strings across the file. This parameter is useful if you want to rename a variable for instance."""


# ---------------------------------------------------------------------------
# FileWrite
# ---------------------------------------------------------------------------
FILE_WRITE_PROMPT = """\
Writes a file to the local filesystem.

Usage:
- This tool will overwrite the existing file if there is one at the provided path.
- If this is an existing file, you MUST use the file_read tool first to read the file's contents. This tool will fail if you did not read the file first.
- Prefer the file_edit tool for modifying existing files — it only sends the diff. Only use this tool to create new files or for complete rewrites.
- NEVER create documentation files (*.md) or README files unless explicitly requested by the User.
- Only use emojis if the user explicitly requests it. Avoid writing emojis to files unless asked."""


# ---------------------------------------------------------------------------
# Glob
# ---------------------------------------------------------------------------
GLOB_PROMPT = """\
- Fast file pattern matching tool that works with any codebase size
- Supports glob patterns like "**/*.js" or "src/**/*.ts"
- Returns matching file paths sorted by modification time
- Use this tool when you need to find files by name patterns
- When you are doing an open ended search that may require multiple rounds of globbing and grepping, use the agent tool instead"""


# ---------------------------------------------------------------------------
# Grep
# ---------------------------------------------------------------------------
GREP_PROMPT = """\
A powerful search tool built on ripgrep.

Usage:
- ALWAYS use grep for search tasks. NEVER invoke grep or rg as a bash command. The grep tool has been optimized for correct permissions and access.
- Supports full regex syntax (e.g., "log.*Error", "function\\s+\\w+")
- Filter files with include parameter in glob format (e.g., "*.js", "**/*.tsx")
- Use agent tool for open-ended searches requiring multiple rounds
- Pattern syntax: Uses ripgrep (not grep) — literal braces need escaping (use interface\\{\\} to find interface{} in Go code)"""


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------
AGENT_PROMPT = """\
Launch a new agent to handle complex, multi-step tasks autonomously.

The agent tool launches specialized agents (subprocesses) that autonomously handle complex tasks. Each agent type has specific capabilities and tools available to it.

When using the agent tool, the agent starts with zero context. Brief the agent like a smart colleague who just walked into the room — it hasn't seen this conversation, doesn't know what you've tried, doesn't understand why this task matters.

## Writing the prompt
- Explain what you're trying to accomplish and why.
- Describe what you've already learned or ruled out.
- Give enough context about the surrounding problem that the agent can make judgment calls rather than just following a narrow instruction.
- If you need a short response, say so ("report in under 200 words").
- Lookups: hand over the exact command. Investigations: hand over the question — prescribed steps become dead weight when the premise is wrong.

Terse command-style prompts produce shallow, generic work.

**Never delegate understanding.** Don't write "based on your findings, fix the bug" or "based on the research, implement it." Those phrases push synthesis onto the agent instead of doing it yourself. Write prompts that prove you understood: include file paths, line numbers, what specifically to change.

When NOT to use the agent tool:
- If you want to read a specific file path, use the file_read tool or glob tool instead, to find the match more quickly
- If you are searching for a specific class definition like "class Foo", use glob instead, to find the match more quickly
- If you are searching for code within a specific file or set of 2-3 files, use the file_read tool instead of the agent tool, to find the match more quickly

Usage notes:
- Always include a short description (3-5 words) summarizing what the agent will do
- Launch multiple agents concurrently whenever possible, to maximize performance; to do that, use a single message with multiple tool uses
- When the agent is done, it will return a single message back to you. The result returned by the agent is not visible to the user. To show the user the result, you should send a text message back to the user with a concise summary of the result.
- Each Agent invocation starts fresh — provide a complete task description.
- The agent's outputs should generally be trusted
- Clearly tell the agent whether you expect it to write code or just to do research (search, file reads, web fetches, etc.), since it is not aware of the user's intent
- If the user specifies that they want you to run agents "in parallel", you MUST send a single message with multiple agent tool use content blocks.

## When to AUTOMATICALLY escalate to multi-agent (hard rule)
If at planning time you estimate the task will take **10 or more independent
steps** (or you've already written ≥10 items via `todo_write`), you MUST:
  1. Partition the work into 2–5 disjoint sub-tasks whose execution does not
     depend on each other's intermediate output.
  2. Emit **multiple `agent` tool calls in a single message** so they run
     concurrently. Do NOT run them one at a time.
  3. In your own follow-up turn, synthesize the returned results into a
     single coherent answer for the user.

Counter-examples (do NOT split):
  - Tasks where each step strictly depends on the previous one (e.g. debug →
    fix → verify in one file).
  - Fewer than ~8 steps — the coordination overhead outweighs the speedup.
  - Tasks requiring shared in-memory state (file you're iteratively editing).

Split candidates:
  - "Audit 5 subsystems" → 1 agent per subsystem.
  - "Read and summarize N unrelated files" → split by directory/topic.
  - "Run tests across multiple packages and report" → 1 agent per package."""


# ---------------------------------------------------------------------------
# WebFetch
# ---------------------------------------------------------------------------
WEB_FETCH_PROMPT = """\
Fetches a web page and returns its content as markdown.

Usage:
- Provide a URL to fetch content from
- Returns the page content converted to markdown format
- Use this to read documentation, blog posts, or any web page
- For API endpoints, returns the raw response"""


# ---------------------------------------------------------------------------
# WebSearch
# ---------------------------------------------------------------------------
WEB_SEARCH_PROMPT = """\
Searches the web and returns a list of relevant results.

Usage:
- Provide a search query string
- Returns a list of search results with titles, URLs, and snippets
- Use this when you need to find information on the web
- For more detailed content, follow up with web_fetch on specific URLs"""


# ---------------------------------------------------------------------------
# TodoWrite
# ---------------------------------------------------------------------------
TODO_WRITE_PROMPT = """\
Reads or writes a structured todo list in a Markdown file.

Usage:
- Break down and manage your work with this tool
- Helpful for planning your work and helping the user track your progress
- Mark each task as completed as soon as you are done with the task
- Do not batch up multiple tasks before marking them as completed
- Use this tool to track and manage multi-step tasks"""


# ---------------------------------------------------------------------------
# NotebookEdit
# ---------------------------------------------------------------------------
NOTEBOOK_EDIT_PROMPT = """\
Completely replaces the contents of a specific cell in a Jupyter notebook (.ipynb file) with new source. Jupyter notebooks are interactive documents that combine code, text, and visualizations, commonly used for data analysis and scientific computing. The notebook_path parameter must be an absolute path, not a relative path. The cell_number is 0-indexed. Use edit_mode=insert to add a new cell at the index specified by cell_number. Use edit_mode=delete to delete the cell at the index specified by cell_number."""


# ---------------------------------------------------------------------------
# AskUserQuestion
# ---------------------------------------------------------------------------
ASK_USER_QUESTION_PROMPT = """\
Use this tool when you need to ask the user questions during execution. This allows you to:
1. Gather user preferences or requirements
2. Clarify ambiguous instructions
3. Get decisions on implementation choices as you work
4. Offer choices to the user about what direction to take.

Usage notes:
- Users will always be able to select "Other" to provide custom text input
- If you recommend a specific option, make that the first option in the list and add "(Recommended)" at the end of the label

Plan mode note: In plan mode, use this tool to clarify requirements or choose between approaches BEFORE finalizing your plan. Do NOT use this tool to ask "Is my plan ready?" or "Should I proceed?" — use exit_plan_mode for plan approval."""


# ---------------------------------------------------------------------------
# TaskStop / TaskOutput
# ---------------------------------------------------------------------------
TASK_STOP_PROMPT = "Stop the current task/agent and return a result to the caller."
TASK_OUTPUT_PROMPT = "Output an intermediate result from a sub-agent task without stopping."


# ---------------------------------------------------------------------------
# ListMcpResources / ReadMcpResource
# ---------------------------------------------------------------------------
LIST_MCP_RESOURCES_PROMPT = """\
List available resources from configured MCP servers.
Each returned resource will include all standard MCP resource fields plus a 'server' field indicating which server the resource belongs to.

Parameters:
- server (optional): The name of a specific MCP server to get resources from. If not provided, resources from all servers will be returned."""

READ_MCP_RESOURCE_PROMPT = """\
Reads a specific resource from an MCP server, identified by server name and resource URI.

Parameters:
- server (required): The name of the MCP server from which to read the resource
- uri (required): The URI of the resource to read"""


# ---------------------------------------------------------------------------
# EnterPlanMode / ExitPlanMode
# ---------------------------------------------------------------------------
ENTER_PLAN_MODE_PROMPT = """\
Enter plan mode to create a structured plan before executing. In plan mode, outline your approach step-by-step before taking actions.

Use this when:
- A task requires multiple steps of implementation
- You want to confirm your approach with the user first
- The task is complex enough that planning will improve results"""

EXIT_PLAN_MODE_PROMPT = """\
Exit plan mode and present the plan for the user to review and approve.

## When to Use This Tool
IMPORTANT: Only use this tool when the task requires planning the implementation steps of a task that requires writing code. For research tasks where you're gathering information, searching files, reading files or in general trying to understand the codebase — do NOT use this tool.

## Before Using This Tool
Ensure your plan is complete and unambiguous:
- If you have unresolved questions about requirements or approach, use ask_user_question first
- Once your plan is finalized, use THIS tool to request approval

**Important:** Do NOT use ask_user_question to ask "Is this plan okay?" or "Should I proceed?" — that's exactly what THIS tool does."""
