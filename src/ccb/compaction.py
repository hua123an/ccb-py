"""Conversation compaction for long agent sessions.

Summarizes and compresses old messages when context grows too large,
preventing token overflow in long-running agent conversations.

Inspired by OpenAI Agents SDK compaction system.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ccb.session import Message, Role, Session


@dataclass
class CompactionConfig:
    """Configuration for when and how to compact."""
    max_messages: int = 100
    max_tokens_estimate: int = 80_000
    keep_recent: int = 10
    summarize_threshold: int = 50


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English."""
    return len(text) // 4


def estimate_message_tokens(msg: Message) -> int:
    """Estimate tokens in a single message."""
    total = 0
    if msg.content:
        total += estimate_tokens(msg.content)
    if msg.tool_calls:
        for tc in msg.tool_calls:
            total += estimate_tokens(json.dumps(tc.input, ensure_ascii=False))
    if msg.tool_results:
        for tr in msg.tool_results:
            total += estimate_tokens(tr.content)
    return total


def estimate_session_tokens(session: Session) -> int:
    """Estimate total tokens in a session."""
    return sum(estimate_message_tokens(m) for m in session.messages)


def should_compact(session: Session, config: CompactionConfig | None = None) -> bool:
    """Check if session needs compaction."""
    cfg = config or CompactionConfig()
    msgs = session.messages
    if len(msgs) > cfg.max_messages:
        return True
    if estimate_session_tokens(session) > cfg.max_tokens_estimate:
        return True
    return False


def compact_messages(
    messages: list[Message],
    config: CompactionConfig | None = None,
) -> list[Message]:
    """Compact a message list by summarizing old messages.

    Keeps the first system message and the most recent N messages,
    replacing everything in between with a summary.
    """
    cfg = config or CompactionConfig()

    if len(messages) <= cfg.keep_recent:
        return messages

    # Split: old messages to summarize, recent to keep
    split_point = len(messages) - cfg.keep_recent
    old_messages = messages[:split_point]
    recent_messages = messages[split_point:]

    # Build summary of old messages
    summary_parts = []
    tool_count = 0
    user_msgs = 0
    assistant_msgs = 0

    for msg in old_messages:
        if msg.role == Role.USER:
            user_msgs += 1
            if msg.content and len(msg.content) > 50:
                # Extract key points from user messages
                preview = msg.content[:200].replace("\n", " ")
                summary_parts.append(f"User asked: {preview}...")
        elif msg.role == Role.ASSISTANT:
            assistant_msgs += 1
            if msg.content and len(msg.content) > 50:
                preview = msg.content[:200].replace("\n", " ")
                summary_parts.append(f"Assistant: {preview}...")
        elif msg.role == Role.TOOL_RESULT:
            tool_count += 1

    summary_lines = [
        f"[Conversation compaction: {user_msgs} user messages, {assistant_msgs} assistant messages, {tool_count} tool results summarized]",
    ]
    if summary_parts:
        # Keep last 5 key points
        summary_lines.extend(summary_parts[-5:])

    summary_text = "\n".join(summary_lines)

    # Create summary message
    summary_msg = Message(
        role=Role.USER,
        content=summary_text,
    )

    return [summary_msg] + recent_messages


async def compact_session(
    session: Session,
    provider: Any | None = None,
    config: CompactionConfig | None = None,
) -> int:
    """Compact a session in-place. Returns number of messages removed.

    If provider is given, uses it to generate a proper summary.
    Otherwise uses heuristic extraction.
    """
    cfg = config or CompactionConfig()

    if not should_compact(session, cfg):
        return 0

    old_count = len(session.messages)

    if provider and old_count > cfg.summarize_threshold:
        # Use LLM to generate a proper summary
        summary = await _llm_summarize(session, provider, cfg)
        if summary:
            # Replace old messages with summary
            split_point = len(session.messages) - cfg.keep_recent
            session.messages = [
                Message(role=Role.USER, content=f"[Compacted summary]\n{summary}"),
            ] + session.messages[split_point:]
            return old_count - len(session.messages)

    # Heuristic compaction
    session.messages = compact_messages(session.messages, cfg)
    return old_count - len(session.messages)


async def _llm_summarize(
    session: Session,
    provider: Any,
    config: CompactionConfig,
) -> str | None:
    """Use LLM to generate a summary of old messages."""
    split_point = len(session.messages) - config.keep_recent
    old_messages = session.messages[:split_point]

    # Build a text representation of old messages
    msg_texts = []
    for msg in old_messages:
        if msg.role == Role.USER and msg.content:
            msg_texts.append(f"User: {msg.content[:500]}")
        elif msg.role == Role.ASSISTANT and msg.content:
            msg_texts.append(f"Assistant: {msg.content[:500]}")

    if not msg_texts:
        return None

    conversation_text = "\n\n".join(msg_texts[-20:])  # Last 20 messages for context

    prompt = (
        "Summarize the following conversation in 3-5 sentences, preserving key decisions, "
        "file changes, and current task state:\n\n"
        f"{conversation_text}"
    )

    try:
        from ccb.session import Session as TempSession
        temp_session = TempSession()
        temp_session.add_user_message(prompt)

        from ccb.loop import run_turn
        from ccb.tools.base import ToolRegistry
        result = await run_turn(
            provider=provider,
            session=temp_session,
            registry=ToolRegistry(),
            system_prompt="You are a summarizer. Be concise.",
            max_tool_rounds=1,
        )

        for msg in reversed(temp_session.messages):
            if msg.role == Role.ASSISTANT and msg.content:
                return msg.content
    except Exception:
        pass

    return None
