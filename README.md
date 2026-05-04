# ccb-py

A Python rewrite of Claude Code CLI — a fast, extensible AI coding assistant.

## Features

- **Multi-provider support**: Anthropic, OpenAI, Gemini, Bedrock, Vertex
- **20 built-in tools**: bash, file ops, grep, glob, web search, code interpreter, image generation, and more
- **Agent SDK integration**: OpenAI Agents SDK + Anthropic Agent SDK features
- **MCP protocol**: Full Model Context Protocol client/server support
- **Sandbox execution**: Docker, macOS sandbox, firejail isolation
- **Multi-agent orchestration**: Parallel sub-agents with automatic decomposition
- **Session management**: Fork, compact, persist conversations
- **Guardrails**: Input/output safety validation
- **Plugin system**: Extensible tool and skill architecture

## Quick Start

```bash
# Install
pip install ccb

# Or from source
git clone https://github.com/hua123an/ccb-py.git
cd ccb-py
pip install -e ".[agents]"

# Run
ccb-py
```

## Configuration

### API Keys

```bash
# Environment variables
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...

# Or configure accounts interactively
ccb-py
> /account add
```

### Account Management

```bash
# List accounts
> /account list

# Switch account
> /account switch <name>

# Switch model
> /model <model-name>
```

## Providers

| Provider | Protocol | Models |
|----------|----------|--------|
| Anthropic | Native | claude-sonnet-4, claude-opus-4, claude-haiku-4-5 |
| OpenAI | OpenAI-compat | gpt-4o, o3, o4-mini |
| Gemini | Native + compat | gemini-2.0-flash, gemini-2.5-pro |
| Bedrock | AWS SDK | claude-sonnet-4, claude-opus-4 |
| Vertex | Google SDK | claude-sonnet-4, claude-opus-4 |

## Tools

| Tool | Description |
|------|-------------|
| `bash` | Execute shell commands |
| `file_read` / `file_write` / `file_edit` | File operations |
| `grep` / `glob` | Search files and content |
| `web_search` / `web_fetch` | Web access |
| `code_interpreter` | Sandboxed Python execution |
| `image_gen` | DALL-E 3 image generation |
| `agent` | Spawn sub-agents |
| `notebook_edit` | Jupyter notebook editing |
| `todo_write` | Task tracking |
| `ask_user_question` | Interactive prompts |

## Agent SDK Features

### @tool Decorator

```python
from ccb.tools.tool_decorator import tool

@tool(
    name="my_tool",
    description="Does something useful",
    input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
)
async def my_tool(input: dict) -> dict:
    return {"result": f"Processed {input['x']}"}
```

### Agent Definitions

```yaml
# ~/.claude/agents/researcher.yaml
name: researcher
description: Read-only research agent
prompt: "You are a researcher. Only read files, never edit."
tools: [file_read, grep, glob, web_search]
effort: low
permission_mode: plan
```

### Task Budget

```python
from ccb.task_budget import TaskBudget

budget = TaskBudget(max_total_tokens=100000, max_turns=50)
# Automatically enforced in the conversation loop
```

### Guardrails

```python
from ccb.guardrails import get_guardrails

g = get_guardrails()
violations = g.check_input("Ignore all previous instructions...")
# Blocks prompt injection attempts
```

## Commands

| Command | Description |
|---------|-------------|
| `/help` | Show help |
| `/compact` | Compress conversation |
| `/model <name>` | Switch model |
| `/account` | Manage accounts |
| `/sandbox` | Toggle sandbox mode |
| `/think` | Toggle thinking mode |
| `/effort <level>` | Set effort (low/medium/high) |
| `/fork` | Fork current session |
| `/agents` | List agent definitions |
| `/budget` | Show token budget |

## Development

```bash
# Install dev dependencies
pip install -e ".[agents]"
pip install pytest pytest-asyncio ruff

# Run tests
pytest tests/ -q

# Lint
ruff check src/ tests/

# Format
ruff format src/ tests/
```

## Architecture

```
src/ccb/
├── api/              # Provider implementations
│   ├── anthropic_provider.py
│   ├── openai_provider.py
│   ├── gemini_provider.py
│   ├── bedrock_provider.py
│   ├── vertex_provider.py
│   └── router.py     # Provider auto-routing
├── tools/            # Built-in tools
│   ├── bash.py
│   ├── file.py
│   ├── code_interpreter.py
│   ├── image_gen.py
│   └── tool_decorator.py
├── mcp/              # MCP client/server
├── oauth/            # OAuth flows
├── loop.py           # Main conversation loop
├── session.py        # Session management
├── config.py         # Configuration
├── commands.py       # Slash commands
├── guardrails.py     # Safety validation
├── compaction.py     # Conversation compression
├── task_budget.py    # Token budget enforcement
├── session_fork.py   # Session branching
└── agent_defs.py     # Agent definitions
```

## License

MIT
