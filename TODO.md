# ccb-py 功能差异清单（对比原版 Claude Code）

> 生成日期: 2025-04-23
> 原版代码: ~434,000 行 TypeScript | ccb-py: ~11,800 行 Python
> 命令覆盖: 72/110 (65%) | 工具覆盖: 15/18 (83%) | 子系统覆盖: 8/40 (20%)

---

## 🔴 P0 — 核心功能缺失

### 1. Git 集成
- **原版**: `src/utils/git/` (3 files) + 多个命令
- **缺失**:
  - [ ] `git diff` 解析和展示
  - [ ] `git log` 集成
  - [ ] `git blame` 集成
  - [ ] `/commit` — 自动生成 commit message 并执行
  - [ ] `/branch` — 真正的分支切换/创建
  - [ ] `/diff` — 显示工作区变更
  - [ ] `/undo` `/redo` — 基于 git 的撤销/重做（目前是存根）

### 2. GitHub 集成
- **原版**: `src/utils/github/` + 多个命令
- **缺失**:
  - [ ] `/pr-comments` — 获取 PR 评论（标记 "not yet implemented"）
  - [ ] `/review` — 代码审查
  - [ ] `/issue` — GitHub Issue 创建/管理
  - [ ] `/autofix-pr` — PR 自动修复
  - [ ] `/install-github-app` — GitHub App 安装
  - [ ] `/install-slack-app` — Slack App 安装
  - [ ] GitHub API 认证和调用基础设施

### 3. OAuth / 认证系统
- **原版**: `src/services/oauth/` (12 files) + `src/utils/secureStorage/`
- **缺失**:
  - [ ] OAuth 2.0 认证流程
  - [ ] `/login` — 完整登录流程（当前可能是存根）
  - [ ] `/logout` — 完整登出
  - [ ] `/account` — 账户信息展示
  - [ ] Token 安全存储 (Keychain/libsecret)
  - [ ] `/oauth-refresh` — Token 刷新

### 4. 多任务并行系统
- **原版**: `src/tasks/` (14 files) + `src/Task.ts` + `src/tasks.ts`
- **缺失**:
  - [ ] 多任务并行调度器
  - [ ] 任务队列和优先级
  - [ ] `/tasks` — 任务列表/管理（当前是存根）
  - [ ] 任务间上下文隔离
  - [ ] 任务进度跟踪

### 5. MCP 完整实现
- **原版**: `src/services/mcp/` (42 files)
- **现有**: `src/ccb/mcp/client.py` (1 file, 378 行)
- **缺失**:
  - [ ] HTTP/SSE transport
  - [ ] Streamable HTTP transport
  - [ ] MCP Server 模式（被其他客户端调用）
  - [ ] Resource 订阅和通知
  - [ ] MCP Auth (OAuth for MCP)
  - [ ] Sampling 支持
  - [ ] MCP 配置 UI 和验证
  - [ ] 连接重试和健康检查

### 6. Compact 完整实现
- **原版**: `src/services/compact/` (26 files)
- **现有**: `src/ccb/compact.py` (206 行)
- **缺失**:
  - [ ] 多轮渐进式压缩
  - [ ] 工具提示精简策略
  - [ ] 上下文窗口自适应
  - [ ] 文件内容引用压缩
  - [ ] 压缩质量评估

### 7. Session Memory
- **原版**: `src/services/SessionMemory/` (3 files) + `src/services/extractMemories/`
- **缺失**:
  - [ ] 跨会话记忆提取
  - [ ] 记忆索引和检索
  - [ ] `/memory` 命令完整后端
  - [ ] 自动记忆提取 (extractMemories)

---

## 🟡 P1 — 增强功能缺失

### 8. Vim 模式
- **原版**: `src/vim/` (5 files)
- **现有**: `vi_mode` toggle 开关
- **缺失**:
  - [ ] 完整的 Vim 键绑定映射
  - [ ] Normal/Insert/Visual 模式指示
  - [ ] Vim 命令行 (`:w`, `:q` 等)
  - [ ] `/vim` 设置交互界面

### 9. 键绑定系统
- **原版**: `src/keybindings/` (16 files)
- **缺失**:
  - [ ] 自定义键绑定引擎
  - [ ] 键绑定配置文件
  - [ ] `/keybindings` 交互式配置
  - [ ] 键冲突检测
  - [ ] 多模式键绑定 (normal/insert/command)

### 10. Buddy 系统
- **原版**: `src/buddy/` (8 files)
- **缺失**:
  - [ ] 编程伙伴/虚拟宠物
  - [ ] `/buddy` 完整实现
  - [ ] 伙伴状态和动画
  - [ ] 伙伴交互逻辑

### 11. Bridge/IDE 集成
- **原版**: `src/bridge/` (19 files)
- **缺失**:
  - [ ] VS Code 扩展通信协议
  - [ ] JetBrains IDE 集成
  - [ ] `/ide` 命令后端
  - [ ] `/desktop` 命令后端
  - [ ] 编辑器状态同步
  - [ ] 文件变更监听和推送

### 12. Voice 语音
- **原版**: `src/voice/` (独立模块)
- **缺失**:
  - [ ] `/voice` 语音输入
  - [ ] 语音转文字 (STT)
  - [ ] 音频录制/处理

### 13. Remote/SSH
- **原版**: `src/remote/` (4 files) + `src/ssh/` (5 files)
- **缺失**:
  - [ ] SSH 隧道连接
  - [ ] 远程开发环境支持
  - [ ] `/remote-env` `/remote-setup` 命令
  - [ ] 远程文件系统操作

### 14. Server 模式
- **原版**: `src/server/` (10 files)
- **缺失**:
  - [ ] HTTP API 服务器模式
  - [ ] IDE 插件调用接口
  - [ ] WebSocket 实时通信
  - [ ] 多客户端连接管理

### 15. State 管理
- **原版**: `src/state/` (8 files)
- **缺失**:
  - [ ] 集中式应用状态管理
  - [ ] 状态持久化
  - [ ] 状态变更通知
  - 当前: 散落的全局变量和模块级状态

### 16. Proactive 提示
- **原版**: `src/proactive/` (2 files) + `src/services/PromptSuggestion/`
- **缺失**:
  - [ ] 主动提示建议
  - [ ] 基于上下文的智能建议
  - [ ] 提示模板推荐

### 17. Analytics / Telemetry
- **原版**: `src/services/analytics/` (10 files) + Langfuse
- **缺失**:
  - [ ] 使用统计收集
  - [ ] Langfuse 链路追踪
  - [ ] `/ant-trace` 追踪查看
  - [ ] 性能指标上报

### 18. Query Engine
- **原版**: `src/query/` (5 files) + `src/QueryEngine.ts`
- **缺失**:
  - [ ] 非交互式查询管道 (`-p` flag 后端)
  - [ ] 管道模式 (stdin/stdout)
  - [ ] `/pipes` 管道管理

### 19. Skill Search
- **原版**: `src/services/skillSearch/` (7 files)
- **缺失**:
  - [ ] 技能语义搜索引擎
  - [ ] 技能推荐
  - [ ] 技能排名

### 20. Settings Sync
- **原版**: `src/services/settingsSync/` + `src/services/remoteManagedSettings/`
- **缺失**:
  - [ ] 云端设置同步
  - [ ] 远程管理设置
  - [ ] 团队设置共享 (`teamMemorySync`)

---

## 🟢 P2 — 次要/边缘功能缺失

### 21. 缺失的命令 (~44 个)

**开发工作流**:
- [ ] `/attach` `/detach` — 附加/分离会话
- [ ] `/fork` — 会话分叉
- [ ] `/resume` — 恢复会话（区别于 /restore）
- [ ] `/send` — 发送消息到其他会话
- [ ] `/peers` — 对等连接

**代码质量**:
- [ ] `/bughunter` — 自动 bug 检测
- [ ] `/review` — 代码审查
- [ ] `/autofix-pr` — PR 自动修复
- [ ] `/perf-issue` — 性能问题分析

**调试/内部**:
- [ ] `/ant-trace` — Anthropic 追踪
- [ ] `/debug-tool-call` — 工具调用调试
- [ ] `/ctx_viz` — 上下文可视化
- [ ] `/heapdump` — 内存转储
- [ ] `/mock-limits` — 模拟限制
- [ ] `/break-cache` — 缓存清除
- [ ] `/claim-main` — 主进程声明

**环境/配置**:
- [ ] `/env` — 环境变量管理
- [ ] `/exit` — 退出（有快捷键但无命令）
- [ ] `/onboarding` — 首次使用引导
- [ ] `/feedback` — 反馈提交
- [ ] `/btw` — 终端环境分享

**平台集成**:
- [ ] `/teleport` — 会话传送
- [ ] `/pipe-status` — 管道状态
- [ ] `/good-claude` `/poor` — 评价反馈

**娱乐/社交**:
- [ ] `/thinkback` `/thinkback-play` — 年度回顾
- [ ] `/agents-platform` — 平台 Agent
- [ ] `/assistant` — 助手模式

### 22. Sandbox
- **原版**: `src/utils/sandbox/`
- **缺失**:
  - [ ] Docker/容器化沙盒执行
  - [ ] `/sandbox` 完整沙盒管理
  - [ ] 安全隔离的命令执行

### 23. LSP 集成
- **原版**: `src/services/lsp/` (8 files)
- **缺失**:
  - [ ] Language Server Protocol 客户端
  - [ ] 代码补全/诊断
  - [ ] Go-to-definition / Find References

### 24. UltraPlan
- **原版**: `src/utils/ultraplan/`
- **缺失**:
  - [ ] 高级多步规划模式
  - [ ] 规划可视化
  - [ ] 规划执行跟踪

### 25. Coordinator
- **原版**: `src/coordinator/` (2 files)
- **缺失**:
  - [ ] 多 Agent 协调器
  - [ ] Agent 间消息传递

### 26. Daemon
- **原版**: `src/daemon/` (2 files)
- **缺失**:
  - [ ] 后台常驻进程
  - [ ] 自动更新检查
  - [ ] 后台任务调度

### 27. Migrations
- **原版**: `src/migrations/`
- **缺失**:
  - [ ] 配置文件版本迁移
  - [ ] 数据格式升级

### 28. Native Installer
- **原版**: `src/native-ts/` + `src/utils/nativeInstaller/`
- **缺失**:
  - [ ] 系统级安装器
  - [ ] PATH 注册
  - [ ] Shell 集成 (bash/zsh/fish completion)

---

## 📊 已实现功能速查

### ✅ 已完成
- 多 Provider 支持 (Anthropic / OpenAI / Gemini / Grok)
- Full-screen REPL + Rich 流式输出
- 15 个工具 (bash, file read/write/edit, grep, glob, notebook, agent, task, todo, plan, web fetch/search, ask_user, mcp_resource)
- 72 个斜杠命令
- 插件系统 (安装/卸载/启用/禁用)
- 插件市场 (浏览/搜索/安装 — 交互式 UI)
- 插件斜杠命令发现和执行
- Skills 系统 (bundled + custom + plugin)
- Hooks 系统 (pre/post tool_call, message, session)
- Permission 系统 (settings.json allow/deny)
- Session 持久化 (save/load/resume)
- CLAUDE.md 多级配置加载
- Tab 补全 (命令 + @文件引用)
- @ 文件/目录提及
- 图片支持 (base64 编码)
- 基础 MCP 客户端 (stdio)
- 基础 Compact (单轮压缩)
- 多模型切换 (/model)
- 主题切换 (/theme, /color)
- 费用追踪 (/cost)
- 会话统计 (/stats)
- 多行输入 (Shift+Enter / Alt+Enter)

### 🔶 部分实现（存根或基础版）
- `/commit` — 有命令但无自动 commit message 生成
- `/diff` — 有命令但只是提示
- `/undo` `/redo` — 有命令但无 git 集成
- `/vim` — 有 toggle 但无完整模式
- `/buddy` — 有命令但是存根
- `/voice` — 有命令但是存根
- `/sandbox` — 有命令但无真正沙盒
- `/memory` — 有命令但无跨会话记忆
- `/tasks` — 有命令但无多任务系统
- Compact — 有基础版但缺多轮和工具精简
- MCP — 有 stdio 但缺 HTTP/SSE/Server

---

## 🎯 推荐优先级

1. **Git 集成** — 开发者最常用 (commit, diff, undo/redo)
2. **GitHub 集成** — PR/Issue 工作流
3. **MCP 完整化** — HTTP transport + Server 模式
4. **Compact 完整化** — 长对话体验关键
5. **多任务系统** — 并行能力
6. **Query Engine** — 非交互管道模式 (`-p`)
7. **Session Memory** — 跨会话记忆
8. **测试** — 0% 覆盖，需要基础测试框架
9. **Vim 完整模式** — 开发者群体常用
10. **Server 模式** — IDE 集成基础
