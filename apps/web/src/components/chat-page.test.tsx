import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, expect, test, vi } from "vitest"

import {
  ApiClient,
  type BoundedResearchPlan,
  type Message,
  type ModelConfiguration,
  type SkillInstallation,
} from "../api"
import {
  buildSearchQuery,
  chatRequestFailureMessage,
  ChatContent,
  ChatPage,
  hasPartiallyAnsweredTurn,
  latestEnabledSkill,
  normalizeChatContent,
  orderTurnAwareMessages,
  relativeDateQualifier,
  resolveWebFollowUp,
  resolveWebQuestion,
  turnStatusLabel,
  webTaskFailureMessage,
  webSearchLimit,
} from "./chat-page"

afterEach(cleanup)

test("keeps a late T1 answer grouped with T1 and exposes user-facing Turn status", () => {
  const common = {
    workspace_id: "22222222-2222-4222-8222-222222222222",
    conversation_id: "33333333-3333-4333-8333-333333333333",
  }
  const userOne: Message = {
    ...common,
    message_id: "10000000-0000-4000-8000-000000000001",
    role: "USER",
    content: "Question one",
    created_at: "2026-09-14T10:00:00Z",
    turn_id: "20000000-0000-4000-8000-000000000001",
    turn_state: "COMPLETED",
    turn_outcome_status: "ANSWERED",
  }
  const userTwo: Message = {
    ...common,
    message_id: "10000000-0000-4000-8000-000000000002",
    role: "USER",
    content: "Question two",
    created_at: "2026-09-14T10:01:00Z",
    turn_id: "20000000-0000-4000-8000-000000000002",
    turn_state: "RUNNING",
    turn_outcome_status: null,
  }
  const answerTwo: Message = {
    ...common,
    message_id: "30000000-0000-4000-8000-000000000002",
    role: "ASSISTANT",
    content: "Answer two completed first",
    created_at: "2026-09-14T10:02:00Z",
    turn_id: userTwo.turn_id,
    turn_state: "COMPLETED",
    turn_outcome_status: "ANSWERED",
  }
  const answerOne: Message = {
    ...common,
    message_id: "30000000-0000-4000-8000-000000000001",
    role: "ASSISTANT",
    content: "Answer one completed late",
    created_at: "2026-09-14T10:03:00Z",
    turn_id: userOne.turn_id,
    turn_state: "COMPLETED",
    turn_outcome_status: "ANSWERED",
  }

  expect(
    orderTurnAwareMessages([userOne, userTwo, answerTwo, answerOne]).map(
      (item) => item.message_id,
    ),
  ).toEqual([
    userOne.message_id,
    answerOne.message_id,
    userTwo.message_id,
    answerTwo.message_id,
  ])
  expect(turnStatusLabel(userTwo)).toBe("处理中")
  expect(
    turnStatusLabel({
      ...userOne,
      turn_outcome_status: "UNAVAILABLE",
    }),
  ).toBe("暂不可用")
  const partial = {
    ...userOne,
    turn_state: "FAILED" as const,
    turn_outcome_status: "PARTIALLY_ANSWERED" as const,
  }
  expect(turnStatusLabel(partial)).toBe("已部分回答")
  expect(hasPartiallyAnsweredTurn([partial], partial.message_id)).toBe(true)
})

test("always selects the newest enabled built-in Skill version", () => {
  const base = {
    installation_id: "11111111-1111-4111-8111-111111111111",
    workspace_id: "22222222-2222-4222-8222-222222222222",
    skill_id: null,
    skill_version_id: null,
    name: "web-research",
    archive_sha256: "a".repeat(64),
    state: "ENABLED" as const,
    version: 1,
    permission_diff: [],
    safe_error: null,
  }
  const selected = latestEnabledSkill(
    [
      { ...base, skill_version: "1.0.1" },
      { ...base, installation_id: "33333333-3333-4333-8333-333333333333", skill_version: "1.0.0" },
      { ...base, installation_id: "44444444-4444-4444-8444-444444444444", skill_version: "1.0.10" },
    ],
    "web-research",
  )

  expect(selected?.skill_version).toBe("1.0.10")
})

test("builds a concise dated query for current-information questions", () => {
  expect(
    buildSearchQuery(
      "请联网查询广州今天的天气，并用中文简要说明温度、天气状况和出行建议，列出可核验来源。",
      new Date(2026, 8, 11),
    ),
  ).toBe("广州 天气 气温 2026-09-11")
  expect(relativeDateQualifier("明天", new Date(2026, 8, 11))).toBe("2026-09-12")
  expect(relativeDateQualifier("上周五", new Date(2026, 8, 11))).toBe("2026-09-04")
  expect(relativeDateQualifier("未来一周", new Date(2026, 8, 11))).toBe(
    "2026-09-11 至 2026-09-17",
  )
  expect(relativeDateQualifier("最新动态", new Date(2026, 8, 11))).toBe(
    "2026-08-13 至 2026-09-11",
  )
})

test("keeps query helpers separate from the backend routing authority", () => {
  expect(buildSearchQuery("广州天气")).toBe("广州 天气 气温 降水")
  expect(webSearchLimit("今天广州天气怎么样")).toBe(3)
  expect(webSearchLimit("今天有什么新闻")).toBe(5)
  expect(webSearchLimit("查询当前 Python 版本")).toBe(4)
})

test("resolves a web follow-up from recent concrete user context", () => {
  expect(
    resolveWebFollowUp("他这几天有什么最新动态？", [
      { role: "USER", content: "特朗普是谁？" },
      { role: "ASSISTANT", content: "唐纳德·特朗普是美国政治人物。" },
      { role: "USER", content: "他这几天有什么最新动态？" },
    ]),
  ).toBe("关于特朗普：这几天有什么最新动态？")
  expect(
    resolveWebFollowUp("那上周五的天气怎么样呢？", [
      { role: "USER", content: "上周五东莞的天气怎么样？" },
    ]),
  ).toBe("关于上周五东莞的天气：上周五的天气怎么样呢？")
  expect(
    resolveWebFollowUp("那价格呢？", [
      { role: "USER", content: "iPhone 17 Pro 最近发布了吗？" },
    ]),
  ).toBe("关于iPhone 17 Pro 最近发布了吗：价格呢？")
  expect(resolveWebQuestion("他最近怎么样？", [])).toEqual({
    instruction: "他最近怎么样？",
    contextRequired: true,
    contextResolved: false,
  })
  expect(resolveWebQuestion("那未来一周东莞天气呢？", []).contextResolved).toBe(true)
  expect(
    resolveWebFollowUp("那上周五的天气呢？", [
      { role: "USER", content: "东莞明天的天气怎么样？" },
      { role: "USER", content: "我刚刚问了哪里的天气？" },
    ]),
  ).toBe("关于东莞明天的天气：上周五的天气呢？")
  expect(resolveWebFollowUp("今天有什么国际新闻", [])).toBe("今天有什么国际新闻")
})

const model: ModelConfiguration = {
  config_id: "11111111-1111-4111-8111-111111111111",
  workspace_id: "22222222-2222-4222-8222-222222222222",
  kind: "CHAT",
  provider: "OpenAICompatible",
  base_url: "http://host.docker.internal:8000/v1",
  model_name: "Qwen/Test",
  external: false,
  has_secret: false,
  enabled: true,
  superseded: false,
  version: 1,
  created_at: "2026-01-01T00:00:00Z",
}

const fallbackModel: ModelConfiguration = {
  ...model,
  config_id: "12121212-1212-4121-8121-121212121212",
  provider: "DeepSeek",
  base_url: "https://api.deepseek.example/v1",
  model_name: "DeepSeek/Backup",
  external: true,
}

test("keeps configured models selectable without deriving an automatic fallback", () => {
  const duplicatePrimary: ModelConfiguration = {
    ...model,
    config_id: "13131313-1313-4131-8131-131313131313",
    provider: "DeepSeek",
    base_url: "HTTP://host.docker.internal:8000/v1/",
  }

  expect([fallbackModel, duplicatePrimary, model].map((item) => item.config_id)).toEqual([
    fallbackModel.config_id,
    duplicatePrimary.config_id,
    model.config_id,
  ])
})

const skill: SkillInstallation = {
  installation_id: "33333333-3333-4333-8333-333333333333",
  workspace_id: model.workspace_id,
  skill_id: "44444444-4444-4444-8444-444444444444",
  skill_version_id: "55555555-5555-4555-8555-555555555555",
  name: "supplier-risk",
  skill_version: "1.0.0",
  archive_sha256: "a".repeat(64),
  state: "ENABLED",
  version: 1,
  permission_diff: [],
  safe_error: null,
}
const webSkill: SkillInstallation = {
  ...skill,
  installation_id: "66666666-6666-4666-8666-666666666666",
  skill_id: "77777777-7777-4777-8777-777777777777",
  skill_version_id: "88888888-8888-4888-8888-888888888888",
  name: "web-research",
}
const webResearchPlan: BoundedResearchPlan = {
  web_queries: ["今天 新闻"],
  retrieval_queries: [],
  source_plan: {
    required_sources: ["PUBLIC_WEB"],
    preferred_sources: ["OFFICIAL_OR_FIRST_PARTY", "INDEPENDENT_MEDIA"],
    fallback_sources: ["GENERAL_WEB"],
    forbidden_sources: ["PARAMETRIC_KNOWLEDGE"],
  },
  result_limit: 5,
  answer_contract: {
    answer_type: "LIST",
    required_aspects: ["distinct current events", "supported dates"],
    completeness: "COMPLETE",
    freshness: "CURRENT",
    minimum_answer_items: 2,
    maximum_answer_items: 5,
    require_explicit_dates: true,
    minimum_evidence_items: 2,
    minimum_independent_sources: 2,
    partial_answer_policy: "ALLOW_SUPPORTED_ONLY",
    organization: "BULLETS",
  },
}


test("keeps web routing automatic and supports attachment and @ skill selection", () => {
  render(
    <ChatPage
      api={new ApiClient("")}
      workspace={model.workspace_id}
      conversation=""
      setConversation={vi.fn()}
      messages={[]}
      setMessages={vi.fn()}
      models={[model]}
      skills={[skill, webSkill]}
      knowledge={[]}
      onRefresh={async () => undefined}
      onOpenModels={vi.fn()}
      onOpenTasks={vi.fn()}
      onOpenSkills={vi.fn()}
      setNotice={vi.fn()}
    />,
  )

  expect(screen.getByRole("button", { name: /Qwen\/Test/ })).toBeInTheDocument()
  expect(screen.queryByText("自动联网")).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: /^联网搜索/ })).not.toBeInTheDocument()

  fireEvent.click(screen.getByRole("button", { name: "添加" }))
  expect(screen.getByRole("button", { name: /添加附件/ })).toBeInTheDocument()
  expect(screen.queryByRole("button", { name: /^联网搜索/ })).not.toBeInTheDocument()

  fireEvent.keyDown(document, { key: "Escape" })
  expect(screen.queryByRole("button", { name: /添加附件/ })).not.toBeInTheDocument()

  fireEvent.change(screen.getByLabelText("消息"), { target: { value: "请分析 @sup" } })
  fireEvent.click(screen.getAllByRole("button", { name: /supplier-risk v1\.0\.0/ })[0])
  expect(screen.getAllByText("supplier-risk").length).toBeGreaterThan(0)
  expect(screen.getByPlaceholderText("输入供应商 UUID 和分析目标…")).toBeInTheDocument()
})
test("keeps automatic web routing hidden when no user-selectable Skill is enabled", () => {
  const onOpenSkills = vi.fn()
  render(
    <ChatPage
      api={new ApiClient("")}
      workspace={model.workspace_id}
      conversation=""
      setConversation={vi.fn()}
      messages={[]}
      setMessages={vi.fn()}
      models={[model]}
      skills={[]}
      knowledge={[]}
      onRefresh={async () => undefined}
      onOpenModels={vi.fn()}
      onOpenTasks={vi.fn()}
      onOpenSkills={onOpenSkills}
      setNotice={vi.fn()}
    />,
  )

  fireEvent.click(screen.getByRole("button", { name: "添加" }))
  expect(screen.queryByRole("button", { name: /^联网搜索/ })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole("button", { name: /管理 Skills/ }))
  expect(screen.queryByRole("button", { name: /添加附件/ })).not.toBeInTheDocument()
  expect(onOpenSkills).toHaveBeenCalledTimes(1)
})

test("uses one selected model and one Turn id for planning and direct execution", async () => {
  const directApi = new ApiClient("")
  const route = vi.spyOn(directApi, "routeChat").mockResolvedValue({
    route: "DIRECT",
    resolved_content: "你好",
    search_query: null,
    search_queries: [],
    search_limit: 0,
    reason: "DIRECT_REQUEST",
    confidence: 1,
    context_required: false,
    context_resolved: true,
    research_plan: null,
    agent_decision: null,
  })
  const stream = vi.spyOn(directApi, "streamChat").mockResolvedValue(undefined)
  vi.spyOn(directApi, "messages").mockResolvedValue([])
  render(
    <ChatPage
      api={directApi}
      workspace={model.workspace_id}
      conversation="aaaaaaaa-1111-4111-8111-111111111111"
      setConversation={vi.fn()}
      messages={[]}
      setMessages={vi.fn()}
      models={[fallbackModel, model]}
      skills={[]}
      knowledge={[]}
      onRefresh={async () => undefined}
      onOpenModels={vi.fn()}
      onOpenTasks={vi.fn()}
      onOpenSkills={vi.fn()}
      setNotice={vi.fn()}
    />,
  )

  fireEvent.change(screen.getByLabelText("消息"), { target: { value: "你好" } })
  fireEvent.click(screen.getByRole("button", { name: "发送" }))

  await waitFor(() => expect(stream).toHaveBeenCalledTimes(1))
  expect(stream.mock.calls[0][0]).toMatchObject({
    model_config_id: model.config_id,
  })
  expect(stream.mock.calls[0][0]).not.toHaveProperty("fallback_model_config_id")
  expect(route.mock.calls[0][0].user_message_id).toBe(
    stream.mock.calls[0][0].user_message_id,
  )
})

test("does not expose automatic or secondary fallback model controls", async () => {
  const directApi = new ApiClient("")
  vi.spyOn(directApi, "routeChat").mockResolvedValue({
    route: "DIRECT",
    resolved_content: "你好",
    search_query: null,
    search_queries: [],
    search_limit: 0,
    reason: "DIRECT_REQUEST",
    confidence: 1,
    context_required: false,
    context_resolved: true,
    research_plan: null,
    agent_decision: null,
  })
  vi.spyOn(directApi, "streamChat").mockResolvedValue(undefined)
  vi.spyOn(directApi, "messages").mockResolvedValue([])
  render(
    <ChatPage
      api={directApi}
      workspace={model.workspace_id}
      conversation="aaaaaaaa-1111-4111-8111-111111111111"
      setConversation={vi.fn()}
      messages={[]}
      setMessages={vi.fn()}
      models={[fallbackModel, model]}
      skills={[]}
      knowledge={[]}
      onRefresh={async () => undefined}
      onOpenModels={vi.fn()}
      onOpenTasks={vi.fn()}
      onOpenSkills={vi.fn()}
      setNotice={vi.fn()}
    />,
  )

  fireEvent.click(screen.getByRole("button", { name: /Qwen\/Test/ }))
  expect(screen.queryByText("备用模型（默认开启）")).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "不使用备用模型" })).not.toBeInTheDocument()
  expect(
    screen.getByRole("button", { name: "选择回答模型 DeepSeek/Backup" }),
  ).toBeInTheDocument()
})

test("renders governed source markdown as a safe clickable link", () => {
  render(<ChatContent content={"参考来源：[weather.example](https://weather.example/today)"} />)

  expect(screen.getByRole("link", { name: "weather.example" })).toHaveAttribute(
    "href",
    "https://weather.example/today",
  )
})

test("renders a turn-scoped attachment before the user message and opens its preview", async () => {
  const previewApi = new ApiClient("")
  const preview = vi.spyOn(previewApi, "knowledgePreview").mockResolvedValue({
    ingestion_id: "abababab-abab-4bab-8bab-abababababab",
    document_version_id: "cdcdcdcd-cdcd-4dcd-8dcd-cdcdcdcdcdcd",
    name: "制度说明.docx",
    media_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    content: "这是附件的安全文本预览。",
    truncated: false,
  })
  const attachedMessage: Message = {
    message_id: "efefefef-efef-4fef-8fef-efefefefefef",
    workspace_id: model.workspace_id,
    conversation_id: "aaaaaaaa-1111-4111-8111-111111111111",
    role: "USER",
    content: "请总结这份文档",
    created_at: "2026-09-16T00:00:00Z",
    attachment: {
      ingestion_id: "abababab-abab-4bab-8bab-abababababab",
      document_version_id: "cdcdcdcd-cdcd-4dcd-8dcd-cdcdcdcdcdcd",
      name: "制度说明.docx",
      media_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    },
  }

  render(
    <ChatPage
      api={previewApi}
      workspace={model.workspace_id}
      conversation={attachedMessage.conversation_id}
      setConversation={vi.fn()}
      messages={[attachedMessage]}
      setMessages={vi.fn()}
      models={[model]}
      skills={[]}
      knowledge={[]}
      onRefresh={async () => undefined}
      onOpenModels={vi.fn()}
      onOpenTasks={vi.fn()}
      onOpenSkills={vi.fn()}
      setNotice={vi.fn()}
    />,
  )

  const attachmentCard = screen.getByRole("button", { name: /制度说明\.docx/ })
  expect(attachmentCard.parentElement?.firstElementChild).toBe(attachmentCard)
  expect(screen.getByText("请总结这份文档")).toBeInTheDocument()
  fireEvent.click(attachmentCard)
  await waitFor(() => expect(preview).toHaveBeenCalledWith(model.workspace_id, attachedMessage.attachment?.ingestion_id))
  expect(await screen.findByRole("dialog", { name: "附件预览" })).toHaveTextContent(
    "这是附件的安全文本预览。",
  )
})

test("keeps the composer focused and editable while the previous answer is running", async () => {
  const directApi = new ApiClient("")
  let resolveRoute: ((value: Awaited<ReturnType<ApiClient["routeChat"]>>) => void) | undefined
  const routePending = new Promise<Awaited<ReturnType<ApiClient["routeChat"]>>>((resolve) => {
    resolveRoute = resolve
  })
  vi.spyOn(directApi, "routeChat").mockReturnValue(routePending)
  vi.spyOn(directApi, "streamChat").mockResolvedValue(undefined)
  vi.spyOn(directApi, "messages").mockResolvedValue([])

  render(
    <ChatPage
      api={directApi}
      workspace={model.workspace_id}
      conversation="aaaaaaaa-1111-4111-8111-111111111111"
      setConversation={vi.fn()}
      messages={[]}
      setMessages={vi.fn()}
      models={[model]}
      skills={[]}
      knowledge={[]}
      onRefresh={async () => undefined}
      onOpenModels={vi.fn()}
      onOpenTasks={vi.fn()}
      onOpenSkills={vi.fn()}
      setNotice={vi.fn()}
    />,
  )

  const composer = screen.getByLabelText("消息")
  fireEvent.change(composer, { target: { value: "第一个问题" } })
  fireEvent.click(screen.getByRole("button", { name: "发送" }))
  await waitFor(() => expect(directApi.routeChat).toHaveBeenCalledTimes(1))
  expect(composer).toHaveFocus()
  expect(composer).not.toBeDisabled()
  fireEvent.change(composer, { target: { value: "下一条追问草稿" } })
  expect(composer).toHaveValue("下一条追问草稿")

  resolveRoute?.({
    route: "DIRECT",
    resolved_content: "第一个问题",
    search_query: null,
    search_queries: [],
    search_limit: 0,
    reason: "DIRECT_REQUEST",
    confidence: 1,
    context_required: false,
    context_resolved: true,
    research_plan: null,
    agent_decision: null,
  })
  await waitFor(() => expect(directApi.streamChat).toHaveBeenCalledTimes(1))
  expect(composer).toHaveValue("下一条追问草稿")
})

test("normalizes time ranges and query intent across changing-information domains", () => {
  const now = new Date(2026, 8, 11)
  expect(relativeDateQualifier("过去一周", now)).toBe(
    "2026-09-05 至 2026-09-11",
  )
  expect(relativeDateQualifier("上个月", now)).toBe(
    "2026-08-01 至 2026-08-31",
  )
  expect(buildSearchQuery("上周五东莞的天气怎么样", now)).toBe(
    "东莞 天气 历史天气 2026-09-04",
  )
  expect(buildSearchQuery("请查一下 OpenAI 最近的发布公告", now)).toBe(
    "OpenAI 发布 公告 官方信息 2026-08-13 至 2026-09-11",
  )
  expect(buildSearchQuery("美元人民币现在的汇率是多少", now)).toBe(
    "美元人民币 汇率 最新数据 2026-09-11",
  )
  expect(resolveWebQuestion("未来一周天气怎么样", [])).toEqual({
    instruction: "未来一周天气怎么样",
    contextRequired: true,
    contextResolved: false,
  })
  expect(resolveWebQuestion("价格是多少", []).contextResolved).toBe(false)
  expect(resolveWebQuestion("今天有什么国际新闻", []).contextResolved).toBe(true)
})

test("preserves the requested metric and time grain in search queries", () => {
  const now = new Date(2026, 8, 12, 8)

  expect(buildSearchQuery("2024年广东的年降雨量是多少", now)).toBe(
    "2024年广东 年降雨量 年平均降水量 气候公报",
  )
  expect(buildSearchQuery("未来一周广州黄埔的天气怎么样呢", now)).toBe(
    "广州黄埔 天气 天气预报 逐日预报 2026-09-12 至 2026-09-18",
  )
  expect(buildSearchQuery("昨天北京的降水和风力如何", now)).toBe(
    "北京 降水 风力 历史天气 降水量 2026-09-11",
  )
  expect(
    buildSearchQuery(
      "2024年广东省年平均降水量是多少？如果水资源公报和气象统计口径不同，请分别说明。",
      now,
    ),
  ).toBe("2024年广东省 年平均降水量 水资源公报 气象 气候公报")
})

test("does not run routing while the user is still composing", () => {
  const routeApi = new ApiClient("")
  const route = vi.spyOn(routeApi, "routeChat")
  render(
    <ChatPage
      api={routeApi}
      workspace={model.workspace_id}
      conversation="aaaaaaaa-1111-4111-8111-111111111111"
      setConversation={vi.fn()}
      messages={[]}
      setMessages={vi.fn()}
      models={[model]}
      skills={[]}
      knowledge={[]}
      onRefresh={async () => undefined}
      onOpenModels={vi.fn()}
      onOpenTasks={vi.fn()}
      onOpenSkills={vi.fn()}
      setNotice={vi.fn()}
    />,
  )
  fireEvent.change(screen.getByLabelText("消息"), {
    target: { value: "OpenAI 最近有什么最新动态" },
  })
  expect(route).not.toHaveBeenCalled()
})

test("normalizes accidental raw markdown without changing source links", () => {
  expect(normalizeChatContent("**重点**：使用 `实时结果`。")).toBe(
    "重点：使用 实时结果。",
  )
})

test("explains governed web failures without pretending that search succeeded", () => {
  expect(webTaskFailureMessage("CAPABILITY_UNAVAILABLE")).toContain("上游限流")
  expect(webTaskFailureMessage("LLM_TIMEOUT")).toContain("回答模型")
  expect(webTaskFailureMessage("LLM_SCHEMA_ERROR")).toContain("证据校验")
  expect(webTaskFailureMessage(undefined)).toContain("真实失败记录")
})

test("does not expose internal planner schema errors to chat users", () => {
  expect(
    chatRequestFailureMessage(
      new Error("structured output invalid after 3 attempt(s): response was not valid schema JSON"),
    ),
  ).toBe("请求规划结果格式不完整，系统未执行不确定的操作；请重试本次请求")
})

test("keeps durable Skills out of chat while materializing web research exactly once", async () => {
  const supplierApi = new ApiClient("")
  const supplierPostMessage = vi.spyOn(supplierApi, "postMessage")
  const supplierTaskResult = vi.spyOn(supplierApi, "taskResultMessage")
  const supplierCreateTask = vi.spyOn(supplierApi, "createTask").mockResolvedValue({
    task_id: "99999999-9999-4999-8999-999999999999",
    workspace_id: model.workspace_id,
    state: "PLANNING",
    version: 1,
    execution_snapshot_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    goal: { goal: "supplier", constraints: [], deliverables: [] },
  })
  vi.spyOn(supplierApi, "routeChat").mockResolvedValue({
    route: "DIRECT",
    resolved_content: "supplier analysis",
    search_query: null,
    search_queries: [],
    search_limit: 3,
    reason: "DIRECT_REQUEST",
    confidence: 1,
    context_required: false,
    context_resolved: true,
    research_plan: null,
    agent_decision: null,
  })
  const openTasks = vi.fn()
  const supplierView = render(
    <ChatPage
      api={supplierApi}
      workspace={model.workspace_id}
      conversation="aaaaaaaa-1111-4111-8111-111111111111"
      setConversation={vi.fn()}
      messages={[]}
      setMessages={vi.fn()}
      models={[model]}
      skills={[skill, webSkill]}
      knowledge={[]}
      onRefresh={async () => undefined}
      onOpenModels={vi.fn()}
      onOpenTasks={openTasks}
      onOpenSkills={vi.fn()}
      setNotice={vi.fn()}
    />,
  )
  fireEvent.change(screen.getByLabelText("消息"), { target: { value: "@sup" } })
  fireEvent.click(screen.getByRole("button", { name: /supplier-risk v1\.0\.0/ }))
  fireEvent.change(screen.getByLabelText("消息"), {
    target: {
      value: "分析供应商 bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    },
  })
  fireEvent.click(screen.getByRole("button", { name: "发送" }))
  await waitFor(() => expect(supplierCreateTask).toHaveBeenCalledTimes(1))
  expect(supplierCreateTask.mock.calls[0][0].requested_skill).toBe("supplier-risk")
  expect(supplierPostMessage).not.toHaveBeenCalled()
  expect(supplierTaskResult).not.toHaveBeenCalled()
  expect(openTasks).toHaveBeenCalledTimes(1)
  supplierView.unmount()

  const webApi = new ApiClient("")
  const postedMessage = {
    message_id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    workspace_id: model.workspace_id,
    conversation_id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
    role: "USER" as const,
    content: "今天有什么新闻",
    created_at: "2026-09-14T00:00:00Z",
  }
  const webPostMessage = vi.spyOn(webApi, "postMessage")
  const webTaskResult = vi.spyOn(webApi, "taskResultMessage").mockResolvedValue({
    ...postedMessage,
    message_id: "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee",
    role: "ASSISTANT",
    content: "今日新闻摘要",
  })
  const webTask = {
    task_id: "ffffffff-ffff-4fff-8fff-ffffffffffff",
    workspace_id: model.workspace_id,
    state: "PLANNING",
    version: 1,
    execution_snapshot_id: "12121212-1212-4121-8121-121212121212",
    goal: { goal: "news", constraints: [], deliverables: [] },
  }
  const webCreateTask = vi.spyOn(webApi, "createTask")
  const webCreateResearchTurn = vi
    .spyOn(webApi, "createResearchTurn")
    .mockResolvedValue({ user_message: postedMessage, task: webTask })
  vi.spyOn(webApi, "routeChat").mockResolvedValue({
    route: "WEB",
    resolved_content: "今天有什么新闻",
    search_query: "今天 新闻",
    search_queries: ["今天 新闻"],
    search_limit: 5,
    reason: "CURRENT_INFORMATION",
    confidence: 1,
    context_required: false,
    context_resolved: true,
    research_plan: webResearchPlan,
    agent_decision: null,
  })
  vi.spyOn(webApi, "task").mockResolvedValue({
    task_id: "ffffffff-ffff-4fff-8fff-ffffffffffff",
    workspace_id: model.workspace_id,
    state: "COMPLETED",
    version: 2,
    execution_snapshot_id: "12121212-1212-4121-8121-121212121212",
    goal: { goal: "news", constraints: [], deliverables: [] },
  })
  vi.spyOn(webApi, "taskSteps").mockResolvedValue([])
  const webTaskCompletion = vi.spyOn(webApi, "taskCompletion").mockResolvedValue({
    workspace_id: model.workspace_id,
    task_id: webTask.task_id,
    execution_status: "COMPLETED",
    completion_status: "COMPLETE",
    delivery_decision: {
      id: "abababab-abab-4aba-8aba-abababababab",
      workspace_id: model.workspace_id,
      task_id: webTask.task_id,
      completion_snapshot_id: webTask.execution_snapshot_id,
      decision: "FULL_ANSWER",
      included_claim_ids: [],
      excluded_claim_ids: [],
      unresolved_requirement_ids: [],
      reason_codes: [],
      disclosure: "",
      policy_version: "1",
      verification_ref: null,
      created_at: "2026-09-14T00:00:00Z",
      supersedes_id: null,
    },
    unresolved_requirement_ids: [],
    disclosure: "",
  })
  vi.spyOn(webApi, "messages").mockResolvedValue([postedMessage])
  render(
    <ChatPage
      api={webApi}
      workspace={model.workspace_id}
      conversation="dddddddd-dddd-4ddd-8ddd-dddddddddddd"
      setConversation={vi.fn()}
      messages={[]}
      setMessages={vi.fn()}
      models={[fallbackModel, model]}
      skills={[webSkill]}
      knowledge={[]}
      onRefresh={async () => undefined}
      onOpenModels={vi.fn()}
      onOpenTasks={vi.fn()}
      onOpenSkills={vi.fn()}
      setNotice={vi.fn()}
    />,
  )
  fireEvent.change(screen.getByLabelText("消息"), {
    target: { value: "今天有什么新闻" },
  })
  fireEvent.click(screen.getByRole("button", { name: "发送" }))
  await waitFor(() => expect(webTaskResult).toHaveBeenCalledTimes(1))
  expect(webTaskCompletion).toHaveBeenCalledTimes(1)
  expect(webTaskResult.mock.invocationCallOrder[0]).toBeGreaterThan(webTaskCompletion.mock.invocationCallOrder[0])
  expect(webCreateResearchTurn).toHaveBeenCalledTimes(1)
  expect(webCreateResearchTurn.mock.calls[0][0].goal.time_budget_sec).toBe(180)
  expect(webCreateResearchTurn.mock.calls[0][0].goal.token_budget).toBe(120000)
  expect(webCreateResearchTurn.mock.calls[0][0].goal.requested_skill).toBe(
    "web-research",
  )
  expect(
    JSON.parse(webCreateResearchTurn.mock.calls[0][0].goal.user_context),
  ).toMatchObject({
    model_config_id: model.config_id,
    research_plan: webResearchPlan,
  })
  expect(
    JSON.parse(webCreateResearchTurn.mock.calls[0][0].goal.user_context),
  ).not.toHaveProperty("fallback_model_config_id")
  expect(webCreateResearchTurn.mock.calls[0][0].goal.user_context).not.toContain(
    "conversation_id",
  )
  expect(webCreateResearchTurn.mock.calls[0][0].goal.user_context).not.toContain(
    "user_message_id",
  )
  expect(webPostMessage).not.toHaveBeenCalled()
  expect(webCreateTask).not.toHaveBeenCalled()
})
