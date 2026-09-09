export type Workspace = { workspace_id: string; name: string; role: string; version: number }
export type Conversation = {
  conversation_id: string
  workspace_id: string
  title: string
  version: number
  created_at: string
}
export type Message = {
  message_id: string
  conversation_id: string
  workspace_id: string
  role: "SYSTEM" | "USER" | "ASSISTANT"
  content: string
  created_at: string
}
export type Task = {
  task_id: string
  workspace_id: string
  state: string
  version: number
  execution_snapshot_id: string
  goal: { goal: string; constraints: string[]; deliverables: string[] }
}
export type Plan = {
  plan_id: string
  task_id: string
  workspace_id: string
  execution_snapshot_id: string
  version: number
  goal: string
  constraints: string[]
  deliverables: string[]
  steps: {
    step_key: string
    objective: string
    dependencies: string[]
    expected_output: string
    risk_hint: string
    execution_mode: string
  }[]
}
export type TaskStep = {
  step_id: string
  workspace_id: string
  task_id: string
  plan_id: string
  spec: Plan["steps"][number]
  state: string
  version: number
  fencing_token: number
}
export type StepAttempt = {
  attempt_id: string
  workspace_id: string
  step_id: string
  worker_id: string
  state: string
  fencing_token: number
  lease_expires_at: string
  started_at: string
  completed_at: string | null
  failure: { code: string; safe_detail: string; trace_id: string } | null
  trace_id: string
}
export type Evidence = {
  evidence_id: string
  workspace_id: string
  task_id: string
  step_id: string
  source_type: "DOCUMENT" | "WEB" | "MCP" | "DERIVED"
  source_id: string
  locator: string
  content: string
  content_hash: string
  source_hash: string
  source_version: string
  confidence: number
  relevance: number
  retrieved_at: string
  generated_by: string
  derived_from: string[]
}
export type Approval = {
  approval_id: string
  workspace_id: string
  state: string
  version: number
  request: { capability_id: string; reason: string; risk: string; evidence_ids: string[]; parameters: Record<string, unknown> }
}
export type Artifact = {
  artifact_id: string
  workspace_id: string
  source_task: string
  sha256: string
  media_type: string
  created_at: string
}
export type AuditEvent = {
  audit_id: string
  workspace_id: string
  actor_type: "USER" | "SERVICE_ACCOUNT" | "SYSTEM" | "AGENT"
  actor_id: string
  task_id: string | null
  step_id: string | null
  action: string
  resource_type: string
  resource_id: string
  capability_id: string | null
  policy_decision: string | null
  approval_id: string | null
  result: Record<string, unknown> | null
  trace_id: string
  created_at: string
}
export type TraceNode = {
  node_id: string
  parent_id: string | null
  kind: "TASK" | "ROUTER" | "PLAN" | "STEP" | "ATTEMPT" | "EVIDENCE" | "CAPABILITY" | "LLM" | "MCP" | "REPLAN" | "HITL" | "ARTIFACT" | "FAILURE" | "EVENT" | "FINISHED"
  label: string
  state: string | null
  occurred_at: string | null
  trace_id: string | null
  safe_detail: string | null
}
export type TaskTrace = {
  workspace_id: string
  task_id: string
  state: string
  nodes: TraceNode[]
}
export type KnowledgeIngestion = {
  ingestion_id: string
  workspace_id: string
  document_id: string
  name: string
  media_type: string
  source_hash: string
  parser_version: string
  state: "PENDING" | "RUNNING" | "SUCCEEDED" | "FAILED"
  version: number
  document_version_id: string | null
  safe_error: string | null
  created_at: string
  completed_at: string | null
}
export type SkillInstallation = {
  installation_id: string
  workspace_id: string
  skill_id: string | null
  skill_version_id: string | null
  name: string
  skill_version: string
  archive_sha256: string
  state: "QUARANTINED" | "REJECTED" | "PENDING_CONFIRMATION" | "INSTALLED" | "ENABLED" | "DISABLED"
  version: number
  permission_diff: string[]
  safe_error: string | null
}
export type MCPServer = {
  server_id: string
  workspace_id: string
  name: string
  provider: "procurement"
  transport: "STDIO"
  status: "REGISTERED" | "ACTIVE" | "UNHEALTHY" | "DISABLED"
  version: number
  schema_hash: string | null
  capability_count: number
  last_health_at: string | null
  safe_error: string | null
  created_at: string
}
export type CapabilityDescriptor = {
  id: string
  name: string
  version: string
  kind: string
  description: string
  risk_level: string
  side_effect_mode: string
  idempotency_mode: string
  reconciliation_capability: string | null
  required_permissions: string[]
  approval_policy: string
  health_status: string
}
export type ModelConfiguration = {
  config_id: string
  workspace_id: string
  kind: "CHAT" | "EMBEDDING" | "RERANKER"
  provider: "Qwen" | "DeepSeek" | "OpenAICompatible"
  base_url: string
  model_name: string
  external: boolean
  has_secret: boolean
  enabled: boolean
  version: number
  created_at: string
}
export type ModelProbe = {
  config_id: string
  ok: boolean
  model: string
  content: string
  input_tokens: number
  output_tokens: number
}
export type ConsistencyIncident = {
  incident_id: string
  workspace_id: string
  task_id: string | null
  severity: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
  incident_type: string
  detail: Record<string, unknown>
  created_at: string
}

type ApiError = { error?: { message?: string } }

export class ApiClient {
  constructor(private readonly token: string) {}

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const response = await fetch(`/api${path}`, {
      ...init,
      headers: {
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...(this.token ? { Authorization: `Bearer ${this.token}` } : {}),
        ...init?.headers,
      },
    })
    if (!response.ok) {
      const body = (await response.json().catch(() => ({}))) as ApiError
      throw new Error(body.error?.message ?? `请求失败（${response.status}）`)
    }
    if (response.status === 204) return undefined as T
    return (await response.json()) as T
  }

  register(email: string, password: string) {
    return this.request("/auth/register", { method: "POST", body: JSON.stringify({ email, password }) })
  }
  login(email: string, password: string) {
    return this.request<{ access_token: string }>("/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    })
  }
  workspaces() { return this.request<Workspace[]>("/workspaces") }
  createWorkspace(name: string) {
    return this.request<Workspace>("/workspaces", { method: "POST", body: JSON.stringify({ name }) })
  }
  conversations(workspace: string) {
    return this.request<Conversation[]>(`/workspaces/${workspace}/conversations`)
  }
  createConversation(workspace_id: string, title: string) {
    return this.request<Conversation>("/conversations", {
      method: "POST",
      body: JSON.stringify({ conversation_id: crypto.randomUUID(), workspace_id, title }),
    })
  }
  messages(workspace: string, conversation: string) {
    return this.request<Message[]>(`/conversations/${conversation}/messages?workspace_id=${workspace}`)
  }
  postMessage(workspace_id: string, conversation: string, content: string) {
    return this.request<Message>(`/conversations/${conversation}/messages`, {
      method: "POST",
      body: JSON.stringify({ message_id: crypto.randomUUID(), workspace_id, content }),
    })
  }
  async streamChat(
    value: {
      workspace_id: string
      conversation_id: string
      model_config_id: string
      content: string
    },
    onToken: (content: string) => void,
  ) {
    const response = await fetch("/api/chat/stream", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...(this.token ? { Authorization: `Bearer ${this.token}` } : {}),
      },
      body: JSON.stringify({
        ...value,
        user_message_id: crypto.randomUUID(),
        assistant_message_id: crypto.randomUUID(),
      }),
    })
    if (!response.ok) {
      const body = (await response.json().catch(() => ({}))) as ApiError
      throw new Error(body.error?.message ?? `请求失败（${response.status}）`)
    }
    if (!response.body) throw new Error("浏览器不支持流式响应")
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ""
    let completed = false
    while (true) {
      const { done, value: bytes } = await reader.read()
      buffer += decoder.decode(bytes, { stream: !done }).replaceAll("\r\n", "\n")
      const frames = buffer.split("\n\n")
      buffer = frames.pop() ?? ""
      for (const frame of frames) {
        let event = "message"
        const data: string[] = []
        for (const line of frame.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim()
          if (line.startsWith("data:")) data.push(line.slice(5).trim())
        }
        if (!data.length) continue
        const payload = JSON.parse(data.join("\n")) as {
          content?: string
          message?: string
        }
        if (event === "token" && payload.content) onToken(payload.content)
        if (event === "error") throw new Error(payload.message ?? "模型流式请求失败")
        if (event === "done") completed = true
      }
      if (done) break
    }
    if (!completed) throw new Error("模型流未正常完成")
  }
  tasks(workspace: string) { return this.request<Task[]>(`/workspaces/${workspace}/tasks`) }
  plans(workspace: string, task: string) {
    return this.request<Plan[]>(`/workspaces/${workspace}/tasks/${task}/plans`)
  }
  taskSteps(workspace: string, task: string) {
    return this.request<TaskStep[]>(`/workspaces/${workspace}/tasks/${task}/steps`)
  }
  taskAttempts(workspace: string, task: string) {
    return this.request<StepAttempt[]>(`/workspaces/${workspace}/tasks/${task}/attempts`)
  }
  taskEvidence(workspace: string, task: string) {
    return this.request<Evidence[]>(`/workspaces/${workspace}/tasks/${task}/evidence`)
  }
  taskTrace(workspace: string, task: string) {
    return this.request<TaskTrace>(`/tasks/${task}/trace?workspace_id=${workspace}`)
  }
  controlTask(workspace: string, task: Task, action: "pause" | "resume" | "cancel") {
    return this.request<Task>(`/workspaces/${workspace}/tasks/${task.task_id}/${action}`, {
      method: "POST",
      body: JSON.stringify({ expected_version: task.version }),
    })
  }
  approvals(workspace: string) { return this.request<Approval[]>(`/approvals?workspace_id=${workspace}`) }
  decideApproval(item: Approval, decision: "approve" | "reject") {
    return this.request<Approval>(`/approvals/${item.approval_id}/${decision}`, {
      method: "POST",
      body: JSON.stringify({ workspace_id: item.workspace_id, expected_version: item.version }),
    })
  }
  artifacts(workspace: string) { return this.request<Artifact[]>(`/artifacts?workspace_id=${workspace}`) }
  knowledge(workspace: string) {
    return this.request<KnowledgeIngestion[]>(`/knowledge/documents?workspace_id=${workspace}`)
  }
  uploadKnowledge(workspace: string, file: File) {
    const query = new URLSearchParams({
      workspace_id: workspace,
      document_id: crypto.randomUUID(),
      name: file.name,
    })
    return this.request<KnowledgeIngestion>(`/knowledge/documents?${query}`, {
      method: "POST",
      body: file,
      headers: { "Content-Type": file.type || "application/octet-stream" },
    })
  }
  skills(workspace: string) {
    return this.request<SkillInstallation[]>(`/skills?workspace_id=${workspace}`)
  }
  quarantineSkill(workspace: string, name: string, version: string, file: File) {
    const query = new URLSearchParams({ workspace_id: workspace, name, version })
    return this.request<SkillInstallation>(`/skills/quarantine?${query}`, {
      method: "POST",
      body: file,
      headers: { "Content-Type": "application/zip" },
    })
  }
  skillAction(item: SkillInstallation, action: "install" | "enable" | "disable") {
    const path = action === "install" ? "/skills/install" : `/skills/${item.installation_id}/${action}`
    return this.request<SkillInstallation>(path, {
      method: "POST",
      body: JSON.stringify({
        workspace_id: item.workspace_id,
        installation_id: action === "install" ? item.installation_id : undefined,
        expected_version: item.version,
      }),
    })
  }
  mcpServers(workspace: string) {
    return this.request<MCPServer[]>(`/mcp?workspace_id=${workspace}`)
  }
  registerMcp(workspace_id: string) {
    return this.request<MCPServer>("/mcp", {
      method: "POST",
      body: JSON.stringify({ workspace_id, name: "Procurement", provider: "procurement" }),
    })
  }
  mcpAction(item: MCPServer, action: "connect" | "health" | "disable") {
    return this.request<MCPServer>(`/mcp/${item.server_id}/${action}`, {
      method: "POST",
      body: JSON.stringify({
        workspace_id: item.workspace_id,
        expected_version: item.version,
      }),
    })
  }
  capabilities(workspace: string) {
    return this.request<CapabilityDescriptor[]>(`/capabilities?workspace_id=${workspace}`)
  }
  models(workspace: string) {
    return this.request<ModelConfiguration[]>(`/models?workspace_id=${workspace}`)
  }
  createModel(value: {
    workspace_id: string
    kind: ModelConfiguration["kind"]
    provider: ModelConfiguration["provider"]
    base_url: string
    model_name: string
    external: boolean
    api_key: string
  }) {
    return this.request<ModelConfiguration>("/models", {
      method: "POST",
      body: JSON.stringify({ config_id: crypto.randomUUID(), ...value }),
    })
  }
  probeModel(workspace: string, config: string) {
    return this.request<ModelProbe>(
      `/models/${config}/probe?workspace_id=${workspace}`,
      { method: "POST" },
    )
  }
  audit(workspace: string) { return this.request<AuditEvent[]>(`/audit?workspace_id=${workspace}`) }
  incidents(workspace: string) {
    return this.request<ConsistencyIncident[]>(
      `/consistency/incidents?workspace_id=${workspace}`,
    )
  }
  artifactDownloadUrl(workspace: string, artifact: string) {
    return `/api/artifacts/${artifact}/download?workspace_id=${workspace}`
  }
}
