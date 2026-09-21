# Codex 接入边界与 Remote 对比

核查日期：2026-09-21。基于当前仓库、本机 Codex CLI 0.155.1 和下文链接的
OpenAI 官方文档。官方产品能力随版本、账号和 workspace 开放情况变化。

## 产品决定

Codex 作为 SessionBell 的第二种 agent 接入。核心价值是将 Claude 和 Codex
在多台电脑上的任务放进同一个锁屏 / Live Activity 视图，让用户快速识别谁在运行、
谁已完成、谁需要接手，并处理已支持的轻量操作。

官方 Remote 已覆盖手机查看任务、继续对话、审批、完成通知和多机器切换。
这些能力本身不是 SessionBell 的独有卖点。原生桌面完整遥控不作为当前核心承诺；
原会话续聊保留为明确受限、可退回监控的 pilot。

## 对比范围

这里的官方 **Remote** 指 ChatGPT 手机端连接桌面主机的功能，任务仍在连接的
电脑上运行；不等同于 Codex Cloud。CLI 的 `--remote` 是连接 app-server 的
传输选项，也不等同于已经完成手机 Remote 配对。

| 能力 | SessionBell 当前边界 | 官方 Remote | 判断 |
| --- | --- | --- | --- |
| 原生桌面任务状态 | 已实现开始、完成、中断、结果摘要和累计 token 监控；依赖本地记录格式 | 可查看并继续已有桌面会话 | 能覆盖常见状态，不能承诺所有阻塞原因 |
| CLI 任务 | 已实现共享 app-server 接入；CLI 需连接该服务 | 本机有实验性 remote-control 命令，但任意独立 CLI 会话是否自动纳入未验证 | 历史可见不等于运行中会话已被接管 |
| 通知 | 已有推送实现；Codex 真机显示仍需验收 | 支持完成和需要用户处理的通知 | 普通通知不构成差异 |
| 锁屏聚合 | App / Widget 已有跨 agent Live Activity 展示 | 本次官方资料未确立同等锁屏聚合能力 | 有价值的差异方向，不据此断言官方绝无此能力 |
| 桌面原会话续聊 | 私有 IPC、显式 opt-in、客户端资源版本校验；运行中排队 | 原生支持续聊及 Queue / Steer | 官方完整性与兼容性更强 |
| 审批和问答 | 共享 CLI 已实现；原生桌面审批待实测，结构化问答未覆盖 | 支持审批、回答问题 | 不能宣称桌面控制完全对等 |
| diff / review | 当前主要展示状态、结果和续聊 | 支持 diff、review、运行中调整方向等 | 官方工作流更完整 |
| 多机器与多 agent | 可聚合多台电脑上的 Claude / Codex | 支持多机器；资料范围为 ChatGPT / Codex | 差异是跨 agent，而非仅跨机器 |

官方能力依据：[Remote](https://learn.chatgpt.com/docs/remote)、
[Remote connections](https://learn.chatgpt.com/docs/remote-connections)、
[Remote engineering workflows](https://developers.openai.com/blog/mastering-codex-remote-for-engineering)。
Remote 需要主机保持可用；SessionBell 的本地 relay 同样不能让休眠或离线电脑继续工作。

## 实现与可靠性

```text
桌面 App → 原生运行服务 → SQLite 索引 / 本地会话记录
                                  ↓ 只读增量观察
CLI → 共享 app-server → SessionBell relay → Worker → iPhone / Live Activity
                                  ↑
桌面原会话续聊 ← 内部 IPC ← 手机命令队列
```

### 原生桌面监控：已实现，依赖内部记录格式

`CodexDesktopObserver` 校验会话 originator，按字节偏移读取完整记录，处理部分行、
文件重写及重启历史基线。完成判断必须来自显式生命周期事件；超时或缺少事件表示未知，
不能猜成完成。累计 token 使用快照替换，不反复累加。

当前只查询最近两天更新、未归档的最多 200 条索引记录，并按展示时限保留任务，
并非整个历史库或全部内部 subagent 的完整管理器。

每轮扫描结束后等待两秒，再叠加落盘、网络和 APNs 延迟。尚未建立可承诺的端到端
延迟指标。运行 / 完成可用不代表每一种等待审批或等待输入状态都能识别。
SQLite / JSONL 属于实现细节，升级兼容需单独检查。

### 共享 CLI 控制：公开协议，有明确接入条件

公开 app-server 提供状态事件、轮次、审批和问答接口。SessionBell 作为另一个客户端
连接同一服务。单独开一个 app-server 能读到历史，不代表它拥有原生桌面正在运行的任务。
不得把仍在另一个服务里运行的会话自动恢复到第二个服务。

官方 app-server / WebSocket 仍有实验性边界，公开可用不等于长期稳定性保证。
参考：[App Server](https://learn.chatgpt.com/docs/app-server)。

### 桌面原会话续聊：依赖私有接口的 pilot

由内部 IPC 找到原 owner 并提交下一轮，保留桌面原服务。支持版本用资源 hash 校验；
不匹配时禁用控制、保留监控。无论超时还是断线，提交结果不明确都不自动重发。

已存在本地协议成功及用户确认桌面继续可用的试验记录。手机队列 / UI / 锁屏验收
须独立完成，不能用本地协议成功替代。当前不包含桌面取消、结构化问答、审批或已归档
会话重开；与同时发生的桌面提交仍有协议层竞态窗口。

详见 [桌面续聊设计与实验记录](codex-desktop-followup-design.md)。

### Hooks：可通过适配继续验证

官方已有 UserPromptSubmit、Stop、PermissionRequest 等生命周期 hooks，需要正常审核
信任。可探索其桌面适配，但不能把文档列出事件等同于已在安装版本上验证全链路。
当前监控仍以 observer 为主，避免重复通知。
参考：[Hooks](https://learn.chatgpt.com/docs/hooks)。

## 验证口径与发布边界

- 单元测试和离线输入格式检查验证代码行为，不能证明实际手机通知已显示。
- 只读真实记录 smoke 验证可解析及历史通知抑制；没有活动任务时，不构成实时追踪验收。
- APNs 返回成功不代表锁屏必然展示；需在配对真机检查通知、卡片、审批和续聊。
- 原生桌面续聊必须在支持版本上检查原会话、桌面显示、后续桌面输入及断线行为。
- 提交代码 / PR 不代表已发布安装器、部署新 Worker 或上传 App Store / TestFlight。

接入、测试命令和已有本地试点记录见 [Codex integration](codex-integration.md)。
