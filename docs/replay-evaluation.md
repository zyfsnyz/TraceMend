# Agent 链路回放建议：调研评估与实施边界

评估日期：2026-11-16

## 结论

**有条件通过。** “从异常 Trace 直接复现模型调用、减少手工复制 prompt/参数、对照新旧输出”是真实且值得做的调试需求；本地优先、中文、框架无关的 TraceMend 也有明确的产品价值。

但原文的市场判断和技术估算有两处关键失真：

1. **并非所有追踪平台都只能看日志，也并非几乎没有现成回放。** Phoenix 已提供 Span Replay，可在多步 LLM 链中选择一步重放、修改 prompt/模型参数并比较输出。[1] LangGraph 已提供基于 checkpoint 的 Replay 和 Fork，可从历史状态继续执行或修改状态后分支。[2] LangSmith Engine 的私有 Beta 可把问题 Trace 重放到指定 Agent deployment，并比较 baseline/fix。[3] Langfuse 提供会话只读回放，AgentOps 文档也列出 step-by-step session replay。[4][5]
2. **“当前 Trace 已存全每步 payload，只需调度器 + 播放器”不符合 TraceMend 当前实现。** 只读播放、暂停、上一步/下一步和进度跳转已经存在；真正缺的是每次 LLM 调用的独立请求载荷。当前代理生成的 LLM Span 没有保存 `input/output`，而多次调用复用 Trace 时 `trace.input` 会更新，无法据此可靠还原任意历史 LLM Span。

因此本项目不采用“独创/唯一回放平台”的宣传，也不把单次输出差异直接解释为“模型随机性导致幻觉”。实施目标调整为：

> **本地 Agent 链路调试台：零 Token 时序回放 + 明示成本与副作用边界的单步 LLM 真实重放 + 新旧输出差异对比。**

## 逐项可靠性判断

| 原建议 | 判断 | 依据与修正 |
|---|---|---|
| 异常 Trace 需要一键复现，手工复制消息和参数麻烦 | **可靠** | 多个主流产品已把 replay、experiment、comparison 做成调试工作流，说明该需求具有实际产品价值；但本文不声称其对所有开发者都是最高频痛点。 |
| 所有追踪平台只能看日志 | **不可靠** | Phoenix、LangGraph、LangSmith 均有不同层次的回放或重执行能力。[1][2][3] Langfuse 与 AgentOps 也提供会话/步骤回放。[4][5] |
| 只读模拟回放零 Token | **可靠，且已有基础** | 当前 ReplayBar 已按 sequence 播放、暂停和跳转，不调用模型；应明确它按步骤顺序播放，而不是宣称恢复真实墙钟等待时间。 |
| 原样重放单个 LLM 调用的消息与模型参数 | **可靠，但需补采集** | 新采集必须在 LLM Span 保存完整 chat-completions 请求与响应；旧数据仅允许“Trace 中只有一个 LLM Span”的无歧义回退。 |
| 一键真实重放任意 Agent 整条链路 | **当前数据模型下不可靠** | Trace 是审计证据，不包含任意框架的控制流、代码版本、检索状态、checkpoint、环境状态与工具副作用策略。LangGraph 的能力依赖 checkpoint，而不是仅依赖日志。[2] |
| 修改任意一步再继续跑后续完整链路 | **暂缓** | 这需要框架适配器/检查点和“修改后重新计算下游状态”的语义；把旧后续请求逐个再发一次并不是真正的断点续跑。 |
| 重放工具参数即可复现工具执行 | **默认不实施** | 重放可能重复发消息、写生产数据库、创建工单或调用付费 API。LangSmith 也明确要求 replay 标记与工具安全策略来避免副作用。[3] |
| 对比一次新旧输出即可判断随机性/幻觉成因 | **不可靠** | 输出一致或不一致都不足以单独归因；模型输出也不因固定数据集而自动具有确定性。[6] 需要重复实验、固定版本与事实评估，界面必须保留这一限制说明。 |
| 开发成本为中 | **仅对可靠垂直切片成立** | 单步 LLM 重放、血缘、比较和安全边界是中等成本；任意框架整链断点续跑是高成本平台能力，不能只靠一个调度器完成。 |

## 本次实施的可靠闭环

### 1. 零 Token 只读回放

- 保留现有播放、暂停、上一步/下一步、DAG/时间线联动。
- 增加明确的 Span 跳转控件与“只读 · 零 Token”标识。
- 不执行模型、MCP 或任何工具。

### 2. 单步 LLM 真实重放

- 仅允许 `kind=llm` 且能恢复独立 OpenAI-compatible chat-completions 请求的 Span。
- 原样复用消息、工具 schema、`temperature/top_p` 等已记录参数；允许白名单覆盖 `model/temperature/top_p/seed/max_tokens`。
- 使用**当前保存的路由和密钥**，使用原始请求模型做路由匹配；不恢复旧客户端 Authorization，不接受 Trace 中的 URL 作为重放目标。
- API 必须显式 `confirm: true`；界面必须说明会产生 Token/费用并再次确认。
- 遵守每日 Token 软预算、现有熔断和重试策略。
- **只重新请求模型，不执行返回的 tool_calls，不自动驱动后续 Span。** 工具调用仅作为模型输出进行对比。

### 3. 差异与血缘

- 每次重放创建独立 Trace，`parent_id` 指向原 Trace，metadata 记录 `source_trace_id/source_span_id/overrides/mode`。
- 原 Trace 不被修改；重放成功和上游失败都留下可审计记录。
- 对比 content、reasoning、tool_calls，给出相似度和 unified diff，并提供跳转到重放 Trace 的入口。
- 固定提示：单次一致/差异不能单独证明随机性或幻觉成因。

### 4. 旧数据兼容

- 新代理数据：从 LLM Span 的独立 `input` 重放。
- 自定义 Ingest：只要 LLM Span 自带合法 chat-completions `input` 即可重放。
- 旧代理数据：只有一个 LLM Span 时可从 Trace 输入无歧义回退；多个 LLM Span 且缺少独立输入时返回 409，要求用新版本重新采集，绝不猜测。

## 不做但未来可评估的能力

完整 Agent 断点续跑应另立技术设计：选择具体框架（如 LangGraph），保存 checkpoint/图版本/代码版本/环境信息，定义工具只读或沙箱策略，并实现 Fork 后的下游状态重算。未完成这些条件前，不使用“任意 Agent 全链路原样重放”或“通用 Agent Debugger”作为已交付能力宣传。

## 可公开使用的亮点文案

> TraceMend 将本地 Agent 审计与调试连接起来：按时间线零 Token 复盘每一步，并在明确成本与工具副作用边界后，单独重放历史 LLM 调用、修改采样参数、比较新旧输出。原始证据保持不变，每次实验都有独立血缘。

## 实现后的真实边界

本节校正实现落地后容易被营销文案放大的部分；上文调研判断与引用保持不变，也不新增来源。

**已实现的是单次选中的 LLM Span 重放，不是整链重放。** 具体边界：

- 只允许 `kind=llm` 的 Span，且必须能从该 Span 的独立 `input` 恢复 OpenAI-compatible `chat/completions` 请求；旧数据只在原 Trace 恰好只有一个 LLM Span 时无歧义回退到 `trace.input`。
- 只允许覆盖 `model/temperature/top_p/seed/max_tokens`；历史请求中的 URL、客户端 `Authorization` 和请求头不会复用，重放使用**当前**保存的路由、密钥和模型路由结果。
- 历史请求为 JSON 或 SSE 时都可发起上游调用；重放接口自身仍返回 JSON 结果。SSE 已开始后不会切换到备用路由。
- 不执行模型返回的 `tool_calls`，不创建 tool/tool_result Span，不重放其他 LLM 或工具 Span，不恢复后续控制流、检索状态、checkpoint、代码版本或环境状态。
- 零 Token 的 ReplayBar 只是按 Span `sequence` 的只读播放，不重演真实墙钟等待时间，也不代表重新执行了 Agent。

**因此不应宣传为：**

- “任意 Agent 全链路原样重放”“一键复现整条链路”或“确定性回放”。这些需要 checkpoint、图/代码版本、检索与环境状态以及下游状态重算；当前审计模型不具备这些条件。[2]
- “工具重放”。重放不会执行工具，避免重复发消息、写库、创建工单或调用付费 API；这与 LangSmith 对 replay 工具安全策略的要求一致。[3]
- “一次对比即可证明模型随机性/幻觉成因”。单次 `same` 或 `different` 只能作为受控反事实调试证据，不能单独推出统计因果结论；需要多次重复、固定版本与事实评估。[6]
- 跨版本、跨环境可移植复现。重放依赖当前路由、密钥与上游模型版本，历史配置变化会影响结果。

**实际价值**是：在原始审计证据不被修改的前提下，对单个 LLM 调用做受控的反事实调试，比较新旧输出并保留独立 Trace 血缘、幂等键和失败审计。它接近 Phoenix Span Replay 的“单步重放”定位 [1]，但不等同于 LangGraph 的 checkpoint time travel [2] 或 LangSmith 的 deployment replay [3]，也不提供统计因果推断。

## Sources

[1] https://arize.com/docs/phoenix/prompt-engineering/overview-prompts/span-replay.md
[2] https://docs.langchain.com/oss/python/langgraph/use-time-travel.md
[3] https://docs.langchain.com/langsmith/engine-fix-validation
[4] https://langfuse.com/docs/observability/features/sessions
[5] https://docs.agentops.ai/v2/examples/google_adk
[6] https://langfuse.com/docs/evaluation/experiments/compare-experiments
