"""Main conversation loop - streaming + tool use cycle."""
from __future__ import annotations

import asyncio
import json as json_mod
import sys
import time
from typing import Any

from ccb.api.base import Message, Provider, Role, StreamEvent, ToolCall
from ccb.api.base import ToolResult as APIToolResult
from ccb.cost_tracker import get_cost_state
from ccb.display import (
    StreamPrinter,
    ask_permission,
    console,
    print_error,
    print_info,
    print_tool_call,
    print_tool_result,
    print_usage,
)
from ccb.hooks import load_hooks, run_hooks
from ccb.mcp.client import MCPManager
from ccb.permissions import is_auto_denied, needs_permission, record_approval
from ccb.session import Session
from ccb.tools.base import ToolRegistry


def _is_retryable(exc: Exception) -> bool:
    """Check if an exception is transient and worth retrying."""
    try:
        import httpx
        _httpx_retryable = (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout)
    except ImportError:
        _httpx_retryable = ()
    # Connection / timeout errors
    if isinstance(exc, (ConnectionError, TimeoutError, *_httpx_retryable)):
        return True
    # HTTP 429 (rate limit) or 5xx from API
    msg = str(exc).lower()
    if "429" in msg or "rate" in msg or "overloaded" in msg:
        return True
    if any(f"{code}" in msg for code in (500, 502, 503, 529)):
        return True
    return False


async def run_turn(
    provider: Provider,
    session: Session,
    registry: ToolRegistry,
    system_prompt: str = "",
    max_tool_rounds: int = 50,
    mcp_manager: MCPManager | None = None,
    hooks: dict | None = None,
    output_format: str = "rich",
    state: dict[str, Any] | None = None,
) -> None:
    """Run one user turn: stream response, handle tool calls, loop until done."""
    if hooks is None:
        hooks = load_hooks(session.cwd)
    state = state or {}

    text_mode = output_format in ("text", "json")

    # Prepend agent prompt if an agent definition is active
    agent_prompt = state.get("_agent_prompt", "")
    if agent_prompt:
        system_prompt = f"{agent_prompt}\n\n{system_prompt}" if system_prompt else agent_prompt

    # Resolve effort → max_tokens / temperature
    effort = state.get("effort", "high")
    effort_map = {"low": (4096, 0.3), "medium": (8192, 0.6), "high": (16384, 1.0)}
    max_tokens, temperature = effort_map.get(effort, (16384, 1.0))
    if state.get("fast"):
        max_tokens = min(max_tokens, 4096)

    # Token budget enforcement
    token_budget = state.get("token_budget")

    # Combine built-in + MCP tools
    all_tool_schemas = registry.all_schemas()
    if mcp_manager:
        all_tool_schemas.extend(mcp_manager.get_all_tools())

    # Context window size for the active model. Looked up in a curated
    # per-model table with user/account overrides (see ccb.model_limits).
    from ccb.model_limits import get_context_limit
    model_name = getattr(provider, "_model", "")
    ctx_limit = get_context_limit(model_name)

    cost = get_cost_state()
    cost.set_model(getattr(provider, "_model", ""))
    cost.start_turn()

    for round_num in range(max_tool_rounds):
        # Budget check
        if token_budget:
            used = session.total_input_tokens + session.total_output_tokens
            if used >= token_budget:
                print_info(f"Token budget reached ({used:,}/{token_budget:,}). Stopping.")
                return

        # Auto-compact: trigger when the CURRENT conversation size (the most
        # recent request's input_tokens) exceeds 80% of the context window.
        # We use last_input_tokens (snapshot of the most recent request), NOT
        # total_input_tokens (cumulative across all tool-call rounds, which
        # over-counts by ~Nx for multi-tool turns and fires prematurely).
        ctx_used = session.last_input_tokens
        if ctx_used > ctx_limit * 0.9 and len(session.messages) > 6:
            print_info(f"Context usage high ({ctx_used:,}/{ctx_limit:,}). Auto-compacting...")
            from ccb.commands import _compact
            try:
                await _compact(session, provider)
                # Reset the snapshot: it's stale after compaction. The next
                # response will repopulate it with the shrunken context size.
                # This also prevents re-triggering on the very next round
                # before a new response updates last_input_tokens.
                session.last_input_tokens = 0
                print_info(f"Compacted to {len(session.messages)} messages.")
            except Exception as e:
                print_error(f"Auto-compact failed: {e}")

        text_buf = ""
        tool_calls: list[ToolCall] = []
        usage: dict[str, int] = {}
        thinking_start: float = 0.0
        thinking_duration_ms: float = 0.0

        printer: StreamPrinter | None = None
        if not text_mode:
            printer = StreamPrinter()
            printer.start()

        thinking_buf = ""

        # Debug logging (CCB_DEBUG=1)
        import os as _os
        if _os.environ.get("CCB_DEBUG"):
            _model = getattr(provider, "_model", "?")
            _n = len(session.messages)
            _last_role = session.messages[-1].role.value if session.messages else "?"
            _last_content = (session.messages[-1].content or "")[:80] if session.messages else ""
            _has_tr = bool(session.messages[-1].tool_results) if session.messages else False
            print_info(f"[debug] round={round_num} msgs={_n} model={_model} last={_last_role} tr={_has_tr} '{_last_content}'")

        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                async for event in provider.stream(
                    messages=session.messages,
                    tools=all_tool_schemas,
                    system=system_prompt,
                    max_tokens=max_tokens,
                ):
                    if event.type == "text":
                        text_buf += event.text
                        if text_mode:
                            sys.stdout.write(event.text)
                            sys.stdout.flush()
                        elif printer:
                            printer.feed(event.text)

                    elif event.type == "thinking":
                        if not thinking_buf and not thinking_start:
                            thinking_start = time.time()
                        thinking_buf += event.text
                        if not text_mode and printer:
                            printer.feed_thinking(event.text)

                    elif event.type == "tool_use_end":
                        if event.tool_call:
                            tool_calls.append(event.tool_call)

                    elif event.type == "done":
                        usage = event.usage
                        if thinking_start:
                            thinking_duration_ms = (time.time() - thinking_start) * 1000
                        break

                    elif event.type == "error":
                        if printer:
                            printer.stop()
                        print_error(event.error or "Unknown API error")
                        return

                break  # success — exit retry loop

            except KeyboardInterrupt:
                if printer:
                    printer.stop()
                print_info("Interrupted.")
                return
            except Exception as e:
                if attempt < max_retries and _is_retryable(e):
                    wait = 2 ** attempt
                    print_info(f"Retrying in {wait}s... ({e})")
                    await asyncio.sleep(wait)
                    # Reset buffers for retry
                    text_buf = ""
                    tool_calls = []
                    thinking_buf = ""
                    if printer:
                        printer.stop()
                        printer = StreamPrinter()
                        printer.start()
                    continue
                if printer:
                    printer.stop()
                print_error(f"API error: {e}")
                return

        if printer:
            printer.stop()
        elif text_mode and text_buf:
            sys.stdout.write("\n")

        # Guard: empty response (no text, no tools) — don't silently exit
        if not text_buf.strip() and not tool_calls:
            print_info("Model returned empty response (may be a context or API issue).")
            cost.end_turn()
            if not text_mode:
                print_usage(usage, thinking_duration_ms=thinking_duration_ms)
            return

        # Record assistant message
        session.add_assistant_message(text_buf, tool_calls if tool_calls else None)
        session.add_usage(usage)
        cost.add_usage(usage)

        # No tool calls → turn is done; end cost timer before printing
        if not tool_calls:
            cost.end_turn()

        if not text_mode:
            print_usage(usage, thinking_duration_ms=thinking_duration_ms)

        if not tool_calls:
            return

        # Execute tool calls (with Ctrl-C protection)
        try:
            results = await execute_tool_calls(tool_calls, registry, session.cwd, mcp_manager, hooks)
        except KeyboardInterrupt:
            print_info("Interrupted during tool execution. Partial results saved.")
            session.save()
            return
        session.add_tool_results(results)

        # Save session after each tool round
        session.save()

    cost.end_turn()
    print_info(f"Reached max tool rounds ({max_tool_rounds})")


async def execute_tool_calls(
    tool_calls: list[ToolCall],
    registry: ToolRegistry,
    cwd: str,
    mcp_manager: MCPManager | None = None,
    hooks: dict | None = None,
) -> list[APIToolResult]:
    """Execute a batch of tool calls, handling permissions.

    Parallelism policy:
      - If the model emitted 2+ ``agent`` calls in this batch, kick them all
        off concurrently up front so wall-clock time = max(agent), not sum.
      - Non-agent tools are still processed sequentially below to preserve
        ordering guarantees (and to keep print output readable).
    """
    results: list[APIToolResult] = []
    hooks = hooks or {}

    # ── Pre-dispatch parallel agents ────────────────────────────────
    agent_ids = [tc.id for tc in tool_calls if tc.name == "agent"]
    agent_tasks: dict[str, asyncio.Task[str]] = {}
    if len(agent_ids) >= 2:
        # Fresh dashboard for this batch — drop completed entries from prior
        # turns so the display shows only the currently-running group.
        from ccb.display import agent_registry_clear
        agent_registry_clear()
        print_info(f"  ⇶ Launching {len(agent_ids)} subagents in parallel")
        for tc in tool_calls:
            if tc.name == "agent":
                # Each agent runs with its own provider + session (see run_agent)
                agent_tasks[tc.id] = asyncio.create_task(
                    run_agent(tc.input, registry, cwd)
                )

    for tc in tool_calls:
        # Check if this is an MCP tool
        if mcp_manager:
            mcp_parsed = mcp_manager.parse_mcp_tool_name(tc.name)
            if mcp_parsed:
                server_name, tool_name = mcp_parsed
                print_tool_call(f"mcp:{server_name}/{tool_name}", tc.input)
                try:
                    output = await mcp_manager.call_tool(server_name, tool_name, tc.input)
                    print_tool_result(tool_name, output, input_data=tc.input)
                    results.append(APIToolResult(tool_use_id=tc.id, content=output))
                except Exception as e:
                    error_msg = f"MCP error: {e}"
                    print_tool_result(tool_name, error_msg, is_error=True, input_data=tc.input)
                    results.append(APIToolResult(tool_use_id=tc.id, content=error_msg, is_error=True))
                continue

        # Handle MCP resource tools
        if tc.name == "list_mcp_resources" and mcp_manager:
            try:
                server = tc.input.get("server")
                resources = await mcp_manager.list_resources(server)
                print_tool_result(tc.name, resources, input_data=tc.input)
                results.append(APIToolResult(tool_use_id=tc.id, content=resources))
            except Exception as e:
                results.append(APIToolResult(tool_use_id=tc.id, content=str(e), is_error=True))
            continue

        if tc.name == "read_mcp_resource" and mcp_manager:
            try:
                server = tc.input.get("server", "")
                uri = tc.input.get("uri", "")
                content = await mcp_manager.read_resource(server, uri)
                print_tool_result(tc.name, content, input_data=tc.input)
                results.append(APIToolResult(tool_use_id=tc.id, content=content))
            except Exception as e:
                results.append(APIToolResult(tool_use_id=tc.id, content=str(e), is_error=True))
            continue

        tool = registry.get(tc.name)
        if not tool:
            results.append(APIToolResult(
                tool_use_id=tc.id,
                content=f"Unknown tool: {tc.name}",
                is_error=True,
            ))
            continue

        # Check permission
        if needs_permission(tc.name, tc.input, cwd=cwd):
            if is_auto_denied(tc.name, tc.input, cwd=cwd):
                results.append(APIToolResult(
                    tool_use_id=tc.id,
                    content="Action denied by approval policy.",
                    is_error=True,
                ))
                continue
            choice = await ask_permission(tc.name, tc.input)
            if choice.startswith("deny"):
                record_approval(tc.name, tc.input, choice, cwd=cwd)
                results.append(APIToolResult(
                    tool_use_id=tc.id,
                    content="User denied permission",
                    is_error=True,
                ))
                continue
            record_approval(tc.name, tc.input, choice, cwd=cwd)

        # Handle agent tool specially
        if tc.name == "agent":
            if tc.id in agent_tasks:
                # Parallel path: task was kicked off in the pre-dispatch loop
                try:
                    result = await agent_tasks[tc.id]
                except Exception as e:
                    result = f"Agent failed: {e}"
            else:
                # Single-agent path: run inline
                result = await run_agent(tc.input, registry, cwd)
            results.append(APIToolResult(tool_use_id=tc.id, content=result))
            continue

        # Execute tool
        print_tool_call(tc.name, tc.input)

        # Pre-tool hook
        await run_hooks("pre_tool_call", hooks, {"tool_name": tc.name, "input": tc.input}, cwd)

        try:
            tool_result = await tool.execute(tc.input, cwd)
            print_tool_result(
                tc.name, tool_result.output, tool_result.is_error,
                input_data=tc.input,
            )

            # Post-tool hook
            await run_hooks("post_tool_call", hooks, {
                "tool_name": tc.name, "output": tool_result.output[:500],
                "is_error": tool_result.is_error,
            }, cwd)

            results.append(APIToolResult(
                tool_use_id=tc.id,
                content=tool_result.output,
                is_error=tool_result.is_error,
            ))
        except Exception as e:
            error_msg = f"Tool execution error: {e}"
            print_tool_result(tc.name, error_msg, is_error=True, input_data=tc.input)
            results.append(APIToolResult(
                tool_use_id=tc.id,
                content=error_msg,
                is_error=True,
            ))

    return results


_agent_counter = 0


async def run_agent(
    input_data: dict[str, Any],
    registry: ToolRegistry,
    cwd: str,
    force_sandbox: bool = True,
) -> str:
    """Run a sub-agent with its own conversation context.

    Isolation guarantees:
      - Dedicated Provider (fresh HTTP client, own model cfg).
      - Dedicated Session (zero parent message history).
      - contextvar ``_inside_agent = True`` marks the entire call tree,
        which causes permission prompts and noisy tool output to route to
        the progress dashboard instead of the parent REPL.
      - Optional: Forced sandbox mode for secure execution (default on).

    Budget: ``max_tool_rounds=80`` — deliberately generous. Per user's
    preference, quality > token cost; cap only exists to prevent runaway
    recursion, not to save tokens.

    Args:
        input_data: Agent task specification with "task" field
        registry: Tool registry for the agent
        cwd: Working directory
        force_sandbox: If True, enforce sandbox mode even if parent disabled it
    """
    from ccb.api.router import create_provider
    from ccb.prompts import get_system_prompt
    from ccb.agent_context import enter_agent
    from ccb.display import agent_register, agent_complete
    from ccb.state import get_state

    global _agent_counter
    _agent_counter += 1
    label = f"a{_agent_counter}"

    task = input_data.get("task", "")

    # Mark this task's context tree as "inside agent". Because contextvars
    # propagate through asyncio.create_task and await, every tool call
    # dispatched below this point (no matter how deep) will see the flag.
    enter_agent(label=label)

    agent_register(label, task)

    # Agent sandbox: temporarily enable if requested
    state = get_state()
    original_sandbox = state.get("sandbox_mode", False) if state else False
    sandbox_restored = False

    try:
        # Force sandbox for agent if requested and available
        if force_sandbox and not original_sandbox:
            from ccb.sandbox_exec import get_sandbox
            sandbox = get_sandbox()
            if sandbox.available:
                sandbox.enable()
                state.set("sandbox_mode", True)
                sandbox_restored = True
                # Set stricter limits for agent
                sandbox.set_timeout(60)  # Shorter timeout for agents

        provider = create_provider()
        agent_session = Session(cwd=cwd)
        agent_session.add_user_message(task)

        await run_turn(
            provider=provider,
            session=agent_session,
            registry=registry,
            system_prompt=get_system_prompt(cwd),
            max_tool_rounds=80,
        )

        for msg in reversed(agent_session.messages):
            if msg.role == Role.ASSISTANT and msg.content:
                agent_complete(label, msg.content)
                return msg.content

        agent_complete(label, "")
        return "(Agent completed without text output)"
    except Exception as e:
        agent_complete(label, f"error: {e}")
        raise
    finally:
        # Restore original sandbox state
        if sandbox_restored and state:
            state.set("sandbox_mode", original_sandbox)
            # Restore timeout
            from ccb.sandbox_exec import get_sandbox
            sandbox = get_sandbox()
            sandbox.set_timeout(30)  # Reset to default
