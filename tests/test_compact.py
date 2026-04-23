"""Tests for ccb.compact module."""
import pytest

from ccb.compact import (
    get_compact_prompt,
    get_compact_system,
    format_compact_summary,
    get_compact_user_message,
    FileReferenceCompressor,
    CompactQualityAssessor,
    AdaptiveContextManager,
)


class TestCompactPrompt:
    def test_basic_prompt(self):
        prompt = get_compact_prompt()
        assert "summary" in prompt.lower()
        assert "analysis" in prompt.lower()
        assert "Do NOT call any tools" in prompt

    def test_with_instructions(self):
        prompt = get_compact_prompt("Focus on TypeScript changes")
        assert "TypeScript" in prompt

    def test_system_message(self):
        sys = get_compact_system()
        assert "summarizer" in sys.lower()
        assert "tool" in sys.lower()


class TestFormatSummary:
    def test_strip_analysis(self):
        raw = "<analysis>my thoughts</analysis>\n<summary>the result</summary>"
        result = format_compact_summary(raw)
        assert "<analysis>" not in result
        assert "the result" in result

    def test_no_analysis(self):
        raw = "<summary>just summary</summary>"
        result = format_compact_summary(raw)
        assert "just summary" in result

    def test_plain_text(self):
        raw = "No tags at all, just text"
        result = format_compact_summary(raw)
        assert raw in result


class TestCompactUserMessage:
    def test_basic(self):
        msg = get_compact_user_message("Summary content here")
        assert "continued from a previous conversation" in msg
        assert "Summary content here" in msg

    def test_suppress_follow_up(self):
        msg = get_compact_user_message("Summary", suppress_follow_up=True)
        assert "Resume directly" in msg

    def test_no_suppress(self):
        msg = get_compact_user_message("Summary", suppress_follow_up=False)
        assert "Resume directly" not in msg


class TestFileReferenceCompressor:
    def test_small_block_unchanged(self):
        comp = FileReferenceCompressor(max_file_lines=10, max_file_chars=500)
        content = "```python\nprint('hello')\n```"
        result = comp._compress_content(content)
        assert result == content

    def test_large_block_compressed(self):
        comp = FileReferenceCompressor(max_file_lines=4, max_file_chars=50)
        lines = "\n".join([f"line {i}" for i in range(20)])
        content = f"```python\n{lines}\n```"
        result = comp._compress_content(content)
        assert "omitted" in result

    def test_tool_result_compression(self):
        comp = FileReferenceCompressor(max_file_chars=50)
        content = "<tool_result>" + "x" * 500 + "</tool_result>"
        result = comp._compress_content(content)
        assert "truncated" in result


class TestCompactQualityAssessor:
    def test_good_summary(self):
        summary = """
1. Primary Request and Intent:
   Build a REST API

2. Key Technical Concepts:
   - FastAPI
   - SQLAlchemy

3. Files and Code Sections:
   - main.py
   ```python
   app = FastAPI()
   ```

4. Errors and fixes:
   - Import error: fixed by installing package

5. Problem Solving:
   Resolved database connection

6. All user messages:
   - "Build me an API"

7. Pending Tasks:
   - Add authentication

8. Current Work:
   Working on endpoints

9. Optional Next Step:
   Add auth middleware
"""
        assessor = CompactQualityAssessor()
        result = assessor.assess(summary)
        assert result["score"] >= 0.7
        assert result["grade"] in ("A", "B")

    def test_poor_summary(self):
        assessor = CompactQualityAssessor()
        result = assessor.assess("Too short")
        assert result["score"] < 0.5
        assert result["grade"] in ("C", "D")


class TestAdaptiveContextManager:
    def test_should_not_compact_low_usage(self):
        mgr = AdaptiveContextManager(max_context_tokens=200000)
        assert mgr.should_compact(50000) == "none"

    def test_should_soft_compact(self):
        mgr = AdaptiveContextManager(max_context_tokens=200000)
        assert mgr.should_compact(130000) == "soft"

    def test_should_aggressive_compact(self):
        mgr = AdaptiveContextManager(max_context_tokens=200000)
        assert mgr.should_compact(160000) == "aggressive"

    def test_should_emergency_compact(self):
        mgr = AdaptiveContextManager(max_context_tokens=200000)
        assert mgr.should_compact(185000) == "emergency"

    def test_strategy(self):
        mgr = AdaptiveContextManager(max_context_tokens=200000)
        s = mgr.compact_strategy(185000, 50)
        assert s["action"] == "emergency"
        assert s["compress_files"] is True
        assert s["slim_tools"] is True
        assert s["target_ratio"] < 0.3
