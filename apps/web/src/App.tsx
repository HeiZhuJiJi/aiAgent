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
  Send,
  Settings2,
  ShieldCheck,
  Square,
  UploadCloud,
  Workflow,
  X,
} from "lucide-react"
import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react"

import { ApiClient, type Approval, type Artifact, type AuditEvent, type CapabilityDescriptor, type ConsistencyIncident, type Conversation, type Evidence, type KnowledgeIngestion, type MCPServer, type Message, type ModelConfiguration, type Plan, type SkillInstallation, type StepAttempt, type Task, type TaskStep, type TaskTrace, type Workspace } from "./api"
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
      onLogin(result.access_token)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法登录")
    } finally { setBusy(false) }
  }

  return (
    <main className="grid min-h-screen place-items-center bg-slate-950 px-5 text-slate-100">
      <div className="fixed inset-0 bg-[radial-gradient(circle_at_20%_0%,rgba(34,211,238,.16),transparent_34%),radial-gradient(circle_at_90%_80%,rgba(99,102,241,.13),transparent_38%)]" />
      <Card className="relative w-full max-w-md border-white/12 bg-slate-900/85 shadow-2xl shadow-black/40 backdrop-blur-xl">
        <CardContent className="p-8">
          <div className="mb-8 flex items-center gap-3">
            <div className="grid size-11 place-items-center rounded-2xl bg-cyan-400 text-slate-950"><Bot size={23} /></div>
            <div><h1 className="text-lg font-semibold">企业智能体工作平台</h1><p className="text-sm text-slate-400">受治理的持久化 Agent Runtime</p></div>
          </div>
          <form className="space-y-4" onSubmit={submit}>
            <div><label className="mb-1.5 block text-sm text-slate-300">邮箱</label><Input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required /></div>
            <div><label className="mb-1.5 block text-sm text-slate-300">密码</label><Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} minLength={12} required /></div>
            {error && <p role="alert" className="rounded-xl bg-rose-500/10 px-3 py-2 text-sm text-rose-200">{error}</p>}
            <Button className="w-full" disabled={busy}>{busy ? "连接中…" : mode === "login" ? "登录" : "注册并登录"}</Button>
          </form>
          <button className="mt-5 w-full text-sm text-slate-400 hover:text-cyan-300" onClick={() => setMode(mode === "login" ? "register" : "login")}>
            {mode === "login" ? "首次使用？创建本地账户" : "已有账户？返回登录"}
          </button>
        </CardContent>
      </Card>
    </main>
  )
}

function Empty({ title, detail }: { title: string; detail: string }) {
  return <Card><CardContent className="py-14 text-center"><div className="mx-auto mb-4 grid size-12 place-items-center rounded-2xl bg-white/6"><Database className="text-slate-400" /></div><h3 className="font-medium text-white">{title}</h3><p className="mx-auto mt-2 max-w-md text-sm leading-6 text-slate-400">{detail}</p></CardContent></Card>
}

function StateBadge({ state }: { state: string }) {
  const active = ["RUNNING", "APPROVED", "COMPLETED", "ACTIVE", "ENABLED", "SUCCEEDED"].includes(state)
  return <Badge className={active ? "border-emerald-400/20 bg-emerald-400/10 text-emerald-300" : ""}>{state}</Badge>
}

export default function App() {
  const [token, setToken] = useState("")
  const [page, setPage] = useState<Page>("tasks")
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
  const [skills, setSkills] = useState<SkillInstallation[]>([])
  const [skillFile, setSkillFile] = useState<File | null>(null)
  const [skillName, setSkillName] = useState("supplier-risk")
  const [skillVersion, setSkillVersion] = useState("1.0.0")
  const [uploadingSkill, setUploadingSkill] = useState(false)
  const [mcpServers, setMcpServers] = useState<MCPServer[]>([])
  const [capabilities, setCapabilities] = useState<CapabilityDescriptor[]>([])
  const [models, setModels] = useState<ModelConfiguration[]>([])
  const [mcpBusy, setMcpBusy] = useState(false)
  const [modelBusy, setModelBusy] = useState(false)
  const [modelKind, setModelKind] = useState<ModelConfiguration["kind"]>("CHAT")
  const [modelProvider, setModelProvider] = useState<ModelConfiguration["provider"]>("OpenAICompatible")
  const [modelBaseUrl, setModelBaseUrl] = useState("")
  const [modelName, setModelName] = useState("")
  const [modelApiKey, setModelApiKey] = useState("")
  const [modelExternal, setModelExternal] = useState(false)
  const [modelProbe, setModelProbe] = useState<Record<string, string>>({})
  const [notice, setNotice] = useState("")
  const [newWorkspace, setNewWorkspace] = useState("")
  const [composer, setComposer] = useState("")
  const [streamingReply, setStreamingReply] = useState("")
  const [chatBusy, setChatBusy] = useState(false)
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

  async function createWorkspace(event: FormEvent) {
    event.preventDefault(); if (!newWorkspace.trim()) return
    try { const item = await api.createWorkspace(newWorkspace); setNewWorkspace(""); setWorkspace(item.workspace_id); await refresh() }
    catch (reason) { setNotice(reason instanceof Error ? reason.message : "创建失败") }
  }

  async function startChat() {
    if (!workspace) return
    try { const item = await api.createConversation(workspace, "新供应商分析"); setConversation(item.conversation_id); setMessages([]); setPage("chat"); await refresh() }
    catch (reason) { setNotice(reason instanceof Error ? reason.message : "创建失败") }
  }

  async function sendMessage(event: FormEvent) {
    event.preventDefault(); if (!workspace || !conversation || !composer.trim()) return
    const chatModel = models.find((item) => item.kind === "CHAT" && item.enabled)
    if (!chatModel) { setNotice("请先在“模型”页面配置可用的 Chat 模型"); return }
    const content = composer
    setComposer(""); setStreamingReply(""); setChatBusy(true)
    try {
      await api.streamChat(
        {
          workspace_id: workspace,
          conversation_id: conversation,
          model_config_id: chatModel.config_id,
          content,
        },
        (chunk) => setStreamingReply((current) => current + chunk),
      )
      setMessages(await api.messages(workspace, conversation))
      setStreamingReply("")
    }
    catch (reason) { setNotice(reason instanceof Error ? reason.message : "发送失败") }
    finally { setChatBusy(false) }
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

  async function configureModel(event: FormEvent) {
    event.preventDefault()
    if (!workspace || !modelBaseUrl.trim() || !modelName.trim()) return
    setModelBusy(true)
    try {
      await api.createModel({
        workspace_id: workspace,
        kind: modelKind,
        provider: modelProvider,
        base_url: modelBaseUrl,
        model_name: modelName,
        external: modelExternal,
        api_key: modelApiKey,
      })
      setModelApiKey("")
      await refresh()
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "模型配置失败")
    } finally {
      setModelBusy(false)
    }
  }

  async function probeModel(item: ModelConfiguration) {
    setModelBusy(true)
    try {
      const result = await api.probeModel(workspace, item.config_id)
      setModelProbe((current) => ({ ...current, [item.config_id]: result.content }))
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "模型连通测试失败")
    } finally {
      setModelBusy(false)
    }
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
    if (!workspace && page !== "workspaces") return <Empty title="先创建工作区" detail="工作区隔离任务、知识、审批、制品和审计记录。" />
    if (page === "workspaces") return <Card><CardHeader><h2 className="font-medium">工作区</h2></CardHeader><CardContent><form className="mb-5 flex gap-2" onSubmit={createWorkspace}><Input placeholder="工作区名称" value={newWorkspace} onChange={(e) => setNewWorkspace(e.target.value)} /><Button>创建</Button></form><div className="space-y-2">{workspaces.map((item) => <button key={item.workspace_id} onClick={() => setWorkspace(item.workspace_id)} className="flex w-full items-center justify-between rounded-xl border border-white/8 p-4 text-left hover:bg-white/5"><span><b className="block text-sm">{item.name}</b><small className="text-slate-500">{item.role}</small></span><ChevronRight size={18} /></button>)}</div></CardContent></Card>
    if (page === "chat") return <div className="flex min-h-[68vh] flex-col rounded-2xl border border-white/8 bg-slate-900/55"><div className="border-b border-white/7 px-5 py-4"><h2 className="font-medium">新供应商分析</h2><p className="text-xs text-slate-500">消息持久保存，并通过工作区已配置的 Chat 模型实时生成回复。</p></div><div className="flex-1 space-y-4 p-5">{messages.map((item) => <div key={item.message_id} className={`max-w-2xl rounded-2xl px-4 py-3 text-sm ${item.role === "USER" ? "ml-auto rounded-tr-md bg-cyan-400 text-slate-950" : "mr-auto rounded-tl-md border border-white/8 bg-slate-800 text-slate-100"}`}>{item.content}</div>)}{streamingReply && <div className="mr-auto max-w-2xl rounded-2xl rounded-tl-md border border-cyan-400/20 bg-slate-800 px-4 py-3 text-sm text-slate-100">{streamingReply}<span className="ml-1 animate-pulse text-cyan-300">▍</span></div>}{!conversation && <Empty title="开始新对话" detail="创建对话后可以提交供应商分析目标。" />}</div><form className="flex gap-2 border-t border-white/7 p-4" onSubmit={sendMessage}><Input placeholder="输入目标、约束和交付物…" value={composer} onChange={(e) => setComposer(e.target.value)} disabled={!conversation || chatBusy} /><Button size="icon" aria-label="发送" disabled={!conversation || chatBusy}><Send size={17} /></Button></form></div>
    if (page === "chats") return <Card><CardHeader className="flex flex-row items-center justify-between"><h2 className="font-medium">对话</h2><Button size="sm" onClick={startChat}><MessageSquarePlus size={15} />新对话</Button></CardHeader><CardContent className="space-y-2">{conversations.length ? conversations.map((item) => <button key={item.conversation_id} onClick={async () => { setConversation(item.conversation_id); setMessages(await api.messages(workspace, item.conversation_id)); setPage("chat") }} className="flex w-full items-center justify-between rounded-xl border border-white/8 p-4 text-left hover:bg-white/5"><span><b className="block text-sm">{item.title}</b><small className="text-slate-500">版本 {item.version}</small></span><ChevronRight size={18} /></button>) : <Empty title="暂无对话" detail="创建第一条对话，记录供应商分析目标。" />}</CardContent></Card>
    if (page === "tasks") return <div className="grid gap-4">{tasks.length ? tasks.map((task) => { const versions = plans[task.task_id] ?? []; const plan = versions.at(-1); const materialized = taskSteps[task.task_id] ?? []; const states = new Map(materialized.map((item) => [item.spec.step_key, item.state])); const stepIds = new Map(materialized.map((item) => [item.spec.step_key, item.step_id])); return <Card key={task.task_id}><CardContent className="space-y-5"><div className="flex flex-col gap-5 md:flex-row md:items-center md:justify-between"><div><div className="mb-2 flex items-center gap-2"><StateBadge state={task.state} /><span className="text-xs text-slate-500">v{task.version}</span>{plan && <Badge>Plan v{plan.version}</Badge>}</div><h3 className="text-base font-medium">{task.goal.goal}</h3><p className="mt-1 text-xs text-slate-500">快照 {task.execution_snapshot_id.slice(0, 12)}…</p></div><div className="flex flex-wrap gap-2"><Button variant="secondary" size="sm" onClick={() => void viewTrace(task)}><Activity size={14} />查看追踪</Button><Button variant="secondary" size="sm" onClick={() => control(task, task.state === "PAUSED" ? "resume" : "pause")}>{task.state === "PAUSED" ? <Play size={14} /> : <Pause size={14} />}{task.state === "PAUSED" ? "继续" : "暂停"}</Button><Button variant="danger" size="sm" onClick={() => control(task, "cancel")}><Square size={13} />取消</Button></div></div>{plan && <div className="border-t border-white/7 pt-4"><p className="mb-3 text-xs font-medium uppercase tracking-wider text-slate-500">不可变计划步骤</p><div className="grid gap-2 md:grid-cols-2">{plan.steps.map((step, index) => <div key={step.step_key} className="rounded-xl border border-white/8 bg-slate-950/35 p-3"><div className="flex items-center gap-2"><span className="grid size-6 place-items-center rounded-lg bg-cyan-400/10 text-xs text-cyan-300">{index + 1}</span><b className="text-sm">{step.step_key}</b><StateBadge state={states.get(step.step_key) ?? "PENDING"} /><Badge className="ml-auto">{step.risk_hint}</Badge></div><p className="mt-2 text-xs leading-5 text-slate-400">{step.objective}</p><p className="mt-2 text-xs text-slate-500">依赖：{step.dependencies.length ? step.dependencies.join("、") : "无"}</p><p className="mt-1 text-xs text-slate-500">尝试：{taskAttempts[task.task_id]?.filter((item) => item.step_id === stepIds.get(step.step_key)).length ?? 0} · 证据：{taskEvidence[task.task_id]?.filter((item) => item.step_id === stepIds.get(step.step_key)).length ?? 0}</p></div>)}</div></div>}</CardContent></Card> }) : <Empty title="暂无任务" detail="任务由 Agent Runtime 从对话目标创建并绑定不可变执行快照。" />}</div>
    if (page === "trace") return trace ? <div className="space-y-4"><Card><CardHeader className="flex flex-row items-center justify-between"><div><h2 className="font-medium">任务执行追踪</h2><p className="mt-1 font-mono text-xs text-slate-500">{trace.task_id}</p></div><StateBadge state={trace.state} /></CardHeader></Card><div className="relative space-y-2 before:absolute before:bottom-4 before:left-5 before:top-4 before:w-px before:bg-cyan-400/20">{trace.nodes.map((node) => <Card key={node.node_id} className="relative ml-10"><span className="absolute -left-[29px] top-5 size-3 rounded-full border-2 border-slate-950 bg-cyan-400" /><CardContent className="flex flex-col gap-2 py-4 sm:flex-row sm:items-center sm:justify-between"><div><div className="flex flex-wrap items-center gap-2"><Badge>{node.kind}</Badge><b className="text-sm">{node.label}</b>{node.state && <StateBadge state={node.state} />}</div>{node.safe_detail && <p className="mt-2 text-xs text-rose-300">{node.safe_detail}</p>}<p className="mt-2 font-mono text-xs text-slate-500">{node.trace_id ?? node.node_id}</p></div><time className="text-xs text-slate-500">{node.occurred_at ? new Date(node.occurred_at).toLocaleString() : "—"}</time></CardContent></Card>)}</div></div> : <Empty title="尚未选择任务" detail="在任务卡片中点击“查看追踪”，即可查看 Task、Plan、Step、Attempt、LLM、Capability、MCP、HITL、Replan 和 Artifact 链路。" />
    if (page === "knowledge") return <div className="space-y-4"><Card><CardHeader><h2 className="font-medium">上传知识文档</h2><p className="text-sm text-slate-400">支持 PDF、DOCX、PPTX、XLSX、CSV、Markdown 和 TXT，单文件最大 50 MB。文件先写入 MinIO，再由 Temporal Worker 持久摄取。</p></CardHeader><CardContent><form className="flex flex-col gap-3 sm:flex-row sm:items-center" onSubmit={uploadKnowledge}><input aria-label="选择知识文档" type="file" accept=".pdf,.docx,.pptx,.xlsx,.csv,.md,.txt" onChange={(event) => setKnowledgeFile(event.target.files?.[0] ?? null)} className="min-w-0 flex-1 rounded-xl border border-white/10 bg-slate-950 px-3 py-2 text-sm file:mr-3 file:rounded-lg file:border-0 file:bg-cyan-400/10 file:px-3 file:py-1 file:text-cyan-300" /><Button disabled={!knowledgeFile || uploadingKnowledge}><UploadCloud size={15} />{uploadingKnowledge ? "上传中…" : "上传并摄取"}</Button><Button type="button" variant="secondary" onClick={() => void refresh()}>刷新状态</Button></form></CardContent></Card><div className="grid gap-3">{knowledge.length ? knowledge.map((item) => <Card key={item.ingestion_id}><CardContent className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between"><div><b className="text-sm">{item.name}</b><p className="mt-1 font-mono text-xs text-slate-500">SHA-256 {item.source_hash.slice(0, 18)}… · {item.parser_version}</p>{item.safe_error && <p className="mt-2 text-xs text-rose-300">{item.safe_error}</p>}</div><StateBadge state={item.state} /></CardContent></Card>) : <Empty title="暂无知识文档" detail="上传供应商材料后，摄取状态和不可变版本会显示在这里。" />}</div></div>
    if (page === "skills") return <div className="space-y-4"><Card><CardHeader><h2 className="font-medium">Skill 隔离区</h2><p className="text-sm text-slate-400">ZIP 会先进入 MinIO 隔离区，完成清单、Schema、版本、依赖、Capability 和权限校验后等待管理员确认。</p></CardHeader><CardContent><form className="grid gap-3 md:grid-cols-[1fr_160px_160px_auto]" onSubmit={uploadSkill}><input aria-label="选择 Skill 包" type="file" accept=".zip" onChange={(event) => setSkillFile(event.target.files?.[0] ?? null)} className="min-w-0 rounded-xl border border-white/10 bg-slate-950 px-3 py-2 text-sm file:mr-3 file:rounded-lg file:border-0 file:bg-cyan-400/10 file:px-3 file:py-1 file:text-cyan-300" /><Input aria-label="Skill 名称" value={skillName} onChange={(event) => setSkillName(event.target.value)} /><Input aria-label="Skill 版本" value={skillVersion} onChange={(event) => setSkillVersion(event.target.value)} /><Button disabled={!skillFile || uploadingSkill}><UploadCloud size={15} />{uploadingSkill ? "校验中…" : "上传校验"}</Button></form></CardContent></Card><div className="grid gap-3">{skills.length ? skills.map((item) => <Card key={item.installation_id}><CardContent className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between"><div><div className="flex items-center gap-2"><b className="text-sm">{item.name}</b><Badge>v{item.skill_version}</Badge><StateBadge state={item.state} /></div><p className="mt-2 font-mono text-xs text-slate-500">SHA-256 {item.archive_sha256.slice(0, 18)}…</p><p className="mt-2 text-xs text-slate-400">新增权限：{item.permission_diff.length ? item.permission_diff.join("、") : "无"}</p>{item.safe_error && <p className="mt-2 text-xs text-rose-300">{item.safe_error}</p>}</div><div className="flex gap-2">{item.state === "PENDING_CONFIRMATION" && <Button size="sm" onClick={() => skillAction(item, "install")}>确认安装</Button>}{(item.state === "INSTALLED" || item.state === "DISABLED") && <Button size="sm" onClick={() => skillAction(item, "enable")}>启用</Button>}{item.state === "ENABLED" && <Button size="sm" variant="secondary" onClick={() => skillAction(item, "disable")}>停用</Button>}</div></CardContent></Card>) : <Empty title="暂无 Skill" detail="上传声明式 Skill 包后，验证和安装状态会显示在这里。" />}</div></div>
    if (page === "mcp") return <div className="space-y-4"><Card><CardHeader className="flex flex-row items-center justify-between gap-4"><div><h2 className="font-medium">Procurement MCP</h2><p className="mt-1 text-sm text-slate-400">平台通过固定的 stdio 启动配置发现工具，只保存环境变量引用，不保存数据库凭据。</p></div>{!mcpServers.length && <Button disabled={mcpBusy} onClick={() => void registerMcp()}><CircleGauge size={15} />{mcpBusy ? "注册中…" : "注册服务"}</Button>}</CardHeader></Card>{mcpServers.map((item) => <Card key={item.server_id}><CardHeader className="flex flex-row items-start justify-between gap-4"><div><div className="flex flex-wrap items-center gap-2"><h3 className="font-medium">{item.name}</h3><StateBadge state={item.status} /><Badge>{item.transport}</Badge></div><p className="mt-2 text-xs text-slate-500">版本 {item.version} · {item.capability_count} 项能力</p>{item.schema_hash && <p className="mt-1 font-mono text-xs text-slate-500">Schema {item.schema_hash.slice(0, 18)}…</p>}{item.safe_error && <p className="mt-2 text-xs text-rose-300">{item.safe_error}</p>}</div><div className="flex flex-wrap gap-2">{item.status === "REGISTERED" && <Button size="sm" disabled={mcpBusy} onClick={() => void mcpAction(item, "connect")}>连接并发现</Button>}{item.status !== "DISABLED" && item.status !== "REGISTERED" && <Button size="sm" variant="secondary" disabled={mcpBusy} onClick={() => void mcpAction(item, "health")}>健康检查</Button>}{item.status !== "DISABLED" && <Button size="sm" variant="danger" disabled={mcpBusy} onClick={() => void mcpAction(item, "disable")}>停用</Button>}</div></CardHeader></Card>)}{mcpServers.length === 0 && <Empty title="尚未注册 MCP" detail="注册后可连接本地 Procurement MCP，并将发现结果固化为不可变能力快照。" />}<Card><CardHeader><h2 className="font-medium">已发现能力</h2><p className="text-sm text-slate-400">只有当前健康并启用的 Schema 快照会进入 Capability Registry。</p></CardHeader><CardContent className="grid gap-3 md:grid-cols-2">{capabilities.map((item) => <div key={item.id} className="rounded-xl border border-white/8 bg-slate-950/40 p-4"><div className="flex items-start justify-between gap-3"><div><b className="text-sm">{item.name}</b><p className="mt-1 font-mono text-xs text-cyan-300">{item.id}</p></div><Badge>{item.risk_level}</Badge></div><p className="mt-3 text-sm leading-6 text-slate-400">{item.description}</p><div className="mt-3 flex flex-wrap gap-2"><Badge>{item.side_effect_mode}</Badge><Badge>{item.idempotency_mode}</Badge>{item.approval_policy !== "NONE" && <Badge>审批 {item.approval_policy}</Badge>}</div></div>)}{!capabilities.length && <p className="py-8 text-center text-sm text-slate-500 md:col-span-2">服务连接并通过 Schema 校验后显示能力。</p>}</CardContent></Card></div>
    if (page === "models") return <div className="space-y-4"><Card><CardHeader><h2 className="font-medium">配置模型</h2><p className="text-sm text-slate-400">API Key 只写入工作区加密 Secret，不会返回到浏览器、Trace 或 Audit。Docker 中访问宿主机模型可使用 host.docker.internal。</p></CardHeader><CardContent><form className="grid gap-3 md:grid-cols-2" onSubmit={configureModel}><select aria-label="模型用途" value={modelKind} onChange={(event) => setModelKind(event.target.value as ModelConfiguration["kind"])} className="h-10 rounded-xl border border-white/10 bg-slate-950 px-3 text-sm"><option value="CHAT">Chat</option><option value="EMBEDDING">Embedding</option><option value="RERANKER">Reranker</option></select><select aria-label="模型提供方" value={modelProvider} onChange={(event) => setModelProvider(event.target.value as ModelConfiguration["provider"])} className="h-10 rounded-xl border border-white/10 bg-slate-950 px-3 text-sm"><option value="OpenAICompatible">OpenAI Compatible</option><option value="Qwen">Qwen</option><option value="DeepSeek">DeepSeek</option></select><Input aria-label="模型 Base URL" placeholder="http://host.docker.internal:8000/v1" value={modelBaseUrl} onChange={(event) => setModelBaseUrl(event.target.value)} /><Input aria-label="模型名称" placeholder="Qwen/Qwen3.8-27B" value={modelName} onChange={(event) => setModelName(event.target.value)} /><Input aria-label="模型 API Key" type="password" placeholder={modelExternal ? "外部模型必填" : "本地模型可留空"} value={modelApiKey} onChange={(event) => setModelApiKey(event.target.value)} /><label className="flex items-center gap-2 rounded-xl border border-white/10 px-3 text-sm text-slate-300"><input type="checkbox" checked={modelExternal} onChange={(event) => setModelExternal(event.target.checked)} />外部模型（仅允许 HTTPS）</label><Button className="md:col-span-2" disabled={modelBusy}>{modelBusy ? "保存中…" : "加密保存配置"}</Button></form></CardContent></Card><div className="grid gap-3">{models.length ? models.map((item) => <Card key={item.config_id}><CardContent className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between"><div><div className="flex flex-wrap items-center gap-2"><b className="text-sm">{item.model_name}</b><Badge>{item.kind}</Badge><Badge>{item.provider}</Badge><StateBadge state={item.enabled ? "ENABLED" : "DISABLED"} /></div><p className="mt-2 break-all font-mono text-xs text-slate-500">{item.base_url}</p><p className="mt-2 text-xs text-slate-400">{item.external ? "外部" : "本地"} · {item.has_secret ? "密钥已加密" : "无密钥"} · v{item.version}</p>{modelProbe[item.config_id] && <p className="mt-3 rounded-xl bg-emerald-400/8 p-3 text-sm text-emerald-200">{modelProbe[item.config_id]}</p>}</div><Button size="sm" variant="secondary" disabled={modelBusy} onClick={() => void probeModel(item)}>测试连接</Button></CardContent></Card>) : <Empty title="尚未配置模型" detail="保存 Chat、Embedding 或 Reranker 端点后，可在这里执行真实连通测试。" />}</div></div>
    if (page === "approvals") return <div className="space-y-4">{approvals.length ? approvals.map((item) => <Card key={item.approval_id}><CardHeader className="flex flex-row items-center justify-between"><div><h3 className="font-medium">{item.request.capability_id}</h3><p className="mt-1 text-sm text-slate-400">{item.request.reason}</p></div><StateBadge state={item.state} /></CardHeader><CardContent><div className="mb-4 flex gap-2"><Badge>{item.request.risk}</Badge><Badge>{item.request.evidence_ids.length} 条证据</Badge></div><p className="mb-2 text-xs font-medium uppercase tracking-wider text-slate-500">将执行的参数</p><pre className="mb-4 overflow-x-auto rounded-xl border border-white/8 bg-slate-950/60 p-3 text-xs leading-5 text-slate-300">{JSON.stringify(item.request.parameters, null, 2)}</pre>{item.state === "PENDING" && <div className="flex gap-2"><Button size="sm" onClick={() => decide(item, "approve")}><CheckCircle2 size={14} />批准</Button><Button size="sm" variant="danger" onClick={() => decide(item, "reject")}><X size={14} />拒绝</Button></div>}</CardContent></Card>) : <Empty title="暂无待处理审批" detail="外部副作用必须绑定具体操作和策略决定后才会出现在这里。" />}</div>
    if (page === "artifacts") return <div className="grid gap-3">{artifacts.length ? artifacts.map((item) => <Card key={item.artifact_id}><CardContent className="flex items-center justify-between"><div><b className="text-sm">{item.media_type}</b><p className="mt-1 font-mono text-xs text-slate-500">SHA-256 {item.sha256.slice(0, 18)}…</p></div><Button asChild size="sm" variant="secondary"><a href={api.artifactDownloadUrl(workspace, item.artifact_id)}>下载</a></Button></CardContent></Card>) : <Empty title="暂无制品" detail="完成的 Markdown 或 DOCX 报告会在校验完整性后显示。" />}</div>
    if (page === "audit") return <div className="space-y-4">{incidents.length > 0 && <Card><CardHeader><h2 className="font-medium">一致性事件</h2><p className="text-sm text-slate-400">Runtime Reconciler 检测 PostgreSQL 与 Temporal 状态并记录修复动作。</p></CardHeader><CardContent className="space-y-2">{incidents.map((item) => <div key={item.incident_id} className="rounded-xl border border-amber-400/15 bg-amber-400/5 p-3"><div className="flex items-center gap-2"><Badge>{item.severity}</Badge><b className="text-sm">{item.incident_type}</b></div><p className="mt-2 font-mono text-xs text-slate-400">{JSON.stringify(item.detail)}</p></div>)}</CardContent></Card>}<Card><CardHeader><h2 className="font-medium">审计记录</h2></CardHeader><CardContent className="overflow-x-auto"><table className="w-full min-w-3xl text-left text-sm"><thead className="text-xs text-slate-500"><tr><th className="pb-3">时间</th><th className="pb-3">动作</th><th className="pb-3">Actor</th><th className="pb-3">Resource</th><th className="pb-3">Decision</th><th className="pb-3">Trace</th></tr></thead><tbody>{audit.map((item) => <tr key={item.audit_id} className="border-t border-white/7"><td className="py-3 text-slate-400">{new Date(item.created_at).toLocaleString()}</td><td className="py-3 text-white">{item.action}</td><td className="py-3 text-xs text-slate-400">{item.actor_type} · <span className="font-mono">{item.actor_id.slice(0, 8)}</span></td><td className="py-3 text-xs text-slate-400">{item.resource_type} · <span className="font-mono">{item.resource_id.slice(0, 8)}</span></td><td className="py-3 text-xs text-slate-400">{item.policy_decision ?? "—"}</td><td className="py-3 font-mono text-xs text-cyan-300">{item.trace_id.slice(0, 12)}</td></tr>)}</tbody></table>{!audit.length && <p className="py-10 text-center text-sm text-slate-500">暂无审计记录</p>}</CardContent></Card></div>
    return null
  })()

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <aside className={`fixed inset-y-0 left-0 z-40 w-64 border-r border-white/7 bg-slate-950/95 p-4 backdrop-blur transition-transform md:translate-x-0 ${mobile ? "translate-x-0" : "-translate-x-full"}`}>
        <div className="mb-7 flex items-center gap-3 px-2"><div className="grid size-9 place-items-center rounded-xl bg-cyan-400 text-slate-950"><Bot size={20} /></div><div><b className="block text-sm">Agent Work</b><span className="text-xs text-slate-500">Enterprise Runtime</span></div></div>
        <nav className="space-y-1">{nav.map(({ id, label, icon: Icon }) => <button key={id} onClick={() => { setPage(id); setMobile(false); if (id === "chat" && !conversation) void startChat() }} className={`flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition ${page === id ? "bg-cyan-400/12 text-cyan-300" : "text-slate-400 hover:bg-white/5 hover:text-white"}`}><Icon size={17} />{label}{id === "approvals" && approvals.filter((a) => a.state === "PENDING").length > 0 && <Badge className="ml-auto border-cyan-400/20 bg-cyan-400/10 text-cyan-300">{approvals.filter((a) => a.state === "PENDING").length}</Badge>}</button>)}</nav>
        <div className="absolute inset-x-4 bottom-4 border-t border-white/7 pt-4"><Button variant="ghost" className="w-full justify-start" onClick={() => setToken("")}><LogOut size={16} />退出登录</Button></div>
      </aside>
      {mobile && <button aria-label="关闭菜单" className="fixed inset-0 z-30 bg-black/60 md:hidden" onClick={() => setMobile(false)} />}
      <main className="md:pl-64"><header className="sticky top-0 z-20 flex h-16 items-center justify-between border-b border-white/7 bg-slate-950/80 px-4 backdrop-blur md:px-7"><div className="flex items-center gap-3"><Button variant="ghost" size="icon" className="md:hidden" onClick={() => setMobile(true)}><Menu size={19} /></Button><div><h1 className="text-sm font-medium">{nav.find((item) => item.id === page)?.label}</h1><p className="text-xs text-slate-500">{activeWorkspace?.name ?? "未选择工作区"}</p></div></div><div className="flex items-center gap-2"><select aria-label="选择工作区" value={workspace} onChange={(e) => setWorkspace(e.target.value)} className="h-9 rounded-xl border border-white/10 bg-slate-900 px-3 text-xs outline-none">{workspaces.map((item) => <option key={item.workspace_id} value={item.workspace_id}>{item.name}</option>)}</select><Button variant="ghost" size="icon"><Settings2 size={17} /></Button></div></header><div className="mx-auto max-w-6xl p-4 md:p-7">{notice && <div role="alert" className="mb-4 flex items-center justify-between rounded-xl border border-rose-400/20 bg-rose-400/10 px-4 py-3 text-sm text-rose-200"><span>{notice}</span><button onClick={() => setNotice("")}><X size={16} /></button></div>}{content}</div></main>
    </div>
  )
}
