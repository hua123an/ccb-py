"""Model-related slash commands."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ccb.display import repl_console as console, print_info

if TYPE_CHECKING:
    from ccb.api.base import Provider
    from ccb.mcp.client import MCPManager
    from ccb.session import Session


async def cmd_model(
    args: str,
    session: Session,
    provider: Provider,
) -> bool:
    """Handle /model command."""
    from ccb.config import get_active_account, switch_account

    def _switch_model(new_model: str) -> None:
        """Switch model on current account — no cross-account routing."""
        session.model = new_model
        provider.set_model(new_model)
        acct = get_active_account()
        acct_name = acct.get("_name", "?") if acct else "?"
        switch_account(acct_name, new_model)
        print_info(f"Model → {new_model} (account: {acct_name})")

    if args:
        _switch_model(args)
        return True

    # Fetch models from current active account only
    import httpx
    from ccb.config import load_accounts
    from ccb.select_ui import select_one

    acct = get_active_account()
    acct_name = acct.get("_name", "") if acct else ""
    store = load_accounts()
    profile = store.get("accounts", {}).get(acct_name, {})

    # Try remote /models first
    base = profile.get("baseUrl", "").rstrip("/")
    key = profile.get("apiKey", "")
    models: list[str] = []
    if base and key:
        headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
        urls = [f"{base}/models"]
        if not base.endswith("/v1"):
            urls.append(f"{base}/v1/models")
        print_info(f"Loading models from {acct_name}...")
        for url in urls:
            try:
                async with httpx.AsyncClient(timeout=8) as client:
                    resp = await client.get(url, headers=headers)
                    if resp.status_code == 200:
                        data = resp.json()
                        models = sorted(m["id"] for m in data.get("data", []) if "id" in m)
                        break
            except Exception:
                continue
    if not models:
        models = profile.get("models", [])

    if not models:
        print_info(f"Current model: {session.model}")
        print_info("Tip: /model <name> to switch")
    else:
        items = []
        active_idx = 0
        for i, m in enumerate(models):
            if m == session.model:
                active_idx = i
            items.append({
                "label": m,
                "description": "← current" if m == session.model else "",
            })
        choice = await select_one(
            items,
            title=f"{acct_name} — {len(models)} models",
            active=active_idx,
            searchable=True,
            search_placeholder="Search models",
            visible_count=15,
        )
        if choice is not None:
            _switch_model(models[choice])
    return True


async def cmd_effort(args: str, state: dict[str, Any]) -> bool:
    """Handle /effort command."""
    levels = ["low", "medium", "high"]
    descs = {"low": "Faster, less thorough", "medium": "Balanced", "high": "Thorough, slower"}
    if args and args in levels:
        state["effort"] = args
        print_info(f"Effort level: {args}")
        return True
    from ccb.select_ui import select_one
    current = state.get("effort", "high")
    active_idx = levels.index(current) if current in levels else 2
    items = [{"label": lv, "description": descs[lv] + (" ← current" if lv == current else "")} for lv in levels]
    choice = await select_one(items, title="Select Effort Level", active=active_idx)
    if choice is not None:
        state["effort"] = levels[choice]
        print_info(f"Effort level: {levels[choice]}")
    return True


def cmd_fast(state: dict[str, Any]) -> bool:
    """Handle /fast command."""
    state["fast"] = not state.get("fast", False)
    print_info(f"Fast mode: {'ON' if state['fast'] else 'OFF'}")
    return True


def cmd_thinking(
    args: str,
    state: dict[str, Any],
    provider: Provider,
) -> bool:
    """Handle /thinking command."""
    if provider.capabilities.supports_thinking:
        arg_lower = args.strip().lower() if args else ""
        # /thinking adaptive [budget]
        if arg_lower.startswith("adaptive"):
            rest = arg_lower[len("adaptive"):].strip()
            budget = int(rest) if rest.isdigit() else 10000
            state["thinking"] = True
            state["thinking_mode"] = "adaptive"
            provider.set_thinking(True, budget, mode="adaptive")
            print_info(f"Extended thinking: ADAPTIVE (budget: {budget:,} tokens)")
        # /thinking on [budget]
        elif arg_lower.startswith("on"):
            rest = arg_lower[len("on"):].strip()
            budget = int(rest) if rest.isdigit() else 10000
            state["thinking"] = True
            state["thinking_mode"] = "on"
            provider.set_thinking(True, budget, mode="on")
            print_info(f"Extended thinking: ON (budget: {budget:,} tokens)")
        # /thinking off
        elif arg_lower == "off":
            state["thinking"] = False
            state["thinking_mode"] = "off"
            provider.set_thinking(False)
            print_info("Extended thinking: OFF")
        # /thinking [budget] — toggle or set budget
        else:
            budget = int(arg_lower) if arg_lower.isdigit() else 10000
            if state.get("thinking"):
                state["thinking"] = False
                state["thinking_mode"] = "off"
                provider.set_thinking(False)
                print_info("Extended thinking: OFF")
            else:
                state["thinking"] = True
                state["thinking_mode"] = "on"
                provider.set_thinking(True, budget)
                print_info(f"Extended thinking: ON (budget: {budget:,} tokens)")
    else:
        model_name = getattr(provider, "_model", "")
        print_info(
            f"Model '{model_name}' does not support thinking/reasoning. "
            "Supported: Claude models, o1/o3/o4 series, gpt-5+."
        )
    return True


def cmd_prefill(args: str, state: dict[str, Any]) -> bool:
    """Handle /prefill command."""
    if args:
        state["prefill"] = args
        print_info(f"Prefill set: \"{args}\"  (will be used for the next message, then cleared)")
    else:
        current = state.get("prefill", "")
        if current:
            state.pop("prefill", None)
            print_info("Prefill cleared.")
        else:
            print_info(
                "Usage: /prefill <text>\n"
                "  The model will start its next reply with <text> and continue from there.\n"
                "  Anthropic protocol: natively supported.\n"
                "  OpenAI protocol: injected as trailing assistant message (effective on Claude relays).\n"
                "  Run /prefill again with no argument to clear."
            )
    return True


def cmd_cost(session: Session) -> bool:
    """Handle /cost command."""
    from ccb.cost_tracker import format_tokens, format_cost, get_cost_state, context_percentage
    from ccb.model_limits import get_context_limit
    cs = get_cost_state()
    inp = cs.total_input_tokens or session.total_input_tokens
    out = cs.total_output_tokens or session.total_output_tokens
    total = inp + out
    ctx_limit = get_context_limit(session.model)
    pct = context_percentage(session.last_input_tokens, ctx_limit)
    console.print(f"  Input tokens:  {format_tokens(inp)} ({inp:,})")
    console.print(f"  Output tokens: {format_tokens(out)} ({out:,})")
    console.print(f"  Total tokens:  {format_tokens(total)} ({total:,})")
    console.print(f"  Context:       {pct}% ({format_tokens(session.last_input_tokens)}/{format_tokens(ctx_limit)})")
    console.print(f"  Est. cost:     {format_cost(cs.total_cost_usd)}")
    if cs.last_turn_duration_ms:
        from ccb.cost_tracker import format_duration
        console.print(f"  Last turn:     {format_duration(cs.last_turn_duration_ms)}")
    return True


def cmd_budget(args: str, state: dict[str, Any], session: Session) -> bool:
    """Handle /budget command."""
    if args:
        try:
            limit = int(args)
            state["token_budget"] = limit
            print_info(f"Token budget set to {limit:,}")
        except ValueError:
            from ccb.display import print_error
            print_error("Usage: /budget <number>")
    else:
        budget = state.get("token_budget")
        if budget:
            used = session.total_input_tokens + session.total_output_tokens
            console.print(f"  Budget: {budget:,} tokens")
            console.print(f"  Used:   {used:,} tokens ({used * 100 // budget}%)")
        else:
            console.print("  No budget set. Use /budget <number>")
    return True


def cmd_context(session: Session, provider: Provider) -> bool:
    """Handle /context command."""
    from ccb.model_limits import get_context_limit
    msg_count = len(session.messages)
    chars = sum(len(m.content) for m in session.messages)
    current_ctx = session.last_input_tokens
    ctx_limit = get_context_limit(session.model or getattr(provider, "_model", ""))
    compact_at = int(ctx_limit * 0.8)
    console.print(f"  Messages:        {msg_count}")
    console.print(f"  Characters:      {chars:,}")
    if current_ctx:
        used_pct = (current_ctx / ctx_limit * 100) if ctx_limit else 0
        console.print(
            f"  Current context: [bold]{current_ctx:,}[/bold] / {ctx_limit:,} tokens "
            f"([dim]{used_pct:.1f}%[/dim]) — last request"
        )
    else:
        console.print(f"  Current context: [dim]—[/dim] / {ctx_limit:,} tokens (no request yet)")
    console.print(f"  Auto-compact at: {compact_at:,} tokens (80%)")
    console.print(f"  Cumulative in:   {session.total_input_tokens:,} tokens")
    console.print(f"  Cumulative out:  {session.total_output_tokens:,} tokens")
    return True


def cmd_status(
    session: Session,
    provider: Provider,
    mcp_manager: MCPManager | None,
) -> bool:
    """Handle /status command."""
    from ccb.config import get_model, get_provider, get_active_account
    from ccb.cost_tracker import (
        get_cost_state, format_tokens, format_cost, format_duration,
        context_percentage,
    )
    from ccb.model_limits import get_context_limit
    acct = get_active_account()
    model = get_model()
    cost = get_cost_state()
    ctx_limit = get_context_limit(model)
    ctx_used = session.last_input_tokens
    ctx_pct = context_percentage(ctx_used, ctx_limit)
    console.print(f"  Account:  {acct.get('_name') if acct else 'none'}")
    console.print(f"  Provider: {get_provider()}")
    console.print(f"  Model:    {model}")
    console.print(f"  Session:  {session.id[:8]}...")
    console.print(f"  Messages: {len(session.messages)}")
    console.print(
        f"  Context:  {ctx_pct}% ({format_tokens(ctx_used)}/{format_tokens(ctx_limit)})"
    )
    console.print(
        f"  Tokens:   {format_tokens(cost.total_input_tokens)} in / "
        f"{format_tokens(cost.total_output_tokens)} out"
    )
    if cost.total_cost_usd > 0:
        console.print(f"  Cost:     {format_cost(cost.total_cost_usd)}")
    if cost.last_turn_duration_ms > 0:
        console.print(f"  Last turn:{format_duration(cost.last_turn_duration_ms)}")
    if mcp_manager:
        mcp_count = sum(1 for s in mcp_manager.servers.values() if s.connected)
        console.print(f"  MCP:      {mcp_count} server(s)")
    return True


def cmd_files(session: Session) -> bool:
    """Handle /files command."""
    file_set: set[str] = set()
    for msg in session.messages:
        if msg.tool_calls:
            for tc in msg.tool_calls:
                fp = tc.input.get("file_path") or tc.input.get("path") or ""
                if fp:
                    file_set.add(fp)
    if file_set:
        console.print("[bold]Files in context:[/bold]")
        for f in sorted(file_set):
            console.print(f"  {f}")
    else:
        console.print("  No files referenced yet.")
    return True


async def cmd_memory(
    args: str,
    session: Session,
    provider: Provider,
    cwd: str,
) -> bool:
    """Handle /memory command."""
    from ccb.memory import get_store, get_extractor, build_memory_context, apply_decay

    mem_store = get_store()
    sub = args.split(maxsplit=1) if args else []
    sub_cmd = sub[0].lower() if sub else ""
    sub_arg = sub[1] if len(sub) > 1 else ""

    if sub_cmd == "add" and sub_arg:
        # /memory add <text> [#tag1 #tag2]
        parts_m = sub_arg.split("#")
        content = parts_m[0].strip()
        tags = [tg.strip() for tg in parts_m[1:] if tg.strip()]
        mem = mem_store.add(content, tags=tags, source=session.id)
        print_info(f"Memory added: {mem.id} ({len(content)} chars, {len(tags)} tags)")

    elif sub_cmd == "list":
        tag_filter = sub_arg.strip() if sub_arg else None
        memories = mem_store.list_all(tag=tag_filter)
        if not memories:
            console.print("  No memories stored.")
        else:
            console.print(f"  [bold]{len(memories)} memories:[/bold]")
            for mem_obj in memories[:20]:
                tags_str = f" [dim][{', '.join(mem_obj.tags)}][/dim]" if mem_obj.tags else ""
                console.print(f"  • {mem_obj.content[:80]}{'...' if len(mem_obj.content) > 80 else ''}{tags_str}")
                console.print(f"    [dim]{mem_obj.id}  accessed {mem_obj.access_count}x[/dim]")

    elif sub_cmd == "search" and sub_arg:
        results = mem_store.search(sub_arg)
        if not results:
            console.print(f"  No memories matching '{sub_arg}'.")
        else:
            console.print(f"  [bold]{len(results)} matches:[/bold]")
            for mem_obj in results:
                console.print(f"  • {mem_obj.content[:80]}{'...' if len(mem_obj.content) > 80 else ''}")

    elif sub_cmd == "delete" and sub_arg:
        if mem_store.delete(sub_arg.strip()):
            print_info(f"Memory deleted: {sub_arg.strip()}")
        else:
            print_info(f"Memory not found: {sub_arg.strip()}")

    elif sub_cmd == "extract":
        # Extract memories from current conversation
        extractor = get_extractor()
        extractor.set_provider(provider)
        print_info("Extracting memories from this conversation...")
        extracted = await extractor.extract_from_session(session.messages, session.id)
        if extracted:
            print_info(f"Extracted {len(extracted)} memories:")
            for mem_obj in extracted:
                console.print(f"  • {mem_obj.content[:80]}")
        else:
            print_info("No new memories extracted.")

    elif sub_cmd == "clear":
        count = mem_store.clear()
        print_info(f"Cleared {count} memories.")

    elif sub_cmd == "decay":
        pruned = apply_decay(mem_store)
        print_info(f"Decay applied. Pruned {pruned} stale memories.")

    elif sub_cmd == "context":
        ctx = build_memory_context(limit=15)
        if ctx:
            console.print(ctx)
        else:
            console.print("  No memory context (empty store).")

    elif sub_cmd == "analyze":
        from ccb.memory import analyze_memories
        report = analyze_memories(mem_store, cwd)
        console.print(report)

    elif sub_cmd == "mermaid":
        # Generate Mermaid diagram from memories (TencentDB-style symbolic compression)
        canvas = mem_store.generate_mermaid_canvas()
        console.print("  [bold]📊 Memory Canvas (Mermaid Diagram)[/bold]")
        console.print("  [dim]Visual representation of memory layer structure[/dim]")
        console.print()
        console.print(f"```mermaid\n{canvas}\n```")

    elif sub_cmd == "trace" and sub_arg:
        # Trace path for a memory (node_id + evidence_refs)
        path = mem_store.get_trace_path(sub_arg.strip())
        if path:
            console.print(f"  [bold]🔍 Trace Path for {sub_arg.strip()}:[/bold]")
            for i, node in enumerate(path):
                indent = "  " * i
                console.print(f"{indent}• {node}")
        else:
            console.print(f"  Memory not found: {sub_arg.strip()}")

    elif sub_cmd == "offload":
        # Check context offload threshold
        ctx = getattr(session, 'last_input_tokens', 0) or 0
        from ccb.model_limits import get_context_limit
        model_name = getattr(provider, '_model', '') if provider else ''
        ctx_limit = get_context_limit(model_name)
        ratio = ctx / ctx_limit if ctx_limit else 0
        should, reason = mem_store.check_offload_threshold(ratio)
        console.print("  [bold]📦 Context Offload Status[/bold]")
        console.print(f"  Context usage: {ctx:,} / {ctx_limit:,} ({ratio*100:.1f}%)")
        if should:
            console.print(f"  ⚠️  Should offload: {reason}")
        else:
            console.print(f"  ✅ No offload needed ({ratio*100:.1f}% < 50%)")

    elif sub_cmd == "layers":
        # Show memories by layer (L0-L4)
        console.print("  [bold]🏷️ Memories by Layer[/bold]")
        for layer in ["L0", "L1", "L2", "L3", "L4"]:
            mems = mem_store.get_by_layer(layer)
            layer_desc = {
                "L0": "Raw conversation",
                "L1": "Atomic facts",
                "L2": "Scenario patterns",
                "L3": "Persona (user profile)",
                "L4": "Skill (SOP)",
            }.get(layer, "")
            console.print(f"  {layer}: {len(mems)} memories [dim]{layer_desc}[/dim]")
            for m in mems[:3]:
                console.print(f"    • {m.content[:60]}...")
            if len(mems) > 3:
                console.print(f"    [dim]... and {len(mems) - 3} more[/dim]")

    else:
        # Default: show summary + CLAUDE.md
        console.print(f"  [bold]Memory store:[/bold] {mem_store.count} memories")
        from ccb.prompts import _find_claude_md
        mds = _find_claude_md(cwd)
        if mds:
            for md in mds:
                console.print(f"  📝 {md}")
        console.print()
        console.print("  [dim]Subcommands: add, list, search, delete, extract, clear, decay, context, analyze, mermaid, trace, offload, layers[/dim]")
        console.print("  [dim]Example: /memory add User prefers Python #preference #python[/dim]")
    return True


async def _compact(session: Session, provider: Provider, focus: str = "") -> None:
    """Compact conversation using official structured prompt (analysis+summary)."""
    from ccb.api.base import Message, Role
    from ccb.compact import (
        get_compact_prompt,
        get_compact_system,
        get_compact_user_message,
    )
    from ccb.display import print_error, print_info

    if len(session.messages) < 4:
        print_info("Not enough messages to compact.")
        return

    print_info("Compacting conversation...")

    # Build the structured compact prompt (mirrors official compact/prompt.ts)
    compact_prompt = get_compact_prompt(custom_instructions=focus)

    # Send all existing messages + the compact instruction as the last user turn
    summary_messages = session.messages.copy()
    summary_messages.append(Message(role=Role.USER, content=compact_prompt))

    full_text = ""
    async for event in provider.stream(
        messages=summary_messages,
        tools=[],
        system=get_compact_system(),
        max_tokens=8192,
    ):
        if event.type == "text":
            full_text += event.text

    if full_text:
        # Format: strip <analysis>, keep <summary> content
        continuation_msg = get_compact_user_message(
            full_text, suppress_follow_up=True
        )
        old_count = len(session.messages)
        old_ctx = session.last_input_tokens

        session.messages.clear()
        session.messages.append(Message(
            role=Role.USER,
            content=continuation_msg,
        ))
        session.messages.append(Message(
            role=Role.ASSISTANT,
            content="Understood. I have full context from the summary above. Continuing where we left off.",
        ))
        from ccb.cost_tracker import format_tokens
        ctx_info = f" (was {format_tokens(old_ctx)})" if old_ctx > 0 else ""
        print_info(f"Compacted {old_count} messages → 2{ctx_info}")
    else:
        print_error("Compaction failed - no summary generated")
