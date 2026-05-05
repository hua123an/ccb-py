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

    # ── Langfuse: start trace for this turn ──
    from ccb.langfuse_monitor import get_monitor as _get_lf_monitor
    _lf = _get_lf_monitor()
    _trace_id = _lf.trace_start(name="run_turn", metadata={"cwd": session.cwd})

    text_mode = output_format in ("text", "json")
    stream_json = output_format == "stream-json"

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

    # Token budget enforcement (supports both legacy int and new TaskBudget)
    token_budget = state.get("token_budget")
    task_budget_obj = state.get("_task_budget")  # TaskBudget instance from agent_defs

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

    # ── Task Planning: auto-decompose on first round when new user message arrives ──
    plan_result: dict[str, Any] | None = None
    last_msg = session.messages[-1] if session.messages else None
    if (
        last_msg
        and last_msg.role == Role.USER
        and not last_msg.tool_results
        and (last_msg.content or "").strip()
    ):
        plan_result = await _run_task_planning(
            session, provider, system_prompt, text_mode, stream_json
        )
        if plan_result:
            total_steps = len(plan_result.get("steps", []))
            complexity = plan_result.get("complexity", "low")
            if not text_mode and not stream_json:
                print_info(
                    f"  📋 Planning: {total_steps} steps, complexity={complexity}"
                )
            # If complex enough, auto-parallelize immediately using the plan
            if total_steps >= 3 or complexity in ("medium", "high"):
                success = await _run_parallel_from_plan(
                    plan_result,
                    session,
                    provider,
                    registry,
                    system_prompt,
                    max_tokens,
                    text_mode,
                    stream_json,
                )
                if success:
                    pass  # _run_parallel_from_plan already injected assistant+user msgs
            else:
                if not text_mode and not stream_json:
                    print_info(
                        f"  📋 Plan: {total_steps} steps, complexity={complexity} — not parallelizing (threshold: >=3 steps or medium/high)"
                    )

    # One-shot prefill: consumed on round 0, then cleared
    _prefill = state.pop("prefill", "") or ""

    for round_num in range(max_tool_rounds):
        # Budget check
        if token_budget:
            used = session.total_input_tokens + session.total_output_tokens
            if used >= token_budget:
                print_info(f"Token budget reached ({used:,}/{token_budget:,}). Stopping.")
                return

        # TaskBudget check (richer budget object)
        if task_budget_obj:
            can_continue, reason = task_budget_obj.check()
            if not can_continue:
                print_info(f"Task budget exhausted: {reason}")
                return

        # Auto-compact: trigger when the CURRENT conversation size (the most
        # recent request's input_tokens) exceeds 80% of the context window.
        # We use last_input_tokens (snapshot of the most recent request), NOT
        # total_input_tokens (cumulative across all tool-call rounds, which
        # over-counts by ~Nx for multi-tool turns and fires prematurely).
        ctx_used = session.last_input_tokens
        if ctx_used > ctx_limit * 0.9 and len(session.messages) > 6:
            print_info(f"Context usage high ({ctx_used:,}/{ctx_limit:,}). Auto-compacting...")
            from ccb.compaction import compact_session
            try:
                removed = await compact_session(session, provider)
                session.last_input_tokens = 0
                if removed:
                    print_info(f"Compacted: removed {removed} messages, {len(session.messages)} remaining.")
                else:
                    # Fallback to legacy compaction
                    from ccb.commands import _compact
                    await _compact(session, provider)
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
        if not text_mode and not stream_json:
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
                # Stream with overall timeout: 180s covers image upload + generation
                stream_coro = provider.stream(
                    messages=session.messages,
                    tools=all_tool_schemas,
                    system=system_prompt,
                    max_tokens=max_tokens,
                    prefill=_prefill if round_num == 0 else "",
                )
                async with asyncio.timeout(180):
                    async for event in stream_coro:
                        if event.type == "text":
                            text_buf += event.text
                            if text_mode:
                                sys.stdout.write(event.text)
                                sys.stdout.flush()
                            elif stream_json:
                                _sj_line = json_mod.dumps({'type': 'text_delta', 'delta': {'text': event.text}})
                                sys.stdout.write(_sj_line + '\n')
                                sys.stdout.flush()
                            elif printer:
                                printer.feed(event.text)

                        elif event.type == "thinking":
                            if not thinking_buf and not thinking_start:
                                thinking_start = time.time()
                            thinking_buf += event.text
                            if not text_mode and printer:
                                printer.feed_thinking(event.text)

                            if stream_json:
                                _sj_line = json_mod.dumps({'type': 'thinking', 'text': event.text})
                                sys.stdout.write(_sj_line + '\n')
                                sys.stdout.flush()

                        elif event.type == "tool_use_start":
                            if stream_json and event.tool_call:
                                tc = event.tool_call
                                _sj_line = json_mod.dumps({'type': 'tool_use_start', 'id': tc.id, 'name': tc.name})
                                sys.stdout.write(_sj_line + '\n')
                                sys.stdout.flush()

                        elif event.type == "tool_use_end":
                            if event.tool_call:
                                tool_calls.append(event.tool_call)

                            if stream_json and event.tool_call:
                                tc = event.tool_call
                                _sj_line = json_mod.dumps({'type': 'tool_use_end', 'id': tc.id, 'name': tc.name, 'input': tc.input})
                                sys.stdout.write(_sj_line + '\n')
                                sys.stdout.flush()

                        elif event.type == "done":
                            usage = event.usage
                            if thinking_start:
                                thinking_duration_ms = (time.time() - thinking_start) * 1000

                            if stream_json:
                                _sj_line = json_mod.dumps({'type': 'done', 'usage': event.usage})
                                sys.stdout.write(_sj_line + '\n')
                                sys.stdout.flush()
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
                # ── Sentry: capture API errors ──
                try:
                    from ccb.sentry_integration import capture_exception as _sentry_capture
                    _sentry_capture(e, context={"round": round_num, "attempt": attempt})
                except Exception:
                    pass

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
        elif (text_mode or stream_json) and text_buf:
            sys.stdout.write("\n")

        # Guard: empty response (no text, no tools) — don't silently exit
        if not text_buf.strip() and not tool_calls:
            print_info("Model returned empty response (may be a context or API issue).")
            cost.end_turn()
            if not text_mode and not stream_json:
                print_usage(usage, thinking_duration_ms=thinking_duration_ms)
            return

        # Record assistant message
        session.add_assistant_message(text_buf, tool_calls if tool_calls else None)
        session.add_usage(usage)
        cost.add_usage(usage)
        if task_budget_obj:
            task_budget_obj.add_usage(usage)

        # No tool calls → turn is done; end cost timer before printing
        if not tool_calls:
            cost.end_turn()
            # ── Langfuse: log generation & end trace ──
            _lf.generation_log(
                _trace_id,
                model=getattr(provider, "_model", ""),
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                latency_ms=thinking_duration_ms,
            )
            _lf.trace_end(_trace_id)

        if not text_mode and not stream_json:
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

        # Auto-parallelize after 4 sequential tool rounds (original was 10 — too late)
        if round_num >= 4:
            success = await _try_auto_parallelize(
                session, provider, registry, system_prompt, max_tokens,
                text_mode, stream_json,
            )
            if success:
                pass

        # Emit tool results for stream-json mode
        if stream_json:
            for _tc, _tr in zip(tool_calls, results):
                _sj_line = json_mod.dumps({'type': 'tool_result', 'tool_use_id': _tr.tool_use_id, 'content': _tr.content[:2000], 'is_error': _tr.is_error})
                sys.stdout.write(_sj_line + '\n')
                sys.stdout.flush()


        # Save session after each tool round
        session.save()

    cost.end_turn()
    _lf.trace_end(_trace_id)
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

    # Load MCP approval manager
    from ccb.mcp_approval import ApprovalMode, get_approval_manager
    approval_mgr = get_approval_manager()

    for tc in tool_calls:
        # Check if this is an MCP tool
        if mcp_manager:
            mcp_parsed = mcp_manager.parse_mcp_tool_name(tc.name)
            if mcp_parsed:
                server_name, tool_name = mcp_parsed
                # MCP approval check
                mode, reason = approval_mgr.check_approval(tool_name, server_name, tc.input)
                if mode == ApprovalMode.DENY:
                    results.append(APIToolResult(
                        tool_use_id=tc.id,
                        content=f"Denied by approval policy: {reason}",
                        is_error=True,
                    ))
                    continue
                if mode == ApprovalMode.ASK:
                    approved = await ask_permission(f"mcp:{server_name}/{tool_name}", tc.input)
                    if not approved:
                        approval_mgr.record_approval(tool_name, server_name, approved=False)
                        results.append(APIToolResult(tool_use_id=tc.id, content="User denied permission", is_error=True))
                        continue
                    approval_mgr.record_approval(tool_name, server_name, approved=True)

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
        # ── Sentry: breadcrumb for tool call ──
        try:
            from ccb.sentry_integration import add_breadcrumb as _sentry_breadcrumb
            _sentry_breadcrumb("tool", f"Executing {tc.name}", data={"input_keys": list(tc.input.keys()) if isinstance(tc.input, dict) else []})
        except Exception:
            pass

        # Pre-tool hook
        await run_hooks("pre_tool_call", hooks, {"tool_name": tc.name, "input": tc.input}, cwd)

        try:
            # ── Sentry: wrap tool execution in a span ──
            # ── Langfuse: span per tool call ──
            from ccb.sentry_integration import tool_span as _sentry_tool_span
            from ccb.langfuse_monitor import get_monitor as _get_lf_mon
            _lf_mon = _get_lf_mon()
            _lf_sid = _lf_mon.span_start(_trace_id, f"tool:{tc.name}", input=tc.input)
            with _sentry_tool_span(tc.name, tc.input):
                tool_result = await tool.execute(tc.input, cwd)
            _lf_mon.span_end(_lf_sid, output=tool_result.output[:500])
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

    # Run input guardrails before execution
    from ccb.guardrails import get_guardrails
    guardrails = get_guardrails()
    violations = guardrails.check_input(task)
    if violations:
        block_msgs = [v.message for v in violations if v.severity == "block"]
        if block_msgs:
            agent_complete(label, f"Guardrail blocked: {'; '.join(block_msgs)}")
            return f"[Guardrail blocked] {'; '.join(block_msgs)}"

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

        result_text = "(Agent completed without text output)"
        for msg in reversed(agent_session.messages):
            if msg.role == Role.ASSISTANT and msg.content:
                result_text = msg.content
                break

        # Run output guardrails
        out_violations = guardrails.check_output(result_text)
        if out_violations:
            warn_msgs = [v.message for v in out_violations if v.severity == "warn"]
            if warn_msgs:
                result_text += f"\n\n[Guardrail warnings: {'; '.join(warn_msgs)}]"

        agent_complete(label, result_text)
        return result_text
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


async def _try_auto_parallelize(
    session: Session,
    provider: Provider,
    registry: ToolRegistry,
    system_prompt: str,
    max_tokens: int,
    text_mode: bool,
    stream_json: bool,
) -> bool:
    """After 4 sequential tool rounds, auto-decompose into parallel sub-agents.

    Returns True if parallelization was performed.
    """
    # Build decompose prompt
    decompose_msg = Message(
        role=Role.USER,
        content=(
            "This complex task has already run 4 tool rounds. "
            "Decompose the remaining work into up to 5 independent subtasks "
            "that can run in parallel. Return ONLY a JSON array like:\n"
            '[{"task":"description"},...]\n'
            "No markdown fences, no explanation."
        ),
    )
    temp_msgs = session.messages + [decompose_msg]

    if not text_mode and not stream_json:
        print_info("  ⇶ Auto-parallelizing: decomposing remaining work...")

    raw_json = ""
    try:
        async with asyncio.timeout(60):
            async for event in provider.stream(
                messages=temp_msgs,
                tools=[],
                system=system_prompt,
                max_tokens=4096,
            ):
                if event.type == "text":
                    raw_json += event.text
                elif event.type == "done":
                    break
                elif event.type == "error":
                    return False
    except Exception:
        return False

    # Parse JSON
    try:
        cleaned = raw_json.strip()
        if "```" in cleaned:
            cleaned = cleaned.split("```")[1].replace("json", "").strip()
        subtasks = json_mod.loads(cleaned)
        if not isinstance(subtasks, list):
            subtasks = [subtasks]
    except Exception:
        if not text_mode and not stream_json:
            print_info("  📋 Auto-parallelize: JSON parse failed, skipping")
        return False

    if len(subtasks) < 2:
        if not text_mode and not stream_json:
            print_info(f"  📋 Auto-parallelize: only {len(subtasks)} subtask(s), need >=2")
        return False

    # Run parallel via coordinator
    from ccb.coordinator import Coordinator

    coord = Coordinator(max_agents=len(subtasks))

    if not text_mode and not stream_json:
        print_info(f"  ⇶ Launching {len(subtasks)} parallel sub-agents")

    async def _executor(task_text: str) -> str:
        return await run_agent({"task": task_text}, registry, session.cwd)

    agents = [
        coord.create_agent(f"auto-{i+1}", prompt=st.get("task", str(st)) if isinstance(st, dict) else str(st))
        for i, st in enumerate(subtasks)
    ]
    results = await coord.run_parallel(agents, _executor)

    # Build merged result and inject into session
    merged = "Auto-parallelization results:\n\n" + "\n\n".join(
        f"### Subtask {i+1}: {agents[i].prompt}\n{r}"
        for i, r in enumerate(results)
    )

    session.add_assistant_message(
        f"Decomposed remaining work into {len(subtasks)} parallel subtasks."
    )
    session.add_user_message(merged)

    if not text_mode and not stream_json:
        print_info("  ⇶ Auto-parallelization complete. Resuming main agent...")

    return True


async def _run_task_planning(
    session: Session,
    provider: Provider,
    system_prompt: str,
    text_mode: bool,
    stream_json: bool,
) -> dict[str, Any] | None:
    """Analyze the latest user message and break it into granular steps.

    Returns a dict with complexity, total_steps, steps list.  Returns None
    on parse failure or if the message is too short.
    """
    last_content = (session.messages[-1].content or "").strip() if session.messages else ""
    if len(last_content) < 30:
        return None

    planning_msg = Message(
        role=Role.USER,
        content=(
            "You are a task planning expert. Decompose the user's request above into the smallest possible atomic executable steps.\n\n"
            "Each step must be a single operation, for example:\n"
            '- "Read file /path/to/file.py"\n'
            '- "Change function name foo to bar on line 12"\n'
            '- "Run test test_foo.py"\n'
            '- "Search for TODO markers in code"\n'
            '- "Check all references to variable bar"\n\n'
            "Principle: each step does one thing only. Never combine multiple operations into one step."
            "Finer steps mean higher completion and accuracy. Prefer more small steps over fewer combined ones.\n\n"
            "Complexity levels:\n"
            "- low: <= 3 steps, single file changes\n"
            "- medium: 4-7 steps, cross-file changes but simple logic\n"
            "- high: >= 8 steps, or involves architecture design, multi-module refactoring\n\n"
            "Return strict JSON format (no markdown code blocks):\n"
            '{"complexity":"low|medium|high","total_steps":N,"steps":["step 1","step 2",...]}'
        ),
    )

    temp_messages = session.messages + [planning_msg]

    if not text_mode and not stream_json:
        print_info("  📋 Analyzing task complexity...")

    raw = ""
    try:
        async with asyncio.timeout(30):
            async for event in provider.stream(
                messages=temp_messages,
                tools=[],
                system=system_prompt,
                max_tokens=2048,
            ):
                if event.type == "text":
                    raw += event.text
                elif event.type == "done":
                    break
                elif event.type == "error":
                    return None
    except Exception:
        return None

    # Parse JSON
    try:
        cleaned = raw.strip()
        if "```" in cleaned:
            parts = cleaned.split("```")
            for part in parts[1:]:
                part = part.replace("json", "").strip()
                if part.startswith("{"):
                    cleaned = part
                    break
        result = json_mod.loads(cleaned)
        if not isinstance(result.get("steps"), list):
            return None
        result["total_steps"] = len(result.get("steps", []))
        return result
    except Exception:
        return None


async def _run_parallel_from_plan(
    plan: dict[str, Any],
    session: Session,
    provider: Provider,
    registry: ToolRegistry,
    system_prompt: str,
    max_tokens: int,
    text_mode: bool,
    stream_json: bool,
) -> bool:
    """Execute a pre-made plan using parallel sub-agents.

    Returns True if parallel execution was performed.
    """
    steps = plan.get("steps", [])
    if len(steps) < 2:
        return False

    # Get parallel groups from plan, or auto-group
    groups = plan.get("parallel_groups")
    if not groups or not isinstance(groups, list):
        group_size = 2 if len(steps) <= 6 else 3
        groups = []
        for i in range(0, len(steps), group_size):
            groups.append(list(range(i, min(i + group_size, len(steps)))))

    if not text_mode and not stream_json:
        print_info(f"  ⇶ Auto-parallelizing {len(steps)} steps into {len(groups)} group(s)")

    from ccb.coordinator import Coordinator

    coord = Coordinator(max_agents=len(groups))

    async def _executor(task_text: str) -> str:
        return await run_agent({"task": task_text}, registry, session.cwd)

    agents = []
    for i, group in enumerate(groups):
        group_steps = [
            steps[idx]
            for idx in group
            if isinstance(idx, int) and idx < len(steps)
        ]
        if not group_steps:
            continue
        task_desc = "\n".join(f"- {s}" for s in group_steps)
        agent = coord.create_agent(f"plan-group-{i + 1}", prompt=task_desc)
        agents.append(agent)

    if len(agents) < 2:
        if not text_mode and not stream_json:
            print_info(f"  📋 Plan parallelization: only {len(agents)} agent(s) after grouping, need >=2")
        return False

    results = await coord.run_parallel(agents, _executor)

    merged_parts = []
    for i, (agent, result) in enumerate(zip(agents, results)):
        merged_parts.append(f"### Group {i + 1}: {agent.prompt}\n\n{result}")

    merged = "Task plan execution results:\n\n" + "\n\n".join(merged_parts)

    session.add_assistant_message(
        f"Based on the task plan ({len(steps)} steps, complexity={plan.get('complexity', '?')}), "
        f"I executed {len(agents)} parallel groups of subtasks."
    )
    session.add_user_message(merged)

    if not text_mode and not stream_json:
        print_info("  ⇶ Auto-parallelization complete. Resuming main agent...")

    return True
