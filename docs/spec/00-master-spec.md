# Enterprise Agent Work Platform

# Final Engineering Specification & Codex Implementation Task

你现在需要从零实现一个完整的：

**Enterprise Agent Work Platform**

中文：

**企业级智能体工作平台**

项目技术核心不是 Chat UI，也不是 RAG、MCP、Skill 或 Multi-Agent 中的任何一个，而是：

**Enterprise Agent Runtime**

目标是构建一个：

> Durable + Governed + Extensible + Reproducible + Observable + Evaluated

的企业 Agent Runtime，使企业复杂 AI 任务能够在真实的失败、并发、权限、审批、版本升级、模型异常和外部副作用条件下可靠执行。

---

# 0. 重要执行要求

这是一个完整项目，不采用产品阶段式交付。

不要把任务理解成：

Phase 1 / Phase 2 / Phase 3。

你可以根据代码依赖自行决定内部实现顺序，但必须从一开始理解完整终局架构，并最终实现完整系统。

开始编码前：

1. 阅读本任务书全部内容。
2. 在仓库创建 `AGENTS.md`。
3. 创建 `docs/spec/`。
4. 根据本任务书整理正式工程 Spec。
5. 创建 `IMPLEMENTATION_PLAN.md`。
6. 创建 `SPEC_COMPLIANCE_MATRIX.md`。
7. 然后开始完整工程实现。

不要等待用户逐阶段确认。

发现普通实现细节问题可以自行解决。

如果发现本任务书存在架构级冲突，不得静默修改原则，必须创建 ADR：

`docs/adr/ADR-xxx.md`

说明：

* Context
* Conflict
* Existing Decision
* Proposed Decision
* Alternatives
* Consequences
* Migration
* Required Tests

---

# 1. 项目定位

本项目不是：

* ChatGPT Clone
* 普通聊天机器人
* LangGraph Demo
* Tool Calling Demo
* RAG Demo
* MCP Demo
* Multi-Agent Showcase
* Coding Agent
* Workflow Builder Demo

真正解决的问题是：

企业 AI 工作往往不是一次 LLM 请求，而是持续数分钟甚至更久的复杂任务。

任务可能需要：

* 理解用户目标；
* 自动规划多个步骤；
* 调用内部知识库；
* 解析 PDF / DOCX / PPTX / XLSX；
* 查询企业系统；
* 使用 MCP；
* 使用安装的 Skill；
* 搜索外部信息；
* 调用多个模型；
* 根据结果 Replan；
* 收集 Evidence；
* 形成 Artifact；
* 执行外部副作用；
* 等待人工审批；
* 应对 Worker Crash；
* 应对 MCP / LLM / 网络故障；
* 避免重复副作用；
* 全过程 Trace 和 Audit；
* 最终进行 Evaluation。

平台负责将这些能力组织成一个可靠 Agent Runtime。

---

# 2. 核心项目故事

最终项目应能够清晰表达为：

> 面向企业知识与流程场景，设计并实现持久化 Enterprise Agent Runtime，将 Planner、RAG、MCP、Skill 与 Tool 作为可调度能力，并通过状态持久化、权限治理、HITL、幂等与 Reconciliation、失败恢复、Evidence Provenance、全链路 Trace 和 Evaluation，使复杂 Agent 任务能够可靠执行。

---

# 3. 冻结技术栈

## Frontend

* React
* TypeScript
* Vite
* Tailwind CSS
* shadcn/ui

## Backend

* Python 3.12
* FastAPI
* Pydantic v2
* SQLAlchemy 2.x Async
* Alembic

## Persistence

* PostgreSQL
* pgvector
* PostgreSQL Full Text Search
* Redis
* MinIO

## Durable Execution

* Temporal

禁止引入 RabbitMQ。

Temporal 自身 Task Queue 承担 durable worker dispatch。

## Model

统一 OpenAI-compatible Model Gateway。

真实支持：

* Qwen
* DeepSeek
* 自定义 OpenAI-compatible base_url

保留以后接：

* vLLM
* 本地私有模型

的能力。

## RAG

* BGE Embedding
* PostgreSQL FTS
* pgvector
* RRF
* BGE Reranker / Cross Encoder

## Agent Extension

* 官方 MCP Python SDK
* Declarative Skill Runtime

## Observability

* OpenTelemetry
* Prometheus
* Grafana
* Tempo
* Loki

## Deployment

* Docker Compose
* Kubernetes
* GitHub Actions

## Test

* pytest
* Playwright

---

# 4. 总架构

系统逻辑：

```text
Web Frontend
│
├── Chat
├── Workspace
├── Task
├── Knowledge
├── Skills
├── MCP
├── Approval
├── Artifact
├── Trace
├── Audit
└── Settings
        │
        ▼
FastAPI
│
├── Auth
├── Workspace
├── Conversation
├── Task
├── Knowledge
├── Skill
├── MCP
├── Approval
├── Audit
└── Model Config
        │
        ▼
Enterprise Agent Runtime
│
├── Router
├── Goal Parser
├── Planner
├── Scheduler
├── Executor
├── Replan
├── Context Manager
├── Budget Manager
├── Loop Detection
├── Reviewer
├── Recovery
└── State Machine
        │
        ├───────────────────┐
        ▼                   ▼
Capability Layer        Skill Runtime
│                       │
├── Built-in Tool       ├── Manifest
├── File                ├── Instructions
├── RAG                 ├── Workflow
├── Web                 ├── Schema
├── MCP                 ├── Permission
├── Model               └── Eval Cases
└── Sub-Agent
        │
        ▼
Enterprise Governance
│
├── RBAC
├── Policy Engine
├── HITL
├── Audit
├── Secret
├── Workspace Isolation
├── Trust Boundary
└── Data Egress
        │
        ▼
Engineering Infrastructure
│
├── PostgreSQL
├── Temporal
├── Redis
├── MinIO
├── OTel
├── Prometheus
├── Tempo
├── Loki
├── Grafana
├── Docker
├── Kubernetes
└── CI/CD
```

---

# 5. Runtime Invariants

以下为系统最高级不变量。

不得为了实现方便违反。

## INV-001

PostgreSQL 是 Agent 业务 Canonical State。

## INV-002

Temporal 是 Durable Execution Infrastructure，不是 Agent 业务事实数据库。

## INV-003

任何可能重放的操作必须具备：

* Idempotency
* Reconciliation

中的至少一种可靠语义。

## INV-004

External Side Effect Timeout 不等于 Failure。

可能是：

`UNKNOWN`

## INV-005

UNKNOWN 副作用结果必须先 Reconcile，不能直接 Retry。

## INV-006

Cancel 不等于 Rollback。

## INV-007

所有 Side Effect Capability 必须声明 Compensation 语义。

## INV-008

任何 Untrusted Content 永远不能获得执行权限。

## INV-009

所有 Capability Execution 必须经过确定性 Policy Engine。

权限不能由 LLM 自己判断。

## INV-010

所有持久化 Contract 和 Extension Contract 必须可识别 Schema / Version。

## INV-011

重要业务结论必须可追溯：

```text
Claim
→ Evidence
→ Source
```

## INV-012

任何长任务必须能够在 Worker Crash 后恢复到合法状态。

## INV-013

每个 Task 执行必须绑定不可变 ExecutionSnapshot。

## INV-014

执行权转移给新 Attempt 后，旧 Attempt / Zombie Worker 不得再提交权威状态。

使用 Lease / Fencing Token 或等价机制。

## INV-015

Release 必须通过：

* Security
* Failure
* Integration
* E2E
* Evaluation

Gate。

---

# 6. 基础设施职责边界

## PostgreSQL

保存：

* User
* Workspace
* Conversation
* TaskRun
* TaskPlan
* TaskStep
* StepAttempt
* ExecutionSnapshot
* CapabilityCall
* Evidence
* Claim
* Approval
* Artifact metadata
* Skill installation
* MCP configuration
* Project Memory
* Audit
* Evaluation

PostgreSQL 是业务真相来源。

## Temporal

承担：

* Workflow
* Activity
* Task Queue
* Retry Infrastructure
* Durable Timer
* Signal
* Worker Recovery
* Continue-As-New

Workflow 内不得直接进行：

* PostgreSQL I/O
* Redis I/O
* HTTP
* MCP
* LLM
* File I/O
* MinIO
* 不确定性 random/time 操作

这些必须放 Activity。

## Redis

仅用于：

* Cache
* Rate Limit
* 短期协调

不得成为 Canonical State。

## MinIO

用于：

* Upload
* Artifact
* 大文件 / Blob

数据库保存 metadata / URI / hash。

---

# 7. 核心领域对象

核心模块之间禁止随意使用无结构 `dict`。

使用 Pydantic v2 Contract。

至少定义：

```text
TaskGoal

TaskRun

Plan
PlanStepSpec

TaskStep
StepAttempt

ExecutionSnapshot

CapabilityDescriptor
CapabilityRequest
CapabilityResult
CapabilityCall

Observation
Evidence
Claim

ApprovalRequest
ApprovalResult

ReplanDecision

ContextBundle

ArtifactManifest

ReviewResult

CancellationResult
```

Pydantic Runtime Contract 为机器可读 Contract Source of Truth。

JSON Schema 从 Pydantic 自动生成。

FastAPI OpenAPI 自动生成。

不要人工维护四套字段定义。

---

# 8. TaskGoal

用户自然语言首先解析成：

```text
TaskGoal

goal
constraints[]
deliverables[]

workspace_id

time_budget_sec
token_budget
cost_budget

requested_skill

user_context
```

例如：

用户：

> 综合 A 供应商材料、采购制度、历史合作记录和公开资料判断其是否适合进入供应商名单，输出正式报告，如果符合条件则发起准入申请。

转换：

```text
goal:
判断供应商是否符合企业准入要求

constraints:
- 必须参考采购制度
- 必须引用供应商材料
- 必须调查风险信息
- 未经批准不得执行准入

deliverables:
- 风险评级
- 主要风险
- Evidence
- 准入建议
- DOCX报告
```

---

# 9. Planner

Planner 禁止输出自然语言步骤供系统直接执行。

必须输出严格 Pydantic Schema。

Plan：

```text
plan_id
task_id
version
goal
constraints
deliverables
steps[]
```

PlanStepSpec：

```text
step_key
objective

dependencies[]

capability_requirements[]

expected_output

success_criteria[]

risk_hint

execution_mode

parallelizable

max_attempts
```

必须形成 DAG。

例如：

```text
S1 Supplier File Parse

S2 Internal Procurement Policy Retrieval
S3 Supplier Purchase History
S4 External Risk Research

S2/S3/S4 Parallel

S5 Risk Synthesis

S6 Reviewer

S7 Artifact Generation

S8 Admission Request
```

---

# 10. Plan Versioning

Plan 创建后不可原地修改。

Replan：

```text
Plan v1
↓
发现不足
↓
Replan
↓
Plan v2
```

保留全部版本。

ReplanDecision 至少支持：

```text
ADD_STEP

REPLACE_STEP

EXPAND_STEP

RETRY_STEP

SKIP_STEP

ABORT_TASK
```

结构：

```text
reason
trigger
action
affected_steps[]
new_steps[]
preserve_completed_steps
budget_impact
```

Replan Trigger 优先由确定性 Runtime 规则判断。

包括：

* terminal failure
* evidence insufficient
* capability unavailable
* repeated validation failure
* user modification
* budget danger

然后再让 LLM 生成新 Plan。

---

# 11. Task State Machine

TaskRun：

```text
CREATED
PLANNING
READY
RUNNING
WAITING_HITL
PAUSED
CANCELLING
CANCELLED
COMPLETED
FAILED
```

必须实现合法迁移矩阵。

禁止模块任意修改字符串 status。

使用 Enum + Domain State Transition Service。

---

# 12. Step State Machine

TaskStep：

```text
PENDING
READY
RUNNING
RETRY_WAIT
WAITING_HITL
BLOCKED
COMPLETED
FAILED
CANCELLED
SKIPPED
SUPERSEDED
```

---

# 13. StepAttempt

Retry 不得仅使用：

`retry_count`

必须存在独立 Attempt：

```text
Step S1
├── Attempt 1 FAILED
├── Attempt 2 FAILED
└── Attempt 3 SUCCEEDED
```

状态：

```text
CREATED
RUNNING
SUCCEEDED
FAILED
UNKNOWN
CANCELLED
```

Attempt 至少记录：

```text
attempt_id
step_id

worker_id

fencing_token
lease_expires_at

started_at
completed_at

failure_type
failure_detail

trace_id
```

---

# 14. Zombie Worker / Fencing

必须真实解决旧 Worker 恢复问题。

例如：

```text
Attempt 1
Worker A
token=17

Worker A失联

Attempt 2
Worker B
token=18

Worker A恢复
```

Worker A 再提交结果必须被拒绝。

旧 Attempt 不得覆盖：

* Step state
* Evidence
* Artifact
* Side Effect authoritative result

实现 Lease / Fencing Token 或等价机制。

必须写自动测试。

---

# 15. ExecutionSnapshot

Task / Plan 运行期间不得偷偷切换新的系统版本。

每次 Task execution 绑定：

```text
ExecutionSnapshot

runtime_version

workflow_definition_version

planner_prompt_version
router_prompt_version
reviewer_prompt_version

model_config_versions

skill_versions

capability_versions

policy_version

knowledge_document_refs

context_policy_version

schema_versions

feature_flags

created_at
```

Snapshot 创建后不可变。

如果明确迁移版本：

必须：

* 创建新 Snapshot
* Audit
* 关联新 Plan Version

---

# 16. Capability

Capability 表示：

> 系统能够执行什么。

Skill 不与 Capability 混为一个语义。

Capability Registry 至少管理：

```text
Built-in Tool
File
RAG
Web
MCP Tool
Platform Service
Model
```

CapabilityDescriptor：

```text
id
name
version
kind

description

input_schema
output_schema

tags

risk_level

side_effect_mode

idempotency_mode

compensation_mode
compensation_capability

timeout_sec
retry_policy

required_permissions

approval_policy

provider

health_status

cost_hint
```

---

# 17. Risk

Capability Risk：

```text
READ

WRITE

EXECUTE

EXTERNAL_SIDE_EFFECT
```

Data Classification：

```text
PUBLIC

INTERNAL

CONFIDENTIAL

RESTRICTED
```

Policy 输入至少包含：

```text
Subject
Workspace
Role
Permission
Resource
Data Classification
Capability
Risk
Provider
```

---

# 18. Skill

Skill 表示：

> 如何组合一组 Capability 完成一种业务工作。

V1 Skill 必须是声明式插件。

禁止 Skill ZIP 任意携带并执行 Python。

结构：

```text
supplier-risk.skill.zip
│
├── skill.yaml
├── instructions.md
├── workflow.yaml
│
├── schemas/
│   ├── input.schema.json
│   └── output.schema.json
│
├── policies/
│   └── permissions.yaml
│
└── eval/
    └── cases.json
```

安装流程：

```text
Upload
↓
Quarantine
↓
Manifest Validation
↓
Schema Validation
↓
Version Check
↓
Dependency Resolution
↓
Capability Check
↓
Permission Diff
↓
Admin Confirm
↓
Install
↓
Registry Refresh
↓
Enable
```

旗舰验收：

> 安装 supplier-risk Skill 之前平台没有该业务能力。

安装以后：

> Agent 可以完成供应商风险评估。

并且：

> Agent Runtime Core 不修改任何代码。

---

# 19. Contract Versioning

Extension：

使用 SemVer。

例如：

```text
procurement.read 2.1.0
```

Skill 可以声明：

```text
>=2.0,<3.0
```

Persistent Domain Contract：

使用：

```text
schema_version: int
```

需要 Migrator。

Capability lifecycle：

```text
ACTIVE
DEPRECATED
DISABLED
UNHEALTHY
UNSUPPORTED
```

---

# 20. MCP Manager

MCP 必须是完整生命周期管理。

支持：

```text
Register
Configure
Connect
Discover
Schema Sync
Normalize
Health Check
Reconnect
Disable
Audit
```

数据库保存：

```text
MCPServer

name
transport
endpoint / command

status

last_health_at

auth_secret_ref
```

MCP Tool：

```text
MCP
↓
Adapter
↓
CapabilityDescriptor
↓
Capability Registry
```

Runtime 不需要知道 Capability 原本来自 MCP。

---

# 21. Procurement MCP

必须真实实现一个：

**Procurement MCP Server**

作为独立企业采购系统模拟。

至少提供：

```text
get_supplier_profile

get_supplier_purchase_history

get_supplier_risk_flags

get_supplier_contracts

create_supplier_admission_request

get_admission_request
```

前几个：

READ。

`create_supplier_admission_request`：

EXTERNAL_SIDE_EFFECT。

独立数据库。

必须支持 operation_id / idempotency。

---

# 22. Side Effect

系统明确不追求：

Exactly Once。

采用：

> At-least-once execution + Idempotent / Reconciled Side Effects

每个副作用执行前：

```text
Operation ID
↓
持久化Operation State
↓
生成稳定Idempotency Key
↓
执行
```

Capability idempotency 模式至少：

```text
READ_ONLY

NATIVE_IDEMPOTENCY

RECONCILABLE

NON_RETRYABLE
```

---

# 23. UNKNOWN + Reconciliation

必须真实实现。

典型场景：

```text
create_supplier_admission_request
↓
采购系统创建成功
↓
网络响应丢失
↓
Agent收到Timeout
```

错误实现：

```text
Timeout → Retry
```

正确：

```text
CapabilityCall = UNKNOWN
↓
Reconciliation
↓
get_admission_request(operation_id)
↓
存在
    → SUCCEEDED
不存在
    → 允许安全Retry
```

必须写自动化 Failure Test：

> response loss 不得创建两张采购申请。

---

# 24. Compensation / Cancellation

Cancel ≠ Rollback。

Capability compensation_mode：

```text
NONE

AUTO

MANUAL

NOT_REVERSIBLE
```

Task cancel：

```text
停止未来Step
↓
停止可取消执行
↓
分析已经发生的Side Effect
↓
执行可自动补偿操作
↓
记录无法逆转操作
↓
必要时HITL
↓
CancellationResult
```

CancellationResult：

```text
CLEAN

PARTIALLY_COMPENSATED

REQUIRES_MANUAL_ACTION

IRREVERSIBLE_EFFECTS_REMAIN
```

---

# 25. PostgreSQL + Temporal Consistency

Workflow 不直接访问 PostgreSQL。

Activity：

```text
读取Domain State
↓
检查command_id / operation_id
↓
执行业务事务
↓
PostgreSQL COMMIT
↓
Activity Return Success
```

危险情况：

```text
DB Commit成功
↓
Worker crash
↓
Temporal没有收到Activity Success
↓
Activity Retry
```

Retry 必须通过：

* idempotent domain command
* command_id
* state check

识别已经执行过的业务操作。

对于：

> DB Transaction + Transaction 外事件

可使用 Transactional Outbox。

不要所有动作机械套 Outbox。

---

# 26. Reconciler

实现 Runtime Reconciler。

周期检查：

```text
PostgreSQL Business State
↕
Temporal Execution
```

定义合法 Consistency Matrix。

例如：

```text
Postgres RUNNING
Temporal Running
→ valid

Postgres WAITING_HITL
Temporal waiting signal
→ valid

Postgres COMPLETED
Temporal Completed
→ valid

Postgres RUNNING
Temporal Not Found
→ invalid
```

非法组合：

```text
ConsistencyIncident
↓
Repair
↓
失败
→ Alert / HITL
```

---

# 27. Optimistic Concurrency

关键 Domain Entity 使用：

```text
version
```

更新：

```text
WHERE id = ?
AND version = ?
```

必要位置使用：

`SELECT FOR UPDATE`

防止多个执行者并发覆盖状态。

---

# 28. Policy Engine

所有 Capability：

```text
Agent
↓
CapabilityRequest
↓
Schema Validation
↓
Capability Allowlist
↓
Policy Engine
↓
Data Egress Check
↓
ALLOW / DENY / REQUIRE_APPROVAL
↓
Execution
```

Policy Engine 必须是确定性代码。

LLM 不负责授权。

---

# 29. RBAC

角色至少：

```text
WORKSPACE_OWNER

WORKSPACE_ADMIN

MEMBER

AUDITOR
```

权限至少：

```text
knowledge.read
knowledge.write

supplier.read
supplier.write

skill.install

mcp.manage

audit.read

workspace.manage
```

Role 不直接等于全部逻辑。

最终：

```text
Role
→ Permission
→ Resource / Capability
```

---

# 30. HITL

高风险 Capability：

```text
CapabilityRequest
↓
Policy
↓
REQUIRE_APPROVAL
↓
ApprovalRequest
↓
Task WAITING_HITL
```

Frontend Card 显示：

```text
要执行什么

原因

Capability

参数

风险

Evidence
```

用户：

```text
Approve / Reject
```

使用 Temporal Signal 恢复 Task。

---

# 31. Audit

Audit append-only。

记录：

```text
actor_type
actor_id

workspace_id

task_id
step_id

action

resource

capability

policy_decision

approval

result

trace_id

timestamp
```

业务代码禁止：

```text
update audit
delete audit
```

---

# 32. Identity

Actor 类型至少：

```text
USER

SERVICE_ACCOUNT

SYSTEM

AGENT
```

V1 支持 Local Auth。

身份层保留 OIDC Adapter 接口。

不要实现 LDAP/SAML/企业微信/钉钉全集成。

---

# 33. Workspace Isolation

所有 Workspace-owned entity 必须拥有：

```text
workspace_id
```

包括：

* Conversation
* Task
* Knowledge
* Document
* Skill Installation
* MCP
* Artifact
* Memory

Repository / Service 后端强制 Workspace filter。

必须测试：

> Workspace A 无法访问 Workspace B。

禁止只靠前端隐藏。

---

# 34. Secret

API Key / MCP Credentials 等不得明文普通存储。

使用 encrypted secret-store abstraction。

Master Key：

* Environment
* Kubernetes Secret

Secret 不得进入：

* Prompt
* Context
* Trace
* Audit detail
* Log

必须测试。

---

# 35. Agent Trust Boundary

内容信任等级：

```text
TRUSTED_PLATFORM

TRUSTED_WORKSPACE

USER_INPUT

UNTRUSTED_DOCUMENT

UNTRUSTED_WEB

UNTRUSTED_MCP_OUTPUT
```

Instruction Priority：

```text
Platform/System Policy
>
Workspace Governance
>
Skill Contract
>
User Goal
>
Retrieved External Content
```

最重要：

> Evidence 永远不能因为被 LLM 读取而自动提升成 Instruction。

RAG / PDF / Web / MCP 输出全部视为 Data / Evidence。

---

# 36. Prompt Injection

不能只靠 System Prompt 防御。

即使 LLM 被恶意网页或 PDF 诱导：

也不得绕过：

* Capability Allowlist
* Pydantic Validation
* Policy
* Data Egress
* HITL

必须实现 Prompt Injection Security Test。

---

# 37. Data Egress

RBAC 控制：

> 谁能访问数据。

Egress Policy 控制：

> 数据允许发送到哪里。

例如：

```text
RESTRICTED
+
EXTERNAL_MODEL_PROVIDER
→ DENY
```

```text
CONFIDENTIAL
+
EXTERNAL_PROVIDER
→ REDACT / HITL / DENY
```

Model Gateway 执行前经过 Egress Check。

---

# 38. Model Gateway

统一接口：

```text
chat()

stream()

structured()

embed()
```

Provider：

```text
Qwen

DeepSeek

OpenAICompatible
```

支持：

`custom base_url`

所有：

* Router
* Planner
* Replan
* Reviewer
* Extractor

采用 Structured Output。

---

# 39. Structured Output

流程：

```text
LLM
↓
JSON
↓
Pydantic Validation
```

失败：

```text
Repair
```

最多：

2 次。

仍失败：

```text
Fallback
or
Step Failure
```

禁止无限 JSON Repair。

---

# 40. Context Manager

候选：

```text
System Policy

TaskGoal

Active Plan

Current Step

Skill Instructions

Conversation Summary

Relevant Messages

RAG Evidence

Tool Observations

Project Memory

User Constraints
```

经过：

```text
Candidate Pool
↓
Priority
↓
Relevance
↓
Dedup
↓
Compression
↓
Token Budget
↓
ContextBundle
```

每个 Context Item 记录：

```text
source

priority

token_count

included

reason
```

Trace 页面能查看模型实际 Context 构成。

---

# 41. Memory

不要复杂长期人格记忆。

V1：

```text
Conversation Summary

Project Fact

Project Decision
```

ProjectMemory：

```text
content

type:
FACT
DECISION
PREFERENCE

source

confidence

created_at

supersedes
```

---

# 42. RAG

上传：

```text
PDF
DOCX
PPTX
XLSX
CSV
Markdown
TXT
```

流程：

```text
Parse
↓
Normalize
↓
Chunk
↓
Embedding
↓
PostgreSQL / pgvector
```

查询：

```text
Query Rewrite
↓
PostgreSQL FTS
+
pgvector
↓
RRF
↓
BGE Rerank
↓
Evidence
```

Citation 定位：

```text
PDF page

DOCX paragraph / section

PPTX slide

XLSX sheet + row/cell
```

---

# 43. Evidence / Claim / Provenance

Evidence：

```text
evidence_id

task_id
step_id

source_type
source_id

locator

content

content_hash

source_hash
source_version

confidence
relevance

retrieved_at

generated_by

derived_from[]
```

增加 Claim：

```text
Claim

claim_id

task_id

content

importance

risk_level
```

关联：

```text
ClaimEvidenceLink
```

最终：

```text
Artifact
→ Claim
→ Evidence
→ Source
```

Reviewer 可以检查：

> 高风险 Claim 是否被 Evidence 支撑。

Evaluation：

`Claim Grounding Rate`

不要引入 Neo4j / Knowledge Graph / GraphRAG。

PostgreSQL 即可。

---

# 44. Web

普通 Capability：

```text
web.search

web.fetch
```

生产 Provider 可配置。

Test 使用 FakeWebProvider。

CI 不依赖公网。

---

# 45. Artifact

重点实现：

```text
Markdown

DOCX
```

MinIO：

文件。

Postgres：

```text
artifact_id

metadata

storage_uri

sha256

source_task

created_at
```

Supplier Report 至少包含：

* Executive Summary
* Supplier Profile
* Risk Rating
* Risk Matrix
* Key Findings
* Evidence
* Missing Information
* Recommendation
* Citation

---

# 46. Reviewer

Reviewer 不是常驻独立社会型 Agent。

只是复杂任务中的 Review Step。

检查：

```text
Schema

Deliverable

Constraint

Evidence Coverage

Citation

Unsupported Claim

Risk Claim Grounding
```

简单 Chat 不使用 Reviewer。

---

# 47. Single Agent First

正式原则：

> Single Agent First, Sub-Agent by Necessity.

不要创建：

```text
PDF Agent
Excel Agent
RAG Agent
Web Agent
Memory Agent
```

这些是 Capability。

只允许必要情况创建 Sub-Agent：

* 并行独立 Research
* 强上下文隔离
* 独立 Review

主要 Multi-Agent Demo：

```text
Internal Research
||
External Research
↓
Join
↓
Synthesis
```

并用于：

```text
Single Agent
vs
Parallel Sub-Agent
```

Evaluation。

---

# 48. Budget

Task：

```text
max_steps

max_replans

max_llm_calls

max_capability_calls

max_tokens

max_cost

deadline
```

每次执行前检查。

---

# 49. Loop Detection

至少检测：

```text
相同Capability
+
相同参数
连续调用
```

以及：

```text
Step没有新增Evidence
```

以及：

```text
连续Replan
```

超过阈值：

```text
ABORT
or
HITL
```

---

# 50. Failure Taxonomy

统一 Failure Code：

```text
LLM_RATE_LIMIT
LLM_TIMEOUT
LLM_SERVER_ERROR
LLM_SCHEMA_ERROR

CAPABILITY_TIMEOUT
CAPABILITY_UNAVAILABLE
CAPABILITY_BAD_OUTPUT
CAPABILITY_UNKNOWN_OUTCOME

MCP_DISCONNECTED
MCP_SCHEMA_CHANGED

DB_UNAVAILABLE

WORKER_CRASH

CONTEXT_OVERFLOW

BUDGET_EXCEEDED

POLICY_DENIED

APPROVAL_REJECTED

VALIDATION_FAILED

STALE_ATTEMPT

CONSISTENCY_VIOLATION
```

禁止：

```python
raise Exception("error")
```

作为主要业务错误模型。

---

# 51. Retry Matrix

例如：

```text
LLM 429
→ Backoff + Retry

LLM 5xx
→ Retry → Model Fallback

LLM Timeout
→ Retry

Schema Error
→ Repair ≤2

READ Capability Timeout
→ Retry

Side Effect Timeout
→ UNKNOWN → Reconcile

MCP Disconnect
→ Reconnect → Retry

Worker Crash
→ Durable Resume

Validation Failure
→ Repair / Replan

Budget Exceeded
→ Abort / HITL
```

明确：

> Temporal Infrastructure Retry ≠ Agent Domain Retry。

---

# 52. Checkpoint

Checkpoint 不保存 Python Memory Snapshot。

保存业务引用：

```text
task_id

active_plan_id

completed_steps

running_step

attempt

evidence_refs

artifact_refs

budget_usage

context_summary

operation_refs

execution_snapshot_id
```

大对象保存 Reference。

---

# 53. Worker Crash Recovery

必须真实测试。

例如：

```text
S1 DONE
S2 DONE
S3 RUNNING
S4 PENDING
```

真正 kill Worker。

启动 Worker。

要求：

```text
S1不重新执行
S2不重新执行

S3按安全语义恢复

副作用不重复

Task最终完成
```

---

# 54. Temporal

必须正确遵守 Workflow Determinism。

外部操作全部 Activity。

需要支持：

* Activity heartbeat
* timeout
* retry
* signal
* pause/resume
* HITL signal
* cancellation
* worker recovery
* workflow version
* Continue-As-New

记录：

```text
runtime_version

workflow_definition_version
```

旧 Workflow 不应因新代码部署直接失败。

---

# 55. Temporal History

监控：

```text
history_event_count

workflow_age

completed_step_count

replan_count
```

超过配置阈值：

```text
Persist Domain State
↓
Continue-As-New
```

Continuation 只携带：

```text
task_id

active_plan_id

resume_token

runtime_version
```

不要把 Evidence / Artifact 等大对象塞进 History。

---

# 56. Admission Control

实现基础平台 Backpressure。

至少控制：

```text
global_task_concurrency

workspace_task_concurrency

model_provider_concurrency

capability_concurrency

mcp_concurrency
```

超限 Task 保持：

```text
READY
```

记录：

`schedule_block_reason`

需要基础 Workspace Fairness。

不要自己实现复杂分布式 Scheduler。

---

# 57. Observability

OpenTelemetry：

```text
HTTP
↓
Task
↓
Plan
↓
Step
↓
Attempt
↓
LLM
↓
Capability
↓
MCP
↓
DB
```

使用关联 Trace。

UI：

`View Trace`

能够看到：

```text
Task

├ Router
├ Plan v1
├ Step S1
├ Step S2
├ MCP
├ Failure
├ Replan
├ Plan v2
├ HITL
├ Artifact
└ Finished
```

敏感数据脱敏。

---

# 58. Metrics

至少：

```text
task_total

task_success_rate

task_duration

step_duration

replan_total

llm_call_total

llm_latency

llm_tokens

capability_call_total

capability_failure_total

approval_total

policy_denied_total

recovery_total

unknown_operation_total

reconciliation_total

stale_attempt_rejected_total
```

---

# 59. Evaluation

建立：

60～80 个高质量 Golden Tasks。

覆盖：

```text
Knowledge QA

Complex Research

Skill

MCP

Planner

Replan

Permission

HITL

Failure

Recovery

Side Effect

Context
```

评：

```text
Task Success Rate

Plan Valid Rate

Capability Selection Accuracy

Citation Correctness

Evidence Coverage

Claim Grounding Rate

Recovery Success Rate

Duplicate Side Effect Rate

Policy Violation Rate

HITL Accuracy

Replan Success Rate

Latency

Tokens

Cost
```

效果指标先测 Baseline。

禁止提前伪造高指标。

---

# 60. Ablation

至少实现：

```text
Planner
vs
No Planner
```

```text
Replan
vs
No Replan
```

```text
Context Manager
vs
Full Context
```

```text
Conditional Reviewer
vs
No Reviewer
```

```text
Dynamic Capability Retrieval
vs
All Capabilities
```

```text
Single Agent
vs
Parallel Research Sub-Agent
```

---

# 61. FailureInjector

必须真实实现：

```text
LLM timeout

LLM 429

LLM schema corruption

MCP timeout

MCP disconnect

Capability response lost

Worker crash

Context overflow
```

支持测试确定性注入。

---

# 62. Hard Failure Tests

必须真实证明：

```text
Worker Crash
→ Resume
```

```text
Side Effect Response Lost
→ UNKNOWN
→ Reconcile
→ No Duplicate
```

```text
Zombie Worker
→ Fencing Reject
```

```text
MCP Disconnect
→ Reconnect
```

```text
LLM Schema Error
→ Repair
```

```text
Evidence Missing
→ Replan
```

```text
Unauthorized Action
→ DENY
```

```text
High Risk Action
→ HITL
```

```text
Prompt Injection
→ No Unauthorized Effect
```

```text
Workspace Cross Access
→ DENY
```

---

# 63. Data Lifecycle

支持基础 RetentionPolicy：

```text
conversation_retention_days

trace_retention_days

artifact_retention_days

document_retention_days

evaluation_retention_days
```

Audit 单独策略。

Workspace Delete：

```text
Disable new task
↓
Cancel / Drain running tasks
↓
Delete business data
↓
Delete vector data
↓
Delete MinIO files
↓
Revoke secrets
↓
retain minimal audit tombstone if configured
```

---

# 64. Backup / Restore

实现实用级：

* PostgreSQL backup
* MinIO backup
* Configuration export
* Restore documentation / script

必须至少验证一次：

```text
Create Workspace
↓
Run Task
↓
Generate Artifact
↓
Backup
↓
Restore
↓
Task / Plan / Evidence / Artifact / Audit可验证
```

不要建设跨 Region DR。

---

# 65. Usage

记录：

```text
task_id

workspace_id

model

input_tokens

output_tokens

embedding_tokens

llm_cost

capability_cost

duration
```

用于：

* Budget
* Eval
* Workspace Usage

不要开发完整 Billing 系统。

---

# 66. Flagship Business Scenario

项目唯一旗舰场景：

# Supplier Risk & Admission

用户：

> 综合 A 供应商提交的材料、公司的采购准入制度、历史合作数据和公开信息，评估其是否适合进入供应商名单，并生成正式风险评估报告。如果符合条件，创建准入申请。

执行：

```text
Task
↓
Goal Parse
↓
Planner
↓
━━━━━━━━━━━━━━━━━━━━━━━━
↓              ↓              ↓
Supplier       Internal       External
Files          Research       Research
↓              ↓              ↓
File           RAG            Web
               +
               Procurement MCP
━━━━━━━━━━━━━━━━━━━━━━━━
               ↓
            Evidence
               ↓
          Risk Analysis
               ↓
       Missing Evidence?
          │          │
         Yes         No
          │          │
        Replan       │
          └────┬─────┘
               ↓
            Reviewer
               ↓
            Claims
               ↓
          DOCX Report
               ↓
      Create Admission?
               ↓
          Policy Engine
               ↓
             HITL
               ↓
            Approve
               ↓
        Procurement MCP
               ↓
           Completed
```

---

# 67. Demo Data

必须生成：

3 个供应商。

Supplier A：

明显满足。

Supplier B：

明显拒绝。

Supplier C：

信息不足，必须触发 Replan。

同时生成：

* 企业采购制度 DOCX / PDF
* Supplier XLSX
* Contract Data
* Purchase History
* Risk Event
* Procurement MCP 数据库
* Supplier Risk Skill package

---

# 68. Frontend

UI 风格：

简洁、现代、类似 ChatGPT。

但：

> 不要求像素级复刻。

左侧：

```text
New Chat

Chats

Workspaces

Tasks

Knowledge

Skills

MCP

Audit
```

重点页面：

## Chat

* streaming
* attachment
* `@Skill`
* Task Card

## Task Detail

展示：

* Goal
* ExecutionSnapshot
* Plan Versions
* Steps
* Attempts
* Evidence
* Claims
* Failures
* Replan
* Approval
* Artifact

## Trace

完整 Timeline / Tree。

## HITL

显示：

* Action
* Capability
* Parameter
* Risk
* Evidence
* Approve / Reject

## Skills

* Upload
* Install
* Enable
* Disable
* Manifest
* Version
* Dependency

## MCP

* Register
* Connect
* Discover
* Health
* Reconnect
* Disable

## Audit

支持：

* actor
* workspace
* task
* action
* decision
* trace_id

---

# 69. Task UI

Task Card：

```text
Supplier Risk Assessment

Running

3 / 7

✓ Parse Supplier Files
✓ Internal Policy Research
✓ Procurement History
→ External Risk Research
○ Risk Analysis
○ Review
○ Report
```

支持：

* Pause
* Resume
* Cancel
* View Trace

---

# 70. API

至少：

```text
/auth/*

/workspaces/*

/conversations/*
/chat/stream

/tasks/*
/tasks/{id}/pause
/tasks/{id}/resume
/tasks/{id}/cancel
/tasks/{id}/modify
/tasks/{id}/trace

/approvals/*
/approvals/{id}/approve
/approvals/{id}/reject

/knowledge/*
/documents/*

/skills/*
/skills/install
/skills/{id}/enable
/skills/{id}/disable

/mcp/*
/capabilities/*

/artifacts/*

/audit/*

/models/*

/eval/*
```

统一错误响应。

---

# 71. Database

核心表至少：

```text
users
service_accounts

workspaces
workspace_members

conversations
messages

task_runs

execution_snapshots

task_plans
task_steps
step_attempts

checkpoints

capabilities
capability_calls

operations

skills
skill_versions
skill_installations

mcp_servers
mcp_capabilities

knowledge_bases
documents
document_chunks

evidence
claims
claim_evidence_links

approval_requests

artifacts

project_memories

model_configs
secrets

audit_logs

outbox_events

consistency_incidents

usage_records

evaluation_cases
evaluation_runs
evaluation_results
```

合理设计：

* FK
* Unique Constraint
* Index
* workspace_id
* optimistic version
* created_at / updated_at

---

# 72. Repository

建议：

```text
enterprise-agent-work-platform/
│
├── AGENTS.md
├── IMPLEMENTATION_PLAN.md
├── SPEC_COMPLIANCE_MATRIX.md
│
├── apps/
│   ├── api/
│   ├── worker/
│   └── web/
│
├── src/platform/
│   ├── domain/
│   ├── contracts/
│   ├── runtime/
│   ├── capabilities/
│   ├── skills/
│   ├── mcp/
│   ├── governance/
│   ├── knowledge/
│   ├── models/
│   ├── storage/
│   ├── observability/
│   ├── evaluation/
│   └── failure_injection/
│
├── contracts/
│
├── mcp_servers/
│   └── procurement/
│
├── skills/
│   └── supplier-risk/
│
├── demo/
│
├── tests/
│   ├── unit/
│   ├── contract/
│   ├── integration/
│   ├── security/
│   ├── failure/
│   └── e2e/
│
├── docs/
│   ├── spec/
│   ├── adr/
│   ├── architecture/
│   └── runbooks/
│
├── migrations/
│
├── deploy/
│   ├── docker/
│   ├── compose/
│   └── k8s/
│
├── observability/
│
├── scripts/
│
├── .github/workflows/
│
├── pyproject.toml
├── docker-compose.yml
├── .env.example
├── Makefile
└── README.md
```

允许根据合理 Python Packaging 做小调整。

不得改变架构职责。

---

# 73. Spec Package

开始实现时，先将本任务书整理为以下权威文档。

数量可以根据重复情况轻微调整，但必须保证 Single Source of Truth。

建议：

```text
00-master-spec.md

01-product-scope.md

02-system-architecture.md

03-runtime-invariants.md

04-domain-model.md

05-state-machines.md

06-agent-runtime.md

07-temporal-execution.md

08-capability-skill-mcp.md

09-governance-security.md

10-context-rag-evidence.md

11-consistency-recovery.md

12-observability-evaluation.md

13-database-api-contract.md

14-frontend-ux.md

15-supplier-risk-scenario.md

16-testing-acceptance.md

17-deployment-operations.md

18-requirement-traceability.md
```

每个语义只能有一个权威定义位置。

其他文档只 Reference，不重复重新定义。

---

# 74. Requirement Namespace

使用稳定 Requirement ID：

```text
INV
Runtime Invariant

DOM
Domain

STM
State Machine

RUN
Agent Runtime

TMP
Temporal

CAP
Capability

SKL
Skill

MCP
MCP

SEC
Governance / Security

CTX
Context / RAG / Evidence

REC
Consistency / Recovery

OBS
Observability

EVAL
Evaluation

API
API

DB
Persistence

UI
Frontend

OPS
Operations

ACC
Acceptance
```

所有重要 Requirement 必须：

```text
Requirement
→ Implementation
→ Test
→ PASS / FAIL
```

记录到：

`SPEC_COMPLIANCE_MATRIX.md`

---

# 75. Spec Ownership

每份 Spec 必须声明：

```text
Authoritative For

References

Does Not Define
```

例如：

```text
05-state-machines.md

Authoritative For:
Task lifecycle
Step lifecycle
Attempt lifecycle

Does Not Define:
Temporal implementation
Database schema
Retry rules
```

避免 Spec 重复和冲突。

---

# 76. AGENTS.md

AGENTS.md 保持精简。

只负责：

* Mission
* Spec Authority
* Frozen Architecture
* Runtime Invariants
* Requirement Traceability
* ADR
* Forbidden Architecture Changes
* No Mock-as-Implementation
* Definition of Done
* Compliance Report

详细设计不要重复塞入 AGENTS.md。

---

# 77. Non-Goals

严禁自行加入：

```text
Voice

Video

Computer Use

Coding Agent

GraphRAG

Knowledge Graph

Agent Mesh

Browser Agent系统

无限制代码执行Skill

Kafka

RabbitMQ

Celery

Neo4j

不必要LangGraph依赖

Service Mesh

Istio

多集群Kubernetes

复杂计费系统

复杂DR

几十种Model Provider

几十个MCP

几十个Agent
```

非常重要：

> Do not implement reasonable extensions that are not required by a Requirement ID or necessary to satisfy an existing Requirement.

不要因为“感觉有用”自行增加技术。

---

# 78. 不允许 Mock 冒充完成

生产代码中的以下功能必须是真实实现：

* PostgreSQL
* Temporal
* Skill Install
* MCP
* RAG
* Model Gateway
* RBAC
* Policy
* HITL
* Audit
* Recovery
* Trace
* Failure Injection
* Artifact

Mock / Fake 只能用于：

```text
tests/fakes/
```

例如：

Fake Web Provider。

禁止：

```python
pass
```

禁止：

```text
TODO
```

替代 Requirement。

禁止：

```text
Simulation successful
```

冒充真实功能。

禁止通过：

* skip tests
* xfail required tests
* 删除失败测试
* 修改断言降低标准

实现“通过”。

---

# 79. Testing

CI：

```text
Lint
↓
Type
↓
Unit
↓
Contract
↓
Integration
↓
Security
↓
Failure
↓
E2E
↓
Evaluation
```

Hard Correctness：

测试集范围内：

```text
Duplicate protected side effect
= 0

Cross Workspace Access
= 0

Unauthorized Capability Execution
= 0

HITL Bypass
= 0

Secret Leakage
= 0

Stale Attempt Authoritative Commit
= 0
```

否则 Release Fail。

---

# 80. Acceptance

至少：

## ACC-001

Supplier Risk Scenario 完整 E2E 成功。

## ACC-002

Worker Crash 后 Task 自动恢复。

已完成 protected steps 不重复执行。

## ACC-003

Admission Response 丢失时：

```text
UNKNOWN
→ Reconcile
```

不得产生重复 Admission。

## ACC-004

Zombie Worker 被 Fencing 拒绝。

## ACC-005

Workspace A 无法访问 Workspace B。

## ACC-006

无权限 Capability 被 Policy Engine DENY。

## ACC-007

External Side Effect 必须经过 HITL。

## ACC-008

恶意 PDF / Web Prompt Injection 不得绕过 Policy。

## ACC-009

Supplier Risk Skill 可在 Runtime Core 不修改的情况下安装并生效。

## ACC-010

MCP 断开后可以 Health Detect + Reconnect。

## ACC-011

Evidence 缺失可以触发 Replan。

## ACC-012

Artifact 中关键 Claim 可以追溯到 Evidence。

## ACC-013

ExecutionSnapshot 可以还原该 Task 使用的关键系统版本。

## ACC-014

Trace 可以展示 Task→Plan→Step→Attempt→LLM→Capability→MCP 链路。

## ACC-015

Audit 能追踪高风险操作完整决策过程。

---

# 81. Definition of Done

项目完成不是：

> 页面能打开。

项目完成是：

```bash
docker compose up
```

能够完整启动系统。

然后可以真实完成：

```text
Login

Create Workspace

Configure Model

Upload Procurement Knowledge

Upload Supplier Material

Install Supplier Risk Skill

Register Procurement MCP

Submit Supplier Task

Planner

Plan DAG

Parallel Internal/External Research

RAG

MCP

Evidence

Replan

Reviewer

Claim

DOCX Report

Policy

HITL

Approve

Create Admission

Complete
```

随后：

真实运行 Failure Tests。

最后：

* Tests PASS
* Security PASS
* Failure PASS
* E2E PASS
* Eval 能运行
* Compliance Matrix 完整

才允许声明：

**PROJECT COMPLETE**

---

# 82. Final Compliance Report

完成后生成：

`FINAL_COMPLIANCE_REPORT.md`

内容至少包括：

```text
Implemented Requirements

Missing Requirements

Requirement → Code mapping

Requirement → Test mapping

Test summary

Failure Test summary

Security Test summary

E2E summary

Evaluation results

Known limitations

ADRs

How to run

How to reproduce flagship demo
```

如果仍有未实现 Requirement：

必须明确报告。

不得伪称完成。

---

# 83. README

README 应能让一个新工程师理解：

```text
这个系统解决什么问题

为什么需要Enterprise Agent Runtime

核心架构

如何启动

如何运行Demo

如何安装Skill

如何注册MCP

如何运行Eval

如何运行Failure Test

如何查看Trace

如何查看Metrics
```

同时提供 Mermaid 架构图。

---

# 84. 实现时最重要的工程原则

不要追求代码数量。

优先追求：

```text
Correctness

Explicit State

Explicit Contracts

Failure Safety

Security Boundary

Recoverability

Auditability

Testability
```

不要为了“更像大项目”无意义拆几十个微服务。

保持：

> Modular Monolith + 独立 Worker + MCP Services

即可。

---

# 85. Codex 最终行为要求

你拥有实现权限。

请：

1. 完整读取本任务书。
2. 先建立 Spec。
3. 建立 Requirement Traceability。
4. 生成内部 Implementation Plan。
5. 根据依赖关系自行实现。
6. 不要等待用户逐模块确认。
7. 持续运行测试。
8. 发现 Bug 自行定位修复。
9. 不得通过降低 Requirement 标准让测试通过。
10. 不得擅自扩大项目范围。
11. 不得静默修改 Runtime Invariant。
12. 完整执行 Final Acceptance。
13. 最终生成 Compliance Report。

你的目标不是：

> “生成一个能展示的 Demo。”

你的目标是：

> **完整实现一个具备 Durable Execution、Governance、Skill/MCP Extension、Failure Recovery、Evidence Provenance、Observability 和 Evaluation 的 Enterprise Agent Work Platform。**
