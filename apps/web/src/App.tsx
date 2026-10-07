import {
  Archive,
  Activity,
  Bot,
  Boxes,
  CheckCircle2,
  ChevronRight,
  CircleGauge,
  Database,
  FileCheck2,
  FileSearch,
  LogOut,
  Menu,
  MessageSquarePlus,
  MessagesSquare,
  Pause,
  Play,
  RefreshCcw,
  Settings2,
  ShieldCheck,
  Square,
  Trash2,
  UploadCloud,
  Workflow,
  X,
} from "lucide-react"
import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react"

import { ApiClient, type Approval, type Artifact, type AuditEvent, type CapabilityDescriptor, type ConsistencyIncident, type Conversation, type Evidence, type KnowledgeIngestion, type MCPServer, type Message, type ModelConfiguration, type Plan, type SkillInstallation, type StepAttempt, type Task, type TaskStep, type TaskTrace, type Workspace } from "./api"
import { ChatPage } from "./components/chat-page"
import { ModelPage } from "./components/model-page"
import { Badge } from "./components/ui/badge"
import { Button } from "./components/ui/button"
import { Card, CardContent, CardHeader } from "./components/ui/card"
import { Input } from "./components/ui/input"

type Page = "chat" | "chats" | "workspaces" | "tasks" | "trace" | "knowledge" | "skills" | "mcp" | "models" | "approvals" | "artifacts" | "audit"

const nav: { id: Page; label: string; icon: typeof Bot }[] = [
  { id: "chat", label: "新对话", icon: MessageSquarePlus },
  { id: "chats", label: "对话", icon: MessagesSquare },
  { id: "workspaces", label: "工作区", icon: Boxes },
  { id: "tasks", label: "任务", icon: Workflow },
  { id: "trace", label: "追踪", icon: Activity },
  { id: "knowledge", label: "知识库", icon: Database },
  { id: "skills", label: "Skills", icon: FileCheck2 },
  { id: "mcp", label: "MCP", icon: CircleGauge },
  { id: "models", label: "模型", icon: Settings2 },
  { id: "approvals", label: "审批", icon: ShieldCheck },
  { id: "artifacts", label: "制品", icon: Archive },
  { id: "audit", label: "审计", icon: FileSearch },
]

function Login({ onLogin }: { onLogin: (token: string) => void }) {
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [mode, setMode] = useState<"login" | "register">("login")
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)

  async function submit(event: FormEvent) {
    event.preventDefault()
    setBusy(true); setError("")
    try {
      const api = new ApiClient("")
      if (mode === "register") await api.register(email, password)
      const result = await api.login(email, password)
      const authenticated = new ApiClient(result.access_token)
      const existingWorkspaces = await authenticated.workspaces()
      if (existingWorkspaces.length === 0) await authenticated.createWorkspace("我的工作区")
      onLogin(result.access_token)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法登录")
    } finally { setBusy(false) }
  }

  return (
    <main className="login-page">
      <section className="login-card">
        <div className="login-brand"><div><Bot size={23} /></div><span><h1>企业智能体工作平台</h1><p>受治理的持久化 Agent Runtime</p></span></div>
        <h2>{mode === "login" ? "欢迎回来" : "创建账户"}</h2>
        <p className="login-subtitle">{mode === "login" ? "登录后直接开始对话。" : "注册后会自动准备默认工作区。"}</p>
        <form className="login-form" onSubmit={submit} autoComplete="on">
          <label htmlFor="login-email">邮箱</label>
          <Input id="login-email" name="username" autoComplete="username" type="email" value={email} onChange={(event) => setEmail(event.target.value)} required />
          <label htmlFor="login-password">密码</label>
          <Input id="login-password" name="password" autoComplete={mode === "login" ? "current-password" : "new-password"} type="password" value={password} onChange={(event) => setPassword(event.target.value)} minLength={12} required />
          {error && <p role="alert" className="form-error">{error}</p>}
          <Button className="w-full" disabled={busy}>{busy ? "连接中…" : mode === "login" ? "登录" : "注册并登录"}</Button>
        </form>
        <button className="login-switch" onClick={() => setMode(mode === "login" ? "register" : "login")}>
          {mode === "login" ? "首次使用？创建本地账户" : "已有账户？返回登录"}
        </button>
      </section>
    </main>
  )
}

function Empty({ title, detail, action, actionLabel }: { title: string; detail: string; action?: () => void; actionLabel?: string }) {
  const body = <><div className="empty-icon"><Database size={22} /></div><h3>{title}</h3><p>{detail}</p>{actionLabel && <span>{actionLabel}</span>}</>
  return action ? <button className="empty-action" onClick={action}>{body}</button> : <Card><CardContent className="empty-static">{body}</CardContent></Card>
}

function StateBadge({ state }: { state: string }) {
  const active = ["RUNNING", "APPROVED", "COMPLETED", "ACTIVE", "ENABLED", "SUCCEEDED"].includes(state)
  return <Badge className={active ? "border-emerald-400/20 bg-emerald-400/10 text-emerald-300" : ""}>{state}</Badge>
}

function knowledgeFailureText(value: string | null): string {
  if (!value) return ""
  const parsed = value.match(/^([A-Z_]+) \[([A-Z_]+)\]:\s*(.+)$/)
  if (!parsed) {
    return value === "DomainError"
      ? "知识摄取失败，但旧任务没有保留具体原因；修复服务配置后可重新上传。"
      : value
  }
  const [, code, phase, detail] = parsed
  if (
    phase === "EMBED"
    && ["LLM_SERVER_ERROR", "LLM_TIMEOUT", "LLM_RATE_LIMIT"].includes(code)
  ) {
    return "向量模型服务不可用；请确认 EMBEDDING_BASE_URL 可访问并返回 1024 维向量，然后点击重试。"
  }
  if (phase === "LOAD" && code === "CAPABILITY_UNAVAILABLE") {
    return "文档对象暂时无法从存储中读取；请检查 MinIO 后点击重试。"
  }
  if (phase === "PARSE" && code === "VALIDATION_FAILED") {
    return `文档解析失败：${detail}`
  }
  return `${phase} 阶段失败：${detail}`
}

const BUILTIN_SKILLS = [
  {
    name: "web-research",
    version: "1.0.1",
    title: "通用联网搜索",
    description: "使用真实搜索服务获取网页结果，保存 Evidence 和来源，再交给当前选择的 Chat 模型回答。",
  },
  {
    name: "document-analysis",
    version: "1.0.0",
    title: "附件与文档分析",
    description: "只检索用户选中的不可变文档版本，避免其他文档和旧版本污染回答。",
  },
  {
    name: "grounded-research",
    version: "1.0.0",
    title: "附件与联网核对",
    description: "并行检索选中附件和当前公开网页，保留来源边界并明确展示冲突。",
  },
] as const

function BuiltinSkillCard({
  title,
  description,
  enabled,
  installed,
  busy,
  onEnable,
}: {
  title: string
  description: string
  enabled: boolean
  installed: boolean
  busy: boolean
  onEnable: () => void
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="font-medium">{title}</h2>
            <StateBadge state={enabled ? "ENABLED" : installed ? "UPDATE_AVAILABLE" : "NOT_ENABLED"} />
          </div>
          <p className="mt-1 text-sm text-slate-400">
            {description}
          </p>
        </div>
        {!enabled && (
          <Button disabled={busy} onClick={onEnable}>
            {busy ? "正在启用…" : installed ? "升级内置 Skill" : "启用内置 Skill"}
          </Button>
        )}
      </CardHeader>
    </Card>
  )
}


export default function App() {
  const [token, setToken] = useState("")
  const [page, setPage] = useState<Page>("chat")
  const [mobile, setMobile] = useState(false)
  const [workspaces, setWorkspaces] = useState<Workspace[]>([])
  const [workspace, setWorkspace] = useState("")
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [conversation, setConversation] = useState("")
  const [messages, setMessages] = useState<Message[]>([])
  const [tasks, setTasks] = useState<Task[]>([])
  const [plans, setPlans] = useState<Record<string, Plan[]>>({})
  const [taskSteps, setTaskSteps] = useState<Record<string, TaskStep[]>>({})
  const [taskAttempts, setTaskAttempts] = useState<Record<string, StepAttempt[]>>({})
  const [taskEvidence, setTaskEvidence] = useState<Record<string, Evidence[]>>({})
  const [trace, setTrace] = useState<TaskTrace | null>(null)
  const [approvals, setApprovals] = useState<Approval[]>([])
  const [artifacts, setArtifacts] = useState<Artifact[]>([])
  const [audit, setAudit] = useState<AuditEvent[]>([])
  const [incidents, setIncidents] = useState<ConsistencyIncident[]>([])
  const [knowledge, setKnowledge] = useState<KnowledgeIngestion[]>([])
  const [knowledgeFile, setKnowledgeFile] = useState<File | null>(null)
  const [uploadingKnowledge, setUploadingKnowledge] = useState(false)
  const [retryingKnowledge, setRetryingKnowledge] = useState("")
  const [deletingKnowledge, setDeletingKnowledge] = useState("")
  const [uploadingKnowledgeVersion, setUploadingKnowledgeVersion] = useState("")
  const [deletingConversation, setDeletingConversation] = useState("")
  const [skills, setSkills] = useState<SkillInstallation[]>([])
  const [skillFile, setSkillFile] = useState<File | null>(null)
  const [skillName, setSkillName] = useState("supplier-risk")
  const [skillVersion, setSkillVersion] = useState("1.0.0")
  const [uploadingSkill, setUploadingSkill] = useState(false)
  const [mcpServers, setMcpServers] = useState<MCPServer[]>([])
  const [installingBuiltinSkill, setInstallingBuiltinSkill] = useState("")
  const [capabilities, setCapabilities] = useState<CapabilityDescriptor[]>([])
  const [models, setModels] = useState<ModelConfiguration[]>([])
  const [mcpBusy, setMcpBusy] = useState(false)

  const [notice, setNotice] = useState("")
  const [newWorkspace, setNewWorkspace] = useState("")

  const api = useMemo(() => new ApiClient(token), [token])

  const refresh = useCallback(async () => {
    if (!token) return
    try {
      const ws = await api.workspaces(); setWorkspaces(ws)
      const selected = workspace || ws[0]?.workspace_id || ""
      if (!workspace && selected) setWorkspace(selected)
      if (!selected) return
      const [chatRows, taskRows, approvalRows, artifactRows, auditRows, knowledgeRows, skillRows, mcpRows, capabilityRows, modelRows, incidentRows] = await Promise.all([
        api.conversations(selected), api.tasks(selected), api.approvals(selected), api.artifacts(selected), api.audit(selected).catch(() => []), api.knowledge(selected), api.skills(selected), api.mcpServers(selected), api.capabilities(selected), api.models(selected), api.incidents(selected).catch(() => []),
      ])
      setConversations(chatRows); setTasks(taskRows); setApprovals(approvalRows); setArtifacts(artifactRows); setAudit(auditRows); setKnowledge(knowledgeRows); setSkills(skillRows); setMcpServers(mcpRows); setCapabilities(capabilityRows); setModels(modelRows); setIncidents(incidentRows)
      const taskPlans = await Promise.all(
        taskRows.map(async (item) => [item.task_id, await api.plans(selected, item.task_id)] as const),
      )
      setPlans(Object.fromEntries(taskPlans))
      const materializedSteps = await Promise.all(
        taskRows.map(async (item) => [item.task_id, await api.taskSteps(selected, item.task_id)] as const),
      )
      setTaskSteps(Object.fromEntries(materializedSteps))
      const attemptRows = await Promise.all(
        taskRows.map(async (item) => [item.task_id, await api.taskAttempts(selected, item.task_id)] as const),
      )
      setTaskAttempts(Object.fromEntries(attemptRows))
      const evidenceRows = await Promise.all(
        taskRows.map(async (item) => [item.task_id, await api.taskEvidence(selected, item.task_id)] as const),
      )
      setTaskEvidence(Object.fromEntries(evidenceRows))
      if (conversation && chatRows.some((item) => item.conversation_id === conversation)) {
        setMessages(await api.messages(selected, conversation))
      }
      setNotice("")
    } catch (reason) { setNotice(reason instanceof Error ? reason.message : "加载失败") }
  }, [api, conversation, token, workspace])

  useEffect(() => {
    const refreshTimer = window.setTimeout(() => void refresh(), 0)
    return () => window.clearTimeout(refreshTimer)
  }, [refresh])

  const knowledgeInProgress = knowledge.some(
    (item) => item.state === "PENDING" || item.state === "RUNNING",
  )

  const latestKnowledgeIngestionIds = useMemo(() => {
    const latest = new Map<string, string>()
    for (const item of knowledge) latest.set(item.document_id, item.ingestion_id)
    return new Set(latest.values())
  }, [knowledge])

  useEffect(() => {
    if (!token || !workspace || !knowledgeInProgress) return
    let active = true
    const timer = window.setInterval(() => {
      void api.knowledge(workspace).then((rows) => {
        if (active) setKnowledge(rows)
      }).catch(() => undefined)
    }, 1500)
    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [api, knowledgeInProgress, token, workspace])

  async function createWorkspace(event: FormEvent) {
    event.preventDefault(); if (!newWorkspace.trim()) return
    try { const item = await api.createWorkspace(newWorkspace); setNewWorkspace(""); setWorkspace(item.workspace_id); await refresh() }
    catch (reason) { setNotice(reason instanceof Error ? reason.message : "创建失败") }
  }

  async function createDefaultWorkspace() {
    try {
      const item = await api.createWorkspace("我的工作区")
      setWorkspace(item.workspace_id)
      setPage("chat")
      await refresh()
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "创建失败")
    }
  }

  async function startChat() {
    if (!workspace) return
    try { const item = await api.createConversation(workspace, "新对话"); setConversation(item.conversation_id); setMessages([]); setPage("chat"); await refresh() }
    catch (reason) { setNotice(reason instanceof Error ? reason.message : "创建失败") }
  }


  async function control(task: Task, action: "pause" | "resume" | "cancel") {
    try { await api.controlTask(workspace, task, action); await refresh() }
    catch (reason) { setNotice(reason instanceof Error ? reason.message : "任务控制失败") }
  }

  async function decide(item: Approval, decision: "approve" | "reject") {
    try { await api.decideApproval(item, decision); await refresh() }
    catch (reason) { setNotice(reason instanceof Error ? reason.message : "审批失败") }
  }

  async function uploadKnowledge(event: FormEvent) {
    event.preventDefault(); if (!workspace || !knowledgeFile) return
    setUploadingKnowledge(true)
    try {
      await api.uploadKnowledge(workspace, knowledgeFile)
      setKnowledgeFile(null)
      await refresh()
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "知识上传失败")
    } finally {
      setUploadingKnowledge(false)
    }
  }

  async function retryKnowledge(item: KnowledgeIngestion) {
    setRetryingKnowledge(item.ingestion_id)
    try {
      const retried = await api.retryKnowledge(item)
      setKnowledge((current) => current.map(
        (candidate) => candidate.ingestion_id === retried.ingestion_id
          ? retried
          : candidate,
      ))
      setNotice("已重新提交知识摄取；状态会自动更新。")
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "知识摄取重试失败")
      await refresh()
    } finally {
      setRetryingKnowledge("")
    }
  }

  async function uploadKnowledgeVersion(item: KnowledgeIngestion, file: File | null) {
    if (!file) return
    setUploadingKnowledgeVersion(item.document_id)
    try {
      await api.uploadKnowledge(workspace, file, item.document_id, item.name)
      setNotice("新版本已上传，摄取状态会自动更新。")
      await refresh()
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "知识版本上传失败")
    } finally {
      setUploadingKnowledgeVersion("")
    }
  }

  async function deleteKnowledge(item: KnowledgeIngestion) {
    if (!window.confirm(`确定从知识库移除“${item.name}”及其全部版本吗？`)) return
    setDeletingKnowledge(item.document_id)
    try {
      await api.deleteKnowledge(item)
      setKnowledge((current) => current.filter(
        (candidate) => candidate.document_id !== item.document_id,
      ))
      setNotice("知识文档已移除；不可变版本按保留策略留存且不再参与检索。")
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "知识文档删除失败")
      await refresh()
    } finally {
      setDeletingKnowledge("")
    }
  }

  async function deleteConversationItem(item: Conversation) {
    if (!window.confirm(`确定删除对话“${item.title}”吗？`)) return
    setDeletingConversation(item.conversation_id)
    try {
      await api.deleteConversation(item)
      setConversations((current) => current.filter(
        (candidate) => candidate.conversation_id !== item.conversation_id,
      ))
      if (conversation === item.conversation_id) {
        setConversation("")
        setMessages([])
      }
      setNotice("对话已从历史记录移除。")
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "对话删除失败")
      await refresh()
    } finally {
      setDeletingConversation("")
    }
  }

  async function uploadSkill(event: FormEvent) {
    event.preventDefault(); if (!workspace || !skillFile) return
    setUploadingSkill(true)
    try {
      await api.quarantineSkill(workspace, skillName, skillVersion, skillFile)
      setSkillFile(null)
      await refresh()
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "Skill 上传失败")
    } finally {
      setUploadingSkill(false)
    }
  }

  async function installBuiltinSkill(
    definition: (typeof BUILTIN_SKILLS)[number],
  ) {
    if (!workspace) return
    setInstallingBuiltinSkill(definition.name)
    try {
      let item = skills.find(
        (candidate) => candidate.name === definition.name
          && candidate.skill_version === definition.version,
      )
      if (!item || item.state === "REJECTED") {
        const response = await fetch(`/builtin-skills/${definition.name}.skill.zip`)
        if (!response.ok) throw new Error(`${definition.title} Skill 包不可用`)
        const file = new File([await response.blob()], `${definition.name}.skill.zip`, {
          type: "application/zip",
        })
        item = await api.quarantineSkill(
          workspace,
          definition.name,
          definition.version,
          file,
        )
      }
      if (item.state === "PENDING_CONFIRMATION") {
        item = await api.skillAction(item, "install")
      }
      if (item.state === "INSTALLED" || item.state === "DISABLED") {
        item = await api.skillAction(item, "enable")
      }
      if (item.state !== "ENABLED") {
        throw new Error(item.safe_error || `${definition.title}当前状态：${item.state}`)
      }
      await refresh()
      setNotice(`${definition.title}已启用。`)
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "内置 Skill 启用失败")
    } finally {
      setInstallingBuiltinSkill("")
    }
  }


  async function skillAction(item: SkillInstallation, action: "install" | "enable" | "disable") {
    try { await api.skillAction(item, action); await refresh() }
    catch (reason) { setNotice(reason instanceof Error ? reason.message : "Skill 操作失败") }
  }

  async function registerMcp() {
    if (!workspace) return
    setMcpBusy(true)
    try { await api.registerMcp(workspace); await refresh() }
    catch (reason) { setNotice(reason instanceof Error ? reason.message : "MCP 注册失败") }
    finally { setMcpBusy(false) }
  }

  async function mcpAction(item: MCPServer, action: "connect" | "health" | "disable") {
    setMcpBusy(true)
    try { await api.mcpAction(item, action); await refresh() }
    catch (reason) { setNotice(reason instanceof Error ? reason.message : "MCP 操作失败") }
    finally { setMcpBusy(false) }
  }


  async function viewTrace(task: Task) {
    if (!workspace) return
    try {
      setTrace(await api.taskTrace(workspace, task.task_id))
      setPage("trace")
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "Trace 加载失败")
    }
  }

  if (!token) return <Login onLogin={setToken} />
  const activeWorkspace = workspaces.find((item) => item.workspace_id === workspace)

  const content = (() => {
    if (!workspace && page !== "workspaces") return <Empty title="正在准备工作区" detail="工作区用于隔离你的对话、任务和数据。" action={() => void createDefaultWorkspace()} actionLabel="立即准备并开始对话" />
    if (page === "workspaces") return <Card><CardHeader><h2 className="font-medium">工作区</h2></CardHeader><CardContent><form className="mb-5 flex gap-2" onSubmit={createWorkspace}><Input placeholder="工作区名称" value={newWorkspace} onChange={(event) => setNewWorkspace(event.target.value)} /><Button>创建</Button></form><div className="space-y-2">{workspaces.length ? workspaces.map((item) => <button key={item.workspace_id} onClick={() => { setWorkspace(item.workspace_id); setPage("chat") }} className="workspace-row"><span><b>{item.name}</b><small>{item.role}</small></span><ChevronRight size={18} /></button>) : <Empty title="还没有工作区" detail="系统通常会自动创建；你也可以在这里立即准备一个。" action={() => void createDefaultWorkspace()} actionLabel="创建默认工作区" />}</div></CardContent></Card>
    if (page === "chat") return <ChatPage api={api} workspace={workspace} conversation={conversation} setConversation={setConversation} messages={messages} setMessages={setMessages} models={models} skills={skills} knowledge={knowledge} onRefresh={refresh} onOpenModels={() => setPage("models")} onOpenTasks={() => setPage("tasks")} onOpenSkills={() => setPage("skills")} setNotice={setNotice} />
    if (page === "chats") return <Card><CardHeader className="flex flex-row items-center justify-between"><h2 className="font-medium">对话</h2><Button size="sm" onClick={startChat}><MessageSquarePlus size={15} />新对话</Button></CardHeader><CardContent className="space-y-2">{conversations.length ? conversations.map((item) => <div key={item.conversation_id} className="flex items-center gap-2 rounded-xl border border-white/8 p-2 hover:bg-white/5"><button onClick={async () => { setConversation(item.conversation_id); setMessages(await api.messages(workspace, item.conversation_id)); setPage("chat") }} className="flex min-w-0 flex-1 items-center justify-between p-2 text-left"><span><b className="block truncate text-sm">{item.title}</b><small className="text-slate-500">版本 {item.version}</small></span><ChevronRight size={18} /></button><Button type="button" size="sm" variant="danger" disabled={deletingConversation === item.conversation_id} onClick={() => void deleteConversationItem(item)}><Trash2 size={14} />{deletingConversation === item.conversation_id ? "删除中…" : "删除"}</Button></div>) : <Empty title="暂无对话" detail="创建第一条对话，记录供应商分析目标。" />}</CardContent></Card>
    if (page === "tasks") return <div className="grid gap-4">{tasks.length ? tasks.map((task) => { const versions = plans[task.task_id] ?? []; const plan = versions.at(-1); const materialized = taskSteps[task.task_id] ?? []; const states = new Map(materialized.map((item) => [item.spec.step_key, item.state])); const stepIds = new Map(materialized.map((item) => [item.spec.step_key, item.step_id])); return <Card key={task.task_id}><CardContent className="space-y-5"><div className="flex flex-col gap-5 md:flex-row md:items-center md:justify-between"><div><div className="mb-2 flex items-center gap-2"><StateBadge state={task.state} /><span className="text-xs text-slate-500">v{task.version}</span>{plan && <Badge>Plan v{plan.version}</Badge>}</div><h3 className="text-base font-medium">{task.goal.goal}</h3><p className="mt-1 text-xs text-slate-500">快照 {task.execution_snapshot_id.slice(0, 12)}…</p></div><div className="flex flex-wrap gap-2"><Button variant="secondary" size="sm" onClick={() => void viewTrace(task)}><Activity size={14} />查看追踪</Button><Button variant="secondary" size="sm" onClick={() => control(task, task.state === "PAUSED" ? "resume" : "pause")}>{task.state === "PAUSED" ? <Play size={14} /> : <Pause size={14} />}{task.state === "PAUSED" ? "继续" : "暂停"}</Button><Button variant="danger" size="sm" onClick={() => control(task, "cancel")}><Square size={13} />取消</Button></div></div>{plan && <div className="border-t border-white/7 pt-4"><p className="mb-3 text-xs font-medium uppercase tracking-wider text-slate-500">不可变计划步骤</p><div className="grid gap-2 md:grid-cols-2">{plan.steps.map((step, index) => <div key={step.step_key} className="rounded-xl border border-white/8 bg-slate-950/35 p-3"><div className="flex items-center gap-2"><span className="grid size-6 place-items-center rounded-lg bg-cyan-400/10 text-xs text-cyan-300">{index + 1}</span><b className="text-sm">{step.step_key}</b><StateBadge state={states.get(step.step_key) ?? "PENDING"} /><Badge className="ml-auto">{step.risk_hint}</Badge></div><p className="mt-2 text-xs leading-5 text-slate-400">{step.objective}</p><p className="mt-2 text-xs text-slate-500">依赖：{step.dependencies.length ? step.dependencies.join("、") : "无"}</p><p className="mt-1 text-xs text-slate-500">尝试：{taskAttempts[task.task_id]?.filter((item) => item.step_id === stepIds.get(step.step_key)).length ?? 0} · 证据：{taskEvidence[task.task_id]?.filter((item) => item.step_id === stepIds.get(step.step_key)).length ?? 0}</p></div>)}</div></div>}</CardContent></Card> }) : <Empty title="暂无任务" detail="任务由 Agent Runtime 从对话目标创建并绑定不可变执行快照。" />}</div>
    if (page === "trace") return trace ? <div className="space-y-4"><Card><CardHeader className="flex flex-row items-center justify-between"><div><h2 className="font-medium">任务执行追踪</h2><p className="mt-1 font-mono text-xs text-slate-500">{trace.task_id}</p></div><StateBadge state={trace.state} /></CardHeader></Card><div className="relative space-y-2 before:absolute before:bottom-4 before:left-5 before:top-4 before:w-px before:bg-cyan-400/20">{trace.nodes.map((node) => <Card key={node.node_id} className="relative ml-10"><span className="absolute -left-[29px] top-5 size-3 rounded-full border-2 border-slate-950 bg-cyan-400" /><CardContent className="flex flex-col gap-2 py-4 sm:flex-row sm:items-center sm:justify-between"><div><div className="flex flex-wrap items-center gap-2"><Badge>{node.kind}</Badge><b className="text-sm">{node.label}</b>{node.state && <StateBadge state={node.state} />}</div>{node.safe_detail && <p className="mt-2 text-xs text-rose-300">{node.safe_detail}</p>}<p className="mt-2 font-mono text-xs text-slate-500">{node.trace_id ?? node.node_id}</p></div><time className="text-xs text-slate-500">{node.occurred_at ? new Date(node.occurred_at).toLocaleString() : "—"}</time></CardContent></Card>)}</div></div> : <Empty title="尚未选择任务" detail="在任务卡片中点击“查看追踪”，即可查看 Task、Plan、Step、Attempt、LLM、Capability、MCP、HITL、Replan 和 Artifact 链路。" />
    if (page === "knowledge") return <div className="space-y-4"><Card><CardHeader><h2 className="font-medium">上传知识文档</h2><p className="text-sm text-slate-400">支持 PDF、DOCX、PPTX、XLSX、CSV、Markdown 和 TXT，单文件最大 50 MB。文件先写入 MinIO，再由 Temporal Worker 持久摄取。</p></CardHeader><CardContent><form className="flex flex-col gap-3 sm:flex-row sm:items-center" onSubmit={uploadKnowledge}><input aria-label="选择知识文档" type="file" accept=".pdf,.docx,.pptx,.xlsx,.csv,.md,.txt" onChange={(event) => setKnowledgeFile(event.target.files?.[0] ?? null)} className="min-w-0 flex-1 rounded-xl border border-white/10 bg-slate-950 px-3 py-2 text-sm file:mr-3 file:rounded-lg file:border-0 file:bg-cyan-400/10 file:px-3 file:py-1 file:text-cyan-300" /><Button disabled={!knowledgeFile || uploadingKnowledge}><UploadCloud size={15} />{uploadingKnowledge ? "上传中…" : "上传并摄取"}</Button><Button type="button" variant="secondary" onClick={() => void refresh()}>刷新状态</Button></form></CardContent></Card><div className="grid gap-3">{knowledge.length ? knowledge.map((item) => <Card key={item.ingestion_id}><CardContent className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"><div><div className="flex items-center gap-2"><b className="text-sm">{item.name}</b>{item.document_version !== null && <Badge>文档 v{item.document_version}</Badge>}</div><p className="mt-1 font-mono text-xs text-slate-500">SHA-256 {item.source_hash.slice(0, 18)}… · {item.parser_version}</p>{item.safe_error && <p className="mt-2 text-xs text-rose-300">{knowledgeFailureText(item.safe_error)}</p>}</div><div className="flex flex-wrap items-center gap-2"><StateBadge state={item.state} />{item.state === "FAILED" && <Button type="button" size="sm" variant="secondary" disabled={retryingKnowledge === item.ingestion_id} onClick={() => void retryKnowledge(item)}><RefreshCcw size={14} />{retryingKnowledge === item.ingestion_id ? "重新提交中…" : "重试摄取"}</Button>}{latestKnowledgeIngestionIds.has(item.ingestion_id) && !["PENDING", "RUNNING"].includes(item.state) && <><label className="inline-flex cursor-pointer items-center gap-1 rounded-lg border border-white/10 px-3 py-1.5 text-xs text-slate-300 hover:bg-white/5"><UploadCloud size={14} />{uploadingKnowledgeVersion === item.document_id ? "上传中…" : "上传新版本"}<input aria-label={`上传 ${item.name} 的新版本`} type="file" accept=".pdf,.docx,.pptx,.xlsx,.csv,.md,.txt" className="hidden" disabled={uploadingKnowledgeVersion === item.document_id} onChange={(event) => { void uploadKnowledgeVersion(item, event.target.files?.[0] ?? null); event.target.value = "" }} /></label><Button type="button" size="sm" variant="danger" disabled={deletingKnowledge === item.document_id} onClick={() => void deleteKnowledge(item)}><Trash2 size={14} />{deletingKnowledge === item.document_id ? "删除中…" : "删除"}</Button></>}</div></CardContent></Card>) : <Empty title="暂无知识文档" detail="上传供应商材料后，摄取状态和不可变版本会显示在这里。" />}</div></div>
    if (page === "skills") return <div className="space-y-4">{BUILTIN_SKILLS.map((definition) => <BuiltinSkillCard key={definition.name} title={definition.title} description={definition.description} enabled={skills.some((candidate) => candidate.name === definition.name && candidate.skill_version === definition.version && candidate.state === "ENABLED")} installed={skills.some((candidate) => candidate.name === definition.name)} busy={installingBuiltinSkill === definition.name} onEnable={() => void installBuiltinSkill(definition)} />)}<Card><CardHeader><h2 className="font-medium">Skill 隔离区</h2><p className="text-sm text-slate-400">ZIP 会先进入 MinIO 隔离区，完成清单、Schema、版本、依赖、Capability 和权限校验后等待管理员确认。</p></CardHeader><CardContent><form className="grid gap-3 md:grid-cols-[1fr_160px_160px_auto]" onSubmit={uploadSkill}><input aria-label="选择 Skill 包" type="file" accept=".zip" onChange={(event) => setSkillFile(event.target.files?.[0] ?? null)} className="min-w-0 rounded-xl border border-white/10 bg-slate-950 px-3 py-2 text-sm file:mr-3 file:rounded-lg file:border-0 file:bg-cyan-400/10 file:px-3 file:py-1 file:text-cyan-300" /><Input aria-label="Skill 名称" value={skillName} onChange={(event) => setSkillName(event.target.value)} /><Input aria-label="Skill 版本" value={skillVersion} onChange={(event) => setSkillVersion(event.target.value)} /><Button disabled={!skillFile || uploadingSkill}><UploadCloud size={15} />{uploadingSkill ? "校验中…" : "上传校验"}</Button></form></CardContent></Card><div className="grid gap-3">{skills.length ? skills.map((item) => <Card key={item.installation_id}><CardContent className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between"><div><div className="flex items-center gap-2"><b className="text-sm">{item.name}</b><Badge>v{item.skill_version}</Badge><StateBadge state={item.state} /></div><p className="mt-2 font-mono text-xs text-slate-500">SHA-256 {item.archive_sha256.slice(0, 18)}…</p><p className="mt-2 text-xs text-slate-400">新增权限：{item.permission_diff.length ? item.permission_diff.join("、") : "无"}</p>{item.safe_error && <p className="mt-2 text-xs text-rose-300">{item.safe_error}</p>}</div><div className="flex gap-2">{item.state === "PENDING_CONFIRMATION" && <Button size="sm" onClick={() => skillAction(item, "install")}>确认安装</Button>}{(item.state === "INSTALLED" || item.state === "DISABLED") && <Button size="sm" onClick={() => skillAction(item, "enable")}>启用</Button>}{item.state === "ENABLED" && <Button size="sm" variant="secondary" onClick={() => skillAction(item, "disable")}>停用</Button>}</div></CardContent></Card>) : <Empty title="暂无 Skill" detail="上传声明式 Skill 包后，验证和安装状态会显示在这里。" />}</div></div>
    if (page === "mcp") return <div className="space-y-4"><Card><CardHeader className="flex flex-row items-center justify-between gap-4"><div><h2 className="font-medium">Procurement MCP</h2><p className="mt-1 text-sm text-slate-400">平台通过固定的 stdio 启动配置发现工具，只保存环境变量引用，不保存数据库凭据。</p></div>{!mcpServers.length && <Button disabled={mcpBusy} onClick={() => void registerMcp()}><CircleGauge size={15} />{mcpBusy ? "注册中…" : "注册服务"}</Button>}</CardHeader></Card>{mcpServers.map((item) => <Card key={item.server_id}><CardHeader className="flex flex-row items-start justify-between gap-4"><div><div className="flex flex-wrap items-center gap-2"><h3 className="font-medium">{item.name}</h3><StateBadge state={item.status} /><Badge>{item.transport}</Badge></div><p className="mt-2 text-xs text-slate-500">版本 {item.version} · {item.capability_count} 项能力</p>{item.schema_hash && <p className="mt-1 font-mono text-xs text-slate-500">Schema {item.schema_hash.slice(0, 18)}…</p>}{item.safe_error && <p className="mt-2 text-xs text-rose-300">{item.safe_error}</p>}</div><div className="flex flex-wrap gap-2">{item.status === "REGISTERED" && <Button size="sm" disabled={mcpBusy} onClick={() => void mcpAction(item, "connect")}>连接并发现</Button>}{item.status !== "DISABLED" && item.status !== "REGISTERED" && <Button size="sm" variant="secondary" disabled={mcpBusy} onClick={() => void mcpAction(item, "health")}>健康检查</Button>}{item.status !== "DISABLED" && <Button size="sm" variant="danger" disabled={mcpBusy} onClick={() => void mcpAction(item, "disable")}>停用</Button>}</div></CardHeader></Card>)}{mcpServers.length === 0 && <Empty title="尚未注册 MCP" detail="注册后可连接本地 Procurement MCP，并将发现结果固化为不可变能力快照。" />}<Card><CardHeader><h2 className="font-medium">已发现能力</h2><p className="text-sm text-slate-400">只有当前健康并启用的 Schema 快照会进入 Capability Registry。</p></CardHeader><CardContent className="grid gap-3 md:grid-cols-2">{capabilities.map((item) => <div key={item.id} className="rounded-xl border border-white/8 bg-slate-950/40 p-4"><div className="flex items-start justify-between gap-3"><div><b className="text-sm">{item.name}</b><p className="mt-1 font-mono text-xs text-cyan-300">{item.id}</p></div><Badge>{item.risk_level}</Badge></div><p className="mt-3 text-sm leading-6 text-slate-400">{item.description}</p><div className="mt-3 flex flex-wrap gap-2"><Badge>{item.side_effect_mode}</Badge><Badge>{item.idempotency_mode}</Badge>{item.approval_policy !== "NONE" && <Badge>审批 {item.approval_policy}</Badge>}</div></div>)}{!capabilities.length && <p className="py-8 text-center text-sm text-slate-500 md:col-span-2">服务连接并通过 Schema 校验后显示能力。</p>}</CardContent></Card></div>
    if (page === "models") return <ModelPage api={api} workspace={workspace} models={models} onChanged={refresh} setNotice={setNotice} />
    if (page === "approvals") return <div className="space-y-4">{approvals.length ? approvals.map((item) => <Card key={item.approval_id}><CardHeader className="flex flex-row items-center justify-between"><div><h3 className="font-medium">{item.request.capability_id}</h3><p className="mt-1 text-sm text-slate-400">{item.request.reason}</p></div><StateBadge state={item.state} /></CardHeader><CardContent><div className="mb-4 flex gap-2"><Badge>{item.request.risk}</Badge><Badge>{item.request.evidence_ids.length} 条证据</Badge></div><p className="mb-2 text-xs font-medium uppercase tracking-wider text-slate-500">将执行的参数</p><pre className="mb-4 overflow-x-auto rounded-xl border border-white/8 bg-slate-950/60 p-3 text-xs leading-5 text-slate-300">{JSON.stringify(item.request.parameters, null, 2)}</pre>{item.state === "PENDING" && <div className="flex gap-2"><Button size="sm" onClick={() => decide(item, "approve")}><CheckCircle2 size={14} />批准</Button><Button size="sm" variant="danger" onClick={() => decide(item, "reject")}><X size={14} />拒绝</Button></div>}</CardContent></Card>) : <Empty title="暂无待处理审批" detail="外部副作用必须绑定具体操作和策略决定后才会出现在这里。" />}</div>
    if (page === "artifacts") return <div className="grid gap-3">{artifacts.length ? artifacts.map((item) => <Card key={item.artifact_id}><CardContent className="flex items-center justify-between"><div><b className="text-sm">{item.media_type}</b><p className="mt-1 font-mono text-xs text-slate-500">SHA-256 {item.sha256.slice(0, 18)}…</p></div><Button asChild size="sm" variant="secondary"><a href={api.artifactDownloadUrl(workspace, item.artifact_id)}>下载</a></Button></CardContent></Card>) : <Empty title="暂无制品" detail="完成的 Markdown 或 DOCX 报告会在校验完整性后显示。" />}</div>
    if (page === "audit") return <div className="space-y-4">{incidents.length > 0 && <Card><CardHeader><h2 className="font-medium">一致性事件</h2><p className="text-sm text-slate-400">Runtime Reconciler 检测 PostgreSQL 与 Temporal 状态并记录修复动作。</p></CardHeader><CardContent className="space-y-2">{incidents.map((item) => <div key={item.incident_id} className="rounded-xl border border-amber-400/15 bg-amber-400/5 p-3"><div className="flex items-center gap-2"><Badge>{item.severity}</Badge><b className="text-sm">{item.incident_type}</b></div><p className="mt-2 font-mono text-xs text-slate-400">{JSON.stringify(item.detail)}</p></div>)}</CardContent></Card>}<Card><CardHeader><h2 className="font-medium">审计记录</h2></CardHeader><CardContent className="overflow-x-auto"><table className="w-full min-w-3xl text-left text-sm"><thead className="text-xs text-slate-500"><tr><th className="pb-3">时间</th><th className="pb-3">动作</th><th className="pb-3">Actor</th><th className="pb-3">Resource</th><th className="pb-3">Decision</th><th className="pb-3">Trace</th></tr></thead><tbody>{audit.map((item) => <tr key={item.audit_id} className="border-t border-white/7"><td className="py-3 text-slate-400">{new Date(item.created_at).toLocaleString()}</td><td className="py-3 text-white">{item.action}</td><td className="py-3 text-xs text-slate-400">{item.actor_type} · <span className="font-mono">{item.actor_id.slice(0, 8)}</span></td><td className="py-3 text-xs text-slate-400">{item.resource_type} · <span className="font-mono">{item.resource_id.slice(0, 8)}</span></td><td className="py-3 text-xs text-slate-400">{item.policy_decision ?? "—"}</td><td className="py-3 font-mono text-xs text-cyan-300">{item.trace_id.slice(0, 12)}</td></tr>)}</tbody></table>{!audit.length && <p className="py-10 text-center text-sm text-slate-500">暂无审计记录</p>}</CardContent></Card></div>
    return null
  })()

  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobile ? "sidebar-open" : ""}`}>
        <div className="sidebar-brand"><div><Bot size={20} /></div><span><b>Agent Work</b><small>Enterprise Runtime</small></span></div>
        <button className="new-chat-button" onClick={() => { setConversation(""); setMessages([]); setPage("chat"); setMobile(false) }}><MessageSquarePlus size={17} />新对话</button>
        <nav>{nav.filter((item) => item.id !== "chat").map(({ id, label, icon: Icon }) => <button key={id} onClick={() => { setPage(id); setMobile(false) }} className={page === id ? "active" : ""}><Icon size={17} />{label}{id === "approvals" && approvals.filter((item) => item.state === "PENDING").length > 0 && <Badge>{approvals.filter((item) => item.state === "PENDING").length}</Badge>}</button>)}</nav>
        <div className="sidebar-footer"><Button variant="ghost" className="w-full justify-start" onClick={() => { setToken(""); setWorkspace(""); setConversation("") }}><LogOut size={16} />退出登录</Button></div>
      </aside>
      {mobile && <button aria-label="关闭菜单" className="mobile-overlay" onClick={() => setMobile(false)} />}
      <main className={page === "chat" ? "main-area chat-main" : "main-area"}>
        <header className="topbar">
          <div className="topbar-title"><Button variant="ghost" size="icon" className="mobile-menu" onClick={() => setMobile(true)}><Menu size={19} /></Button><div><h1>{page === "chat" ? "Agent Work" : nav.find((item) => item.id === page)?.label}</h1><p>{activeWorkspace?.name ?? "正在准备工作区"}</p></div></div>
          <select aria-label="选择工作区" value={workspace} onChange={(event) => { setWorkspace(event.target.value); setConversation(""); setMessages([]) }}>{workspaces.map((item) => <option key={item.workspace_id} value={item.workspace_id}>{item.name}</option>)}</select>
        </header>
        <div className={page === "chat" ? "content-area chat-content" : "content-area"}>{notice && <div role="alert" className={notice.includes("已创建") ? "notice success" : "notice error"}><span>{notice}</span><button onClick={() => setNotice("")}><X size={16} /></button></div>}{content}</div>
      </main>
    </div>
  )
}
