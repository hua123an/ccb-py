# ccb-py 功能差异清单（对比原版 Claude Code）

> 更新日期: 2025-04-23
> 原版代码: ~434,000 行 TypeScript | ccb-py: ~23,800 行 Python (90 files)
> 命令覆盖: 112/110+ | 工具覆盖: 18/18 (100%) | 子系统覆盖: 40+/40 (100%)
> 测试: 21 test files, 433 tests, 3,661 lines

---

## ✅ 所有 P0/P1/P2 功能已实现

---

## 📊 已实现子系统完整清单

### 核心 (P0)
| # | 子系统 | 文件 | 说明 |
|---|--------|------|------|
| 1 | Git | `git_ops.py` (473L) | diff/log/blame/branch/commit/undo + merge/rebase/cherry-pick/conflict |
| 2 | GitHub | `github_ops.py` (492L) | PR CRUD + merge/review, issue CRUD + comment, releases, workflows, gists |
| 3 | OAuth | `oauth/` (3 files) | Auth Code PKCE, Device Code, Client Credentials + Keychain/libsecret/wincred/encrypted |
| 4 | Task Manager | `task_manager.py` (329L) | Worker pool, batch ops, subtasks, retry, isolation |
| 5 | MCP | `mcp/` (6 files) | Client (stdio+HTTP), auth, health, config validator, sampling, server |
| 6 | Compact | `compact.py` (537L) | Multi-round progressive, file ref, quality assessment, adaptive context |
| 7 | Memory | `memory.py` (346L) | Auto-extraction, decay pruning, context injection |

### 增强 (P1)
| # | 子系统 | 文件 | 说明 |
|---|--------|------|------|
| 8 | Vim | `vim_mode.py` (189L) | dd/yy/p/o/O/A/I/jj/ZZ/gcc + command parser |
| 9 | Keybinding | `keybinding.py` (148L) | Load/save/conflict detection/mode filtering |
| 10 | Buddy | `buddy_impl.py` (256L) | ASCII pets, XP/levels, mood/state persistence |
| 11 | Bridge/IDE | `bridge.py` (282L) | WS request/response, VS Code/JetBrains commands, file watch, diagnostics |
| 12 | Voice | `voice_input.py` (239L) | Whisper/sox/API backends, continuous listen, push-to-talk |
| 13 | Remote | `remote.py` (332L) | SCP transfer, tunnels, reverse tunnel, remote file R/W, SSH import |
| 14 | HTTP Server | `http_server.py` (223L) | REST API + WebSocket + real tool execution + auth middleware |
| 15 | State | `state.py` (160L) | Observable centralized state with listeners + persistence |
| 16 | Proactive | `proactive.py` (183L) | 10+ context-aware suggestion rules |
| 17 | Analytics | `analytics_tracker.py` (296L) | Langfuse generation/span/event, latency, CSV export |
| 18 | Query Engine | `query_engine.py` (166L) | Non-interactive pipe mode + PipeChain |
| 19 | Skill Search | `skill_search.py` (162L) | Keyword + context-based search/recommend |
| 20 | Settings Sync | `settings_sync.py` (150L) | Push/pull/diff file-based sync |

### 次要 (P2)
| # | 子系统 | 文件 | 说明 |
|---|--------|------|------|
| 21 | Commands | `commands.py` (3330L) | 112 slash commands (exceeds original 110) |
| 22 | Sandbox | `sandbox_exec.py` (295L) | Docker/macOS/firejail, command validation, resource limits |
| 23 | LSP | `lsp_client.py` (376L) | Hover, symbols, rename, format, code actions, lifecycle |
| 24 | UltraPlan | `ultraplan.py` (231L) | Dependency tracking, critical path, markdown rendering |
| 25 | Coordinator | `coordinator.py` (151L) | Multi-agent parallel/sequential/fan-out-merge |
| 26 | Daemon | `daemon_proc.py` (158L) | Background process with fork, periodic tasks |
| 27 | Migrations | `migrations.py` (147L) | Config v0→v1→v2 + plugin format migration |
| 28 | Installer | `installer.py` (143L) | bash/zsh/fish shell completions, PATH setup |

### 新增 (v2 补全)
| # | 子系统 | 文件 | 说明 |
|---|--------|------|------|
| 29 | AutoDream | `auto_dream.py` | Background memory consolidation, forked subagent, time/session gates |
| 30 | ContextCollapse | `context_collapse.py` | Collapse old context, keep head+tail, summary generation, overflow recovery |
| 31 | MagicDocs | `magic_docs.py` | Auto-maintain MAGIC DOC files via background subagent |
| 32 | Tips | `tips.py` | Tip registry + scheduler + history, contextual tips, cooldown |
| 33 | PolicyLimits | `policy_limits.py` | Org-level policy restrictions, ETag caching, fail-open |
| 34 | AgentSummary | `agent_summary.py` | Periodic background summarization for coordinator sub-agents |
| 35 | SessionTranscript | `session_transcript.py` | Export session as markdown transcript |
| 36 | ToolUseSummary | `tool_use_summary.py` | Haiku-generated one-line summary of tool batches |
| 37 | UpstreamProxy | `upstream_proxy.py` | HTTP proxy relay for API calls, retry, stats |
| 38 | Jobs | `jobs.py` | Template job queue (queued/running/waiting/error), persistence |
| 39 | Memdir | `memdir.py` | Team memory, scan, relevance scoring, context injection |

### 基础设施 (始终存在)
| # | 子系统 | 文件 | 说明 |
|---|--------|------|------|
| 40 | API | `api/` (3 providers) | Anthropic + OpenAI + Gemini/Grok, async streaming |
| 41 | Tools | `tools/` (18 tools) | bash, file_read/write/edit, grep, glob, agent, web_fetch/search, todo, notebook, etc |
| 42 | REPL | `repl.py` (1407L) | Full-screen prompt_toolkit, streaming, scroll, Ctrl+Y copy |
| 43 | Display | `display.py` (863L) | Rich Live, markdown→PTK fragments, URL detection |
| 44 | Permissions | `permissions.py` (316L) | Layered allow/deny, glob/regex, session/workspace persistence |
| 45 | Hooks | `hooks.py` | Pre/post tool_call, message, session start/end |
| 46 | Session | `session.py` | Save/load/list with full message serialization |
| 47 | Config | `config.py` | ~/.claude.json, settings.json, accounts.json, project configs |
| 48 | Plugins | `plugins.py` (880L) | Install/uninstall/enable/disable, marketplace, slash commands |

---

## 🎯 下一步

所有原版子系统已覆盖。后续可优化方向：
1. **集成测试** — 端到端测试覆盖
2. **性能优化** — 启动速度、流式延迟
3. **文档** — API 文档、用户指南
4. **打包发布** — PyPI 发布、安装脚本
