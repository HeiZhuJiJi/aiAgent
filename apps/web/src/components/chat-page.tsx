import {
  AtSign,
  Bot,
  Check,
  ChevronDown,
  FileText,
  Paperclip,
  Plus,
  Send,
  Sparkles,
  X,
} from "lucide-react"
import { type FormEvent, type ReactNode, useEffect, useMemo, useRef, useState } from "react"

import {
  ApiClient,
  type KnowledgeIngestion,
  type KnowledgeDocumentPreview,
  type Message,
  type ModelConfiguration,
  type SkillInstallation,
} from "../api"
import { Badge } from "./ui/badge"
import { Button } from "./ui/button"

type Props = {
  api: ApiClient
  workspace: string
  conversation: string
  setConversation: (value: string) => void
  messages: Message[]
  setMessages: (value: Message[]) => void
  models: ModelConfiguration[]
  skills: SkillInstallation[]
  knowledge: KnowledgeIngestion[]
  onRefresh: () => Promise<void>
  onOpenModels: () => void
  onOpenTasks: () => void
  onOpenSkills: () => void
  setNotice: (value: string) => void
}

export function orderTurnAwareMessages(messages: Message[]): Message[] {
  const turnAnchors = new Map<string, string>()
  for (const item of messages) {
    if (!item.turn_id) continue
    const current = turnAnchors.get(item.turn_id)
    if (
      current === undefined
      || item.role === "USER"
      || item.created_at.localeCompare(current) < 0
    ) {
      turnAnchors.set(item.turn_id, item.created_at)
    }
  }
  return messages
    .map((item, index) => ({ item, index }))
    .sort((left, right) => {
      const leftAnchor = left.item.turn_id
        ? turnAnchors.get(left.item.turn_id) ?? left.item.created_at
        : left.item.created_at
      const rightAnchor = right.item.turn_id
        ? turnAnchors.get(right.item.turn_id) ?? right.item.created_at
        : right.item.created_at
      const anchorOrder = leftAnchor.localeCompare(rightAnchor)
      if (anchorOrder !== 0) return anchorOrder
      const leftGroup = left.item.turn_id ?? `message:${left.index}`
      const rightGroup = right.item.turn_id ?? `message:${right.index}`
      const groupOrder = leftGroup.localeCompare(rightGroup)
      if (groupOrder !== 0) return groupOrder
      const leftPhase = left.item.role === "USER" ? 0 : left.item.role === "ASSISTANT" ? 1 : 0
      const rightPhase = right.item.role === "USER" ? 0 : right.item.role === "ASSISTANT" ? 1 : 0
      return leftPhase - rightPhase || left.index - right.index
    })
    .map(({ item }) => item)
}

export function turnStatusLabel(message: Message): string | null {
  if (message.role !== "USER" || !message.turn_state) return null
  if (message.turn_state === "PENDING") return "等待处理"
  if (message.turn_state === "RUNNING") return "处理中"
  if (message.turn_state === "CANCELLED") return "已取消"
  if (message.turn_state === "SUPERSEDED") return "已由新请求替代"
  if (message.turn_outcome_status === "PARTIALLY_ANSWERED") return "已部分回答"
  if (message.turn_state === "FAILED" || message.turn_outcome_status === "FAILED") {
    return "处理失败"
  }
  if (message.turn_outcome_status === "UNAVAILABLE") return "暂不可用"
  if (message.turn_outcome_status === "CLARIFICATION_REQUIRED") return "等待补充"
  return null
}

export function hasPartiallyAnsweredTurn(messages: Message[], userMessageId: string): boolean {
  return messages.some(
    (item) =>
      item.message_id === userMessageId
      && item.role === "USER"
      && item.turn_outcome_status === "PARTIALLY_ANSWERED",
  )
}

export function normalizeChatContent(content: string): string {
  return content
    .replace(/^#{1,6}\s+/gm, "")
    .replace(/\*\*([^*\n]+)\*\*/g, "$1")
    .replace(/__([^_\n]+)__/g, "$1")
    .replace(/`([^`\n]+)`/g, "$1")
}

export function ChatContent({ content }: { content: string }) {
  const normalized = normalizeChatContent(content)
  const parts: ReactNode[] = []
  const links = /\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/g
  let offset = 0
  for (const match of normalized.matchAll(links)) {
    const index = match.index ?? 0
    parts.push(normalized.slice(offset, index))
    parts.push(
      <a key={`${index}:${match[2]}`} href={match[2]} target="_blank" rel="noreferrer">
        {match[1]}
      </a>,
    )
    offset = index + match[0].length
  }
  parts.push(normalized.slice(offset))
  return <>{parts}</>
}

function formatLocalDate(value: Date): string {
  const year = value.getFullYear()
  const month = String(value.getMonth() + 1).padStart(2, "0")
  const day = String(value.getDate()).padStart(2, "0")
  return `${year}-${month}-${day}`
}

function addLocalDays(value: Date, days: number): Date {
  return new Date(value.getFullYear(), value.getMonth(), value.getDate() + days, 12)
}

function startOfLocalWeek(value: Date): Date {
  return addLocalDays(value, -((value.getDay() + 6) % 7))
}

const relativeTimeExpression =
  /(未来|接下来)(?:的)?(?:一周|7天|七天)|未来几天|(?:过去|最近)(?:的)?(?:一周|7天|七天)|(?:上|本|这|下)周[一二三四五六日天]?|(?:上|本|这|下)个?月|后天|明天|明日|今天|今日|当前|现在|实时|昨天|昨日|前天|最新|最近|这几天|近几天/g

export function relativeDateQualifier(content: string, now = new Date()): string {
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 12)
  if (/(未来|接下来)(?:的)?(?:一周|7天|七天)|未来几天/.test(content)) {
    return `${formatLocalDate(today)} 至 ${formatLocalDate(addLocalDays(today, 6))}`
  }
  if (/(?:过去|最近)(?:的)?(?:一周|7天|七天)/.test(content)) {
    return `${formatLocalDate(addLocalDays(today, -6))} 至 ${formatLocalDate(today)}`
  }
  const weekday = content.match(/(上|本|这|下)周([一二三四五六日天])/)
  if (weekday) {
    const weekdayOffset = "一二三四五六日天".indexOf(weekday[2]) % 7
    const weekOffset = weekday[1] === "上" ? -7 : weekday[1] === "下" ? 7 : 0
    return formatLocalDate(addLocalDays(startOfLocalWeek(today), weekOffset + weekdayOffset))
  }
  const week = content.match(/(上|本|这|下)周/)
  if (week) {
    const weekOffset = week[1] === "上" ? -7 : week[1] === "下" ? 7 : 0
    const start = addLocalDays(startOfLocalWeek(today), weekOffset)
    const end = week[1] === "本" || week[1] === "这" ? today : addLocalDays(start, 6)
    return `${formatLocalDate(start)} 至 ${formatLocalDate(end)}`
  }
  const month = content.match(/(上|本|这|下)个?月/)
  if (month) {
    const offset = month[1] === "上" ? -1 : month[1] === "下" ? 1 : 0
    const start = new Date(today.getFullYear(), today.getMonth() + offset, 1, 12)
    const end = month[1] === "本" || month[1] === "这"
      ? today
      : new Date(today.getFullYear(), today.getMonth() + offset + 1, 0, 12)
    return `${formatLocalDate(start)} 至 ${formatLocalDate(end)}`
  }
  const relativeDays: Array<[RegExp, number]> = [
    [/后天/, 2],
    [/明天|明日/, 1],
    [/今天|今日|当前|现在|实时/, 0],
    [/昨天|昨日/, -1],
    [/前天/, -2],
  ]
  for (const [pattern, offset] of relativeDays) {
    if (pattern.test(content)) return formatLocalDate(addLocalDays(today, offset))
  }
  if (/这几天|近几天/.test(content)) {
    return `${formatLocalDate(addLocalDays(today, -6))} 至 ${formatLocalDate(today)}`
  }
  if (/最新|最近/.test(content)) {
    return `${formatLocalDate(addLocalDays(today, -29))} 至 ${formatLocalDate(today)}`
  }
  return ""
}

type TemporalDirection = "PAST" | "CURRENT" | "FUTURE"

function temporalDirection(content: string): TemporalDirection {
  if (/(未来|接下来|明天|明日|后天|下周|下个月)/.test(content)) return "FUTURE"
  if (/(过去|昨天|昨日|前天|上周|上个月|历史|曾经)/.test(content)) return "PAST"
  return "CURRENT"
}

function searchIntentQualifiers(content: string): string[] {
  const direction = temporalDirection(content)
  if (/天气|气温|降雨|降水|台风|weather/i.test(content)) {
    const annualPrecipitation =
      /(?:年|年度|全年).{0,5}(?:降雨量|降水量|雨量)|(?:降雨量|降水量|雨量).{0,5}(?:年|年度|全年)/.test(
        content,
      )
    if (annualPrecipitation) {
      return [
        "年平均降水量",
        "气候公报",
        ...(/水资源|水利/.test(content) ? ["水资源公报"] : []),
      ]
    }

    const qualifiers =
      direction === "PAST"
        ? ["历史天气"]
        : direction === "FUTURE"
          ? ["天气预报"]
          : []
    if (/降雨|降水|雨量/.test(content)) qualifiers.push("降水量")
    if (/气温|温度/.test(content)) qualifiers.push("气温")
    if (/风力|风向|大风/.test(content)) qualifiers.push("风力")
    if (/湿度/.test(content)) qualifiers.push("湿度")
    if (direction === "FUTURE" && /一周|7天|七天|未来几天/.test(content)) {
      qualifiers.push("逐日预报")
    }
    if (!qualifiers.length) qualifiers.push("气温", "降水")
    return qualifiers
  }
  if (/新闻|要闻|动态|消息|进展|事件|news/i.test(content)) {
    return [direction === "PAST" ? "事件记录" : "最新消息"]
  }
  if (/股价|股票|汇率|价格|报价|市值|指数/.test(content)) {
    return [direction === "PAST" ? "历史数据" : "最新数据"]
  }
  if (/比分|赛程|航班|路况|排队|库存|营业时间/.test(content)) {
    return [direction === "PAST" ? "历史记录" : "实时状态"]
  }
  if (/政策|法规|标准|版本|发布|公告/.test(content)) return ["官方信息"]
  return []
}

export function buildSearchQuery(content: string, now = new Date()): string {
  const dateQualifier = relativeDateQualifier(content, now)
  const concise = content
    .replace(
      /列出可核验来源|列出来源|提供来源|附上来源|并用中文|用中文|简要说明|简单说明|简要回答|详细说明|请|帮我|麻烦|联网|上网|搜索|查询|查一下|查找|核实一下/g,
      " ",
    )
    .replace(/\b(?:please|search|look up|online|web|cite sources?)\b/gi, " ")
    .replace(relativeTimeExpression, " ")
    .replace(/天气状况/g, "天气")
    .replace(/出行建议|穿衣建议|穿什么|是否带伞|要不要带伞/g, " ")
    .replace(/温度/g, "气温")
    .replace(
      /(年平均降水量|年降雨量|年降水量|水资源公报|气候公报|统计公报|财务报告|研究报告|政策文件|官方公告|发布说明)/g,
      " $1 ",
    )
    .replace(/如果|分别说明|不同口径|统计口径/g, " ")
    .replace(/有何不同|有什么不同|不同|区别|差异|对比/g, " ")
    .replace(/怎么样|如何|是什么情况|是多少|有哪些|有些什么|有什么/g, " ")
    .replace(/以及|并且|同时|和/g, " ")
    .replace(/(天气|新闻|要闻|动态|价格|股价|汇率|比分|赛程|航班|路况|政策|法规|公告)/g, " $1 ")
    .replace(/[，。！？、,!?;；:：]/g, " ")
    .replace(/^(?:关于|那|那么|再说说|告诉我)\s*/i, "")
    .replace(
      /(?:怎么样|如何|是什么情况|是多少|有哪些|有些什么|有什么)(?:呢|吗|嘛|呀|啊)?$/i,
      "",
    )
    .replace(/(?:呢|吗|嘛|呀|啊)$/i, "")
    .replace(/\s*的\s*/g, " ")
    .replace(/\s+/g, " ")
    .trim()
  const parts = (concise || content.trim()).split(/\s+/).filter(Boolean)
  for (const qualifier of searchIntentQualifiers(content)) {
    if (!parts.some((part) => part === qualifier || part.includes(qualifier))) {
      parts.push(qualifier)
    }
  }
  if (dateQualifier) parts.push(dateQualifier)
  return [...new Set(parts)].join(" ")
}

type ConversationMessageLike = Pick<Message, "role" | "content">

function compactConversationSubject(content: string): string | null {
  let candidate = content
    .trim()
    .replace(/[，。！？、,!?;；:：]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
  if (
    !candidate
    || /^(他|她|它|他们|她们|它们|其|这个|那个|这件事|那件事|那(?:么)?|还有|再说|继续|然后|另外|刚刚|刚才|之前|前面|上面)/i.test(
      candidate,
    )
    || /(?:我|你|我们)?(?:刚刚|刚才|之前|前面|上面|上一(?:次|条|个))(?:问|说|聊|提|回答|讲)|还记得|对话(?:里|中)|上下文/i.test(
      candidate,
    )
  ) {
    return null
  }
  candidate = candidate
    .replace(/^(请|帮我|麻烦|介绍一下|介绍|说说|告诉我)\s*/i, "")
    .replace(/(?:是?谁|是什么(?:人|东西)?|是做什么的|怎么样(?:呢)?|如何(?:呢)?|什么情况|呢)$/i, "")
    .trim()
  if (
    candidate.length < 2
    || candidate.length > 80
    || /[@]|\bhttps?:\/\//i.test(candidate)
    || /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f-]{23,}/i.test(candidate)
  ) {
    return null
  }
  return candidate
}

const contextualFollowUp =
  /^(他|她|它|他们|她们|它们|其|这个人|那个人|此人|该人物|这家公司|那家公司|该公司|这件事|那件事|这个|那个|那(?:么)?|还有|再说|继续|然后|另外)/i

function needsConversationAnchor(content: string): boolean {
  const value = content.trim()
  const hasContextMarker = contextualFollowUp.test(value)
  const subjectRequiredDomain =
    /天气|气温|降雨|动态|价格|股价|汇率|比分|赛程|航班|路况|政策|法规|版本|发布/
  if (!hasContextMarker && !subjectRequiredDomain.test(value)) return false
  const residual = value
    .replace(contextualFollowUp, " ")
    .replace(/(未来|接下来)(?:的)?(?:一周|7天|七天)|未来几天|这几天|近几天/g, " ")
    .replace(/(?:上|本|这|下)周[一二三四五六日天]?|今天|今日|明天|明日|后天|昨天|昨日|前天|当前|现在|最近|最新|实时/g, " ")
    .replace(/天气|气温|降雨|新闻|要闻|动态|消息|价格|股价|汇率|比分|赛程|航班|路况|政策|法规|版本|发布/g, " ")
    .replace(/怎么样|如何|有哪些|有些什么|有什么|是什么|是多少|多少|是谁|情况|再说|说说|告诉我|呢|吗|嘛|的|了|是|有/g, " ")
    .replace(/[^\p{L}\p{N}]+/gu, "")
  return residual.length < 2
}

export type WebQuestionResolution = {
  instruction: string
  contextRequired: boolean
  contextResolved: boolean
}

export function resolveWebQuestion(
  content: string,
  history: readonly ConversationMessageLike[],
): WebQuestionResolution {
  const contextRequired = needsConversationAnchor(content)
  if (!contextRequired) {
    return { instruction: content, contextRequired: false, contextResolved: true }
  }
  for (let index = history.length - 1; index >= 0; index -= 1) {
    const message = history[index]
    if (message.role !== "USER") continue
    const subject = compactConversationSubject(message.content)
    if (!subject) continue
    const followUp = content.trim().replace(contextualFollowUp, "").trim()
    return {
      instruction: `关于${subject}：${followUp || content.trim()}`,
      contextRequired: true,
      contextResolved: true,
    }
  }
  return { instruction: content, contextRequired: true, contextResolved: false }
}

export function resolveWebFollowUp(
  content: string,
  history: readonly ConversationMessageLike[],
): string {
  return resolveWebQuestion(content, history).instruction
}

export function webTaskFailureMessage(code: string | undefined): string {
  if (code === "CAPABILITY_UNAVAILABLE" || code === "CAPABILITY_TIMEOUT") {
    return "联网检索服务暂时不可用或被上游限流，请稍后重试；本次没有使用模型记忆冒充联网结果"
  }
  if (code === "LLM_TIMEOUT" || code === "LLM_RATE_LIMIT" || code === "LLM_SERVER_ERROR") {
    return "检索已经启动，但回答模型暂时不可用或响应超时，请稍后重试"
  }
  if (code === "LLM_SCHEMA_ERROR" || code === "VALIDATION_FAILED") {
    return "本轮回答未通过格式或证据校验；可在“任务”中查看失败原因和重试记录"
  }
  if (code === "BUDGET_EXCEEDED") {
    return "联网任务已达到时间或用量上限并安全停止；可在“任务”中查看追踪记录"
  }
  return "联网任务执行失败；可在“任务”中查看真实失败记录"
}

export function chatRequestFailureMessage(reason: unknown): string {
  const message = reason instanceof Error ? reason.message : "发送失败"
  if (/structured output invalid|valid schema JSON|LLM_SCHEMA_ERROR/i.test(message)) {
    return "请求规划结果格式不完整，系统未执行不确定的操作；请重试本次请求"
  }
  return message
}

export function webSearchLimit(content: string): number {
  if (/有哪些|有什么|哪些|列出|盘点|比较|对比|分别|汇总|综述|深入|研究|趋势|影响|多方面/i.test(content)) return 5
  if (/谁|多少|何时|什么时候|哪里|哪天|怎么样|是什么(?:\?|？)?$/i.test(content)) return 3
  return 4
}

export function latestEnabledSkill(
  skills: readonly SkillInstallation[],
  name: string,
): SkillInstallation | undefined {
  return [...skills]
    .filter((item) => item.name === name && item.state === "ENABLED")
    .sort((left: SkillInstallation, right: SkillInstallation) =>
      left.skill_version.localeCompare(right.skill_version, undefined, {
        numeric: true,
        sensitivity: "base",
      }),
    )
    .at(-1)
}

function modelRuntimeIdentity(model: ModelConfiguration): string {
  try {
    const endpoint = new URL(model.base_url)
    const defaultPort =
      (endpoint.protocol === "http:" && endpoint.port === "80")
      || (endpoint.protocol === "https:" && endpoint.port === "443")
    return [
      endpoint.protocol.toLowerCase(),
      endpoint.hostname.toLowerCase(),
      defaultPort ? "" : endpoint.port,
      endpoint.pathname.replace(/\/+$/, ""),
      model.model_name,
    ].join("|")
  } catch {
    return `${model.base_url.trim().replace(/\/+$/, "").toLowerCase()}|${model.model_name}`
  }
}

export function automaticFallbackModel(
  models: readonly ModelConfiguration[],
  primary: ModelConfiguration | undefined,
): ModelConfiguration | undefined {
  if (!primary) return undefined
  const primaryIdentity = modelRuntimeIdentity(primary)
  const alternatives = [...models]
    .reverse()
    .filter(
      (item) => item.config_id !== primary.config_id
        && modelRuntimeIdentity(item) !== primaryIdentity,
    )
  return alternatives.find((item) => item.provider !== primary.provider)
    ?? alternatives.find(
      (item) => item.base_url !== primary.base_url || item.model_name !== primary.model_name,
    )
}

export function ChatPage({
  api,
  workspace,
  conversation,
  setConversation,
  messages,
  setMessages,
  models,
  skills,
  knowledge,
  onRefresh,
  onOpenModels,
  onOpenTasks,
  onOpenSkills,
  setNotice,
}: Props) {
  const activeModels = useMemo(
    () => models.filter((item) => item.kind === "CHAT" && item.enabled && !item.superseded),
    [models],
  )
  const enabledSkills = useMemo(
    () => skills.filter((item) => item.state === "ENABLED"),
    [skills],
  )
  const userSelectableSkills = useMemo(
    () => enabledSkills.filter(
      (item) => !["web-research", "document-analysis", "grounded-research"].includes(item.name),
    ),
    [enabledSkills],
  )
  const webResearchSkill = latestEnabledSkill(enabledSkills, "web-research")
  const documentAnalysisSkill = latestEnabledSkill(enabledSkills, "document-analysis")
  const groundedResearchSkill = latestEnabledSkill(enabledSkills, "grounded-research")
  const [selectedModelId, setSelectedModelId] = useState("")
  const [selectedSkill, setSelectedSkill] = useState("")
  const [composer, setComposer] = useState("")
  const [streamingReply, setStreamingReply] = useState("")
  const [busy, setBusy] = useState(false)
  const [toolsOpen, setToolsOpen] = useState(false)
  const [modelOpen, setModelOpen] = useState(false)
  const [attachment, setAttachment] = useState<KnowledgeIngestion | null>(null)
  const [preview, setPreview] = useState<KnowledgeDocumentPreview | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)
  const [uploading, setUploading] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const composerInput = useRef<HTMLTextAreaElement>(null)
  const toolsRoot = useRef<HTMLDivElement>(null)
  const modelRoot = useRef<HTMLDivElement>(null)

  const selectedModel = activeModels.find((item) => item.config_id === selectedModelId)
    ?? activeModels.at(-1)
  const timelineMessages = useMemo(() => orderTurnAwareMessages(messages), [messages])
  const attachmentState = attachment
    ? knowledge.find((item) => item.ingestion_id === attachment.ingestion_id) ?? attachment
    : null
  const mention = composer.match(/(?:^|\s)@([\w-]*)$/)
  const mentionMatches = mention
    ? userSelectableSkills.filter(
      (item) => item.name.toLowerCase().includes(mention[1].toLowerCase()),
    )
    : []
  const agentMode = Boolean(
    (selectedSkill && selectedSkill !== "web-research")
      || attachmentState,
  )

  useEffect(() => {
    function closeOnOutsideClick(event: PointerEvent) {
      const target = event.target as Node
      if (toolsOpen && !toolsRoot.current?.contains(target)) setToolsOpen(false)
      if (modelOpen && !modelRoot.current?.contains(target)) setModelOpen(false)
    }
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setToolsOpen(false)
        setModelOpen(false)
      }
    }
    document.addEventListener("pointerdown", closeOnOutsideClick)
    document.addEventListener("keydown", closeOnEscape)
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsideClick)
      document.removeEventListener("keydown", closeOnEscape)
    }
  }, [modelOpen, toolsOpen])

  useEffect(() => {
    if (!busy) composerInput.current?.focus()
  }, [busy])

  async function openAttachmentPreview(ingestionId: string) {
    setPreviewLoading(true)
    try {
      setPreview(await api.knowledgePreview(workspace, ingestionId))
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "附件预览失败")
    } finally {
      setPreviewLoading(false)
    }
  }

  function chooseSkill(name: string) {
    setSelectedSkill(name)
    setComposer((current) => current.replace(/(?:^|\s)@[\w-]*$/, "").trimStart())
    setToolsOpen(false)
  }

  async function attach(file: File | undefined) {
    if (!file) return
    setUploading(true)
    setToolsOpen(false)
    try {
      const result = await api.uploadKnowledge(workspace, file)
      setAttachment(result)
      await onRefresh()
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "附件上传失败")
    } finally {
      setUploading(false)
      if (fileInput.current) fileInput.current.value = ""
    }
  }

  async function send(event: FormEvent) {
    event.preventDefault()
    const content = composer.trim()
    if (!content || busy) return
    if (!selectedModel) {
      setNotice("请先配置并启用一个 Chat 模型")
      onOpenModels()
      return
    }
    const submittedAttachment = attachmentState
    if (submittedAttachment && submittedAttachment.state !== "SUCCEEDED") {
      setNotice(`附件仍在知识摄取中（${submittedAttachment.state}），完成后即可交给 Skill 使用`)
      await onRefresh()
      return
    }
    const manuallySelectedSkill =
      selectedSkill && selectedSkill !== "web-research" ? selectedSkill : ""
    const supplierId = manuallySelectedSkill === "supplier-risk"
      ? content.match(/[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/i)?.[0]
      : undefined
    if (manuallySelectedSkill === "supplier-risk" && !supplierId) {
      setNotice("supplier-risk Skill 需要在消息中包含供应商 UUID，才能调用采购数据能力")
      return
    }

    let conversationIdForRecovery = conversation
    let requestAccepted = false
    setBusy(true)
    setNotice("")
    setComposer("")
    setAttachment(null)
    window.setTimeout(() => composerInput.current?.focus(), 0)
    try {
      let conversationId = conversation
      if (!conversationId) {
        const created = await api.createConversation(workspace, content.slice(0, 32) || "新对话")
        conversationId = created.conversation_id
        setConversation(conversationId)
      }
      conversationIdForRecovery = conversationId
      const userMessageId = crypto.randomUUID()
      const route = await api.routeChat({
        workspace_id: workspace,
        conversation_id: conversationId,
        user_message_id: userMessageId,
        content,
        web_mode: "AUTO",
        model_config_id: selectedModel.config_id,
        ...(submittedAttachment?.document_version_id
          ? { document_version_id: submittedAttachment.document_version_id }
          : {}),
      })
      const automaticSkill =
        route.route === "MIXED"
          ? "grounded-research"
          : route.route === "RAG"
            ? "document-analysis"
            : route.route === "WEB"
              ? "web-research"
              : ""
      const requestedSkill = manuallySelectedSkill || automaticSkill
      const builtInResearchSkills: Record<string, SkillInstallation | undefined> = {
        "web-research": webResearchSkill,
        "document-analysis": documentAnalysisSkill,
        "grounded-research": groundedResearchSkill,
      }
      const requiredSkill = builtInResearchSkills[requestedSkill]
      if (
        requestedSkill in builtInResearchSkills
        && !requiredSkill
      ) {
        setComposer((current) => current || content)
        setAttachment((current) => current ?? submittedAttachment)
        setNotice(`请先在 Skills 页面启用 ${requestedSkill}`)
        onOpenSkills()
        return
      }
      const useAgentMode = Boolean(requestedSkill || route.route === "DURABLE")
      const returnsToChat = [
        "web-research",
        "document-analysis",
        "grounded-research",
      ].includes(requestedSkill)
      // An explicitly selected governed Skill owns its own workflow. Automatic
      // routing only chooses among direct chat, web, document, and mixed research
      // when the user did not select another Skill.
      const useWebResearch = !manuallySelectedSkill
        && (route.route === "WEB" || route.route === "MIXED")
      const useDocument = !manuallySelectedSkill
        && (route.route === "RAG" || route.route === "MIXED")
      const resolvedInstruction = route.resolved_content
      setStreamingReply("")

      if (useAgentMode) {
        if (returnsToChat && !route.research_plan) {
          setComposer((current) => current || content)
          setAttachment((current) => current ?? submittedAttachment)
          setNotice("研究计划未能安全生成，请重试")
          return
        }
        const taskGoal = {
          goal: content,
          constraints: [
            "只使用当前 Skill 声明并通过治理的 Capability",
            ...(useWebResearch ? ["使用工作流中声明的 web.search 获取最新公开信息"] : []),
            ...(useDocument
              ? [submittedAttachment?.document_version_id
                ? "只使用用户选中的不可变文档版本作为内部证据"
                : "只使用当前工作区内已成功入库且未删除的知识文档作为内部证据"]
              : []),
          ],
          deliverables: [
            "输出自然、完整且带有可审计 Claim-Evidence 关联的回答",
          ],
          workspace_id: workspace,
          time_budget_sec: route.route === "MIXED" || useWebResearch ? 180 : useDocument ? 75 : 900,
          token_budget: route.route === "MIXED" || useWebResearch ? 120000 : 50000,
          cost_budget: useWebResearch || useDocument ? 40 : 100,
          ...(requestedSkill ? { requested_skill: requestedSkill } : {}),
          user_context: JSON.stringify({
            original_request: content,
            resolved_request: resolvedInstruction,
            query: useWebResearch
              ? route.search_query ?? resolvedInstruction
              : resolvedInstruction,
            search_queries: useWebResearch ? route.search_queries : undefined,
            limit: useWebResearch ? route.search_limit : undefined,
            instruction: resolvedInstruction,
            research_plan: returnsToChat ? route.research_plan : undefined,
            agent_decision: route.agent_decision,
            model_config_id: selectedModel.config_id,
            web_search_requested: useWebResearch,
            supplier_id: supplierId,
            attachment_ingestion_id: submittedAttachment?.ingestion_id,
            document_version_id: useDocument
              ? submittedAttachment?.document_version_id
              : undefined,
          }),
        }
        const researchTurn = returnsToChat
          ? await api.createResearchTurn({
              workspace_id: workspace,
              conversation_id: conversationId,
              user_message_id: userMessageId,
              content,
              goal: taskGoal,
            })
          : undefined
        const durableTurn = route.route === "DURABLE"
          ? await api.createAgentTaskTurn({
              workspace_id: workspace,
              conversation_id: conversationId,
              user_message_id: userMessageId,
              content,
              goal: taskGoal,
            })
          : undefined
        const task = researchTurn?.task ?? durableTurn?.task ?? await api.createTask(taskGoal)
        requestAccepted = true
        if (researchTurn || durableTurn) {
          setMessages(await api.messages(workspace, conversationId))
        }
        if (!returnsToChat) {
          await onRefresh()
          setNotice("Agent Task 已创建，正在受治理的 Runtime 中执行")
          onOpenTasks()
          return
        }

        setStreamingReply(
          route.route === "MIXED"
            ? "正在核对附件与最新公开来源…"
            : route.route === "RAG"
              ? submittedAttachment?.document_version_id
                ? "正在检索所选附件并核对证据…"
                : "正在检索工作区知识库并核对证据…"
              : "正在联网检索并核对来源…",
        )
        const pollingStartedAt = Date.now()
        const pollingDeadlineMs = (taskGoal.time_budget_sec + 20) * 1000
        for (let attempt = 0; Date.now() - pollingStartedAt < pollingDeadlineMs; attempt += 1) {
          if (attempt > 0) {
            const delayMs = attempt <= 20 ? 500 : attempt <= 50 ? 1000 : 1500
            await new Promise<void>((resolve) => window.setTimeout(resolve, delayMs))
          }
          const [current, currentSteps] = await Promise.all([
            api.task(workspace, task.task_id),
            api.taskSteps(workspace, task.task_id).catch(() => []),
          ])
          if (!current) continue
          if (current.state === "COMPLETED") {
            const completion = await api.taskCompletion(workspace, task.task_id).catch(() => null)
            setStreamingReply("")
            const decision = completion?.delivery_decision?.decision
            if (decision === "FULL_ANSWER" || decision === "PARTIAL_ANSWER") {
              await api.taskResultMessage(workspace, conversationId, task.task_id)
              setMessages(await api.messages(workspace, conversationId))
              if (decision === "PARTIAL_ANSWER") {
                setNotice(completion?.disclosure || "仅展示已通过验证的部分结果")
              }
            } else {
              setNotice(completion?.disclosure || "任务已完成，但当前没有可安全交付的验证结果")
            }
            await onRefresh()
            return
          }
          if (current.state === "FAILED" || current.state === "CANCELLED") {
            const completion = await api.taskCompletion(workspace, task.task_id).catch(() => null)
            setStreamingReply("")
            const decision = completion?.delivery_decision?.decision
            if (decision === "FULL_ANSWER" || decision === "PARTIAL_ANSWER") {
              await api.taskResultMessage(workspace, conversationId, task.task_id)
              setMessages(await api.messages(workspace, conversationId))
              if (decision === "PARTIAL_ANSWER") {
                setNotice(completion?.disclosure || "仅展示已通过验证的部分结果")
              }
            } else if (completion) {
              setNotice(completion.disclosure || (decision === "BLOCKED" ? "该任务的交付被授权或策略阻塞" : "该任务没有可安全交付的验证结果"))
            }
            if (current.state === "CANCELLED") {
              throw new Error("联网任务已取消；可在“任务”中查看")
            }
            if (decision !== "FULL_ANSWER" && decision !== "PARTIAL_ANSWER") {
              await onRefresh()
              return
            }
            const resultMessages = await api.messages(workspace, conversationId)
            if (hasPartiallyAnsweredTurn(resultMessages, userMessageId)) {
              await onRefresh()
              return
            }
            const attempts = await api.taskAttempts(workspace, task.task_id).catch(() => [])
            const failedAttempt = attempts
              .filter((item) => item.failure)
              .sort((left, right) => right.started_at.localeCompare(left.started_at))[0]
            throw new Error(webTaskFailureMessage(failedAttempt?.failure?.code))
          }
          const runningStep = currentSteps.find((item) => item.state === "RUNNING")
          const retried = currentSteps.some((item) => item.state === "SUPERSEDED")
          const elapsedSeconds = Math.max(
            1,
            Math.round((Date.now() - pollingStartedAt) / 1000),
          )
          if (current.state === "WAITING_HITL") {
            setStreamingReply("联网任务正在等待审批…")
          } else if (runningStep?.spec.step_key.includes("search-web") || runningStep?.spec.step_key.includes("search-current-web")) {
            setStreamingReply(`正在联网检索并筛选来源（${elapsedSeconds} 秒）…`)
          } else if (runningStep?.spec.step_key.includes("retrieve-selected-document")) {
            setStreamingReply(
              submittedAttachment?.document_version_id
                ? `正在检索所选附件版本（${elapsedSeconds} 秒）…`
                : `正在检索工作区知识库（${elapsedSeconds} 秒）…`,
            )
          } else if (runningStep?.spec.step_key.includes("synthesize")) {
            setStreamingReply(
              retried
                ? `证据已补充，正在重新生成回答（${elapsedSeconds} 秒）…`
                : `检索完成，正在使用模型生成回答（${elapsedSeconds} 秒）…`,
            )
          } else if (runningStep?.spec.step_key.includes("review")) {
            setStreamingReply(`回答已生成，正在校验证据引用（${elapsedSeconds} 秒）…`)
          } else {
            setStreamingReply(`正在整理联网结果（${elapsedSeconds} 秒）…`)
          }
        }
        throw new Error("联网任务等待超时；任务仍保留，可在“任务”中继续查看")
      }

      await api.streamChat(
        {
          workspace_id: workspace,
          conversation_id: conversationId,
          model_config_id: selectedModel.config_id,
          user_message_id: userMessageId,
          content,
        },
        (chunk) => setStreamingReply((current) => current + chunk),
      )
      requestAccepted = true
      setMessages(await api.messages(workspace, conversationId))
      setStreamingReply("")
      await onRefresh()
    } catch (reason) {
      setStreamingReply("")
      if (conversationIdForRecovery) {
        const recoveredMessages = await api.messages(
          workspace,
          conversationIdForRecovery,
        ).catch(() => null)
        if (recoveredMessages) setMessages(recoveredMessages)
      }
      if (!requestAccepted) {
        setComposer((current) => current || content)
        setAttachment((current) => current ?? submittedAttachment)
      }
      setNotice(chatRequestFailureMessage(reason))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="chat-shell">
      <div className="chat-scroll">
        {messages.length === 0 && !streamingReply ? (
          <div className="chat-welcome">
            <div className="chat-welcome-icon"><Bot size={25} /></div>
            <h2>今天想处理什么？</h2>
            <p>直接提问；系统会自动判断使用普通对话、联网信息、附件或受治理的 Skill。</p>
          </div>
        ) : (
          <div className="chat-thread">
            {timelineMessages.map((item) => (
              <article key={item.message_id} className={`chat-message ${item.role.toLowerCase()}`}>
                <div className="chat-avatar">{item.role === "USER" ? "你" : <Bot size={17} />}</div>
                <div className="chat-copy">
                  {item.role === "USER" && item.attachment && (
                    <button
                      className="message-attachment"
                      type="button"
                      onClick={() => void openAttachmentPreview(item.attachment!.ingestion_id)}
                    >
                      <span className="message-attachment-icon"><FileText size={18} /></span>
                      <span>
                        <b>{item.attachment.name}</b>
                        <small>点击预览附件</small>
                      </span>
                    </button>
                  )}
                  <ChatContent content={item.content} />
                  {turnStatusLabel(item) && (
                    <span
                      aria-label={`轮次状态：${turnStatusLabel(item)}`}
                      className="mt-1 block text-[11px] text-slate-400"
                    >
                      {turnStatusLabel(item)}
                    </span>
                  )}
                </div>
              </article>
            ))}
            {streamingReply && (
              <article className="chat-message assistant">
                <div className="chat-avatar"><Bot size={17} /></div>
                <div className="chat-copy">{streamingReply}<span className="typing-cursor">▍</span></div>
              </article>
            )}
          </div>
        )}
      </div>

      <div className="composer-wrap">
        {(selectedSkill || attachmentState) && (
          <div className="composer-chips">
            {selectedSkill && selectedSkill !== "web-research" && <button type="button" onClick={() => setSelectedSkill("")}><Sparkles size={14} />{selectedSkill}<X size={13} /></button>}
            {attachmentState && <button type="button" onClick={() => setAttachment(null)}><FileText size={14} />{attachmentState.name}<Badge>{attachmentState.state}</Badge><X size={13} /></button>}
          </div>
        )}
        <form className="composer" onSubmit={send}>
          <textarea
            ref={composerInput}
            aria-label="消息"
            placeholder={selectedSkill === "supplier-risk" ? "输入供应商 UUID 和分析目标…" : agentMode ? "描述要交给 Agent 完成的目标…" : "给 Agent Work 发消息"}
            value={composer}
            onChange={(event) => setComposer(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault()
                event.currentTarget.form?.requestSubmit()
              }
            }}
            rows={1}
          />
          {mentionMatches.length > 0 && (
            <div className="mention-menu">
              <p><AtSign size={14} />选择 Skill</p>
              {mentionMatches.map((item) => (
                <button key={item.installation_id} type="button" onClick={() => chooseSkill(item.name)}>
                  <Sparkles size={15} /><span><b>{item.name}</b><small>v{item.skill_version}</small></span>
                </button>
              ))}
            </div>
          )}
          <div className="composer-actions">
            <div className="menu-anchor" ref={toolsRoot}>
              <Button type="button" variant="ghost" size="icon" aria-label="添加" aria-expanded={toolsOpen} onClick={() => { setModelOpen(false); setToolsOpen((value) => !value) }}><Plus size={20} /></Button>
              {toolsOpen && (
                <div className="tool-menu">
                  <button type="button" onClick={() => { setToolsOpen(false); fileInput.current?.click() }} disabled={uploading}><Paperclip size={17} /><span><b>添加附件</b><small>上传到工作区知识库</small></span></button>
                  <div className="tool-menu-divider" />
                  <p>Skills</p>
                  {userSelectableSkills.length ? userSelectableSkills.map((item) => (
                    <button key={item.installation_id} type="button" onClick={() => chooseSkill(item.name)}><Sparkles size={17} /><span><b>{item.name}</b><small>v{item.skill_version}</small></span>{selectedSkill === item.name && <Check size={16} />}</button>
                  )) : <button type="button" onClick={() => { setToolsOpen(false); onOpenSkills() }}><Sparkles size={17} /><span><b>管理 Skills</b><small>启用可由用户指定的受治理能力</small></span></button>}
                </div>
              )}
            </div>
            <input ref={fileInput} className="sr-only" type="file" aria-label="选择附件" accept=".pdf,.docx,.pptx,.xlsx,.csv,.md,.txt" onChange={(event) => void attach(event.target.files?.[0])} />
            <div className="composer-spacer" />
            <div className="menu-anchor" ref={modelRoot}>
              <button className="model-picker" type="button" aria-expanded={modelOpen} onClick={() => { setToolsOpen(false); setModelOpen((value) => !value) }}>
                {selectedModel?.model_name ?? "选择模型"}<ChevronDown size={14} />
              </button>
              {modelOpen && (
                <div className="model-menu">
                  <p>回答模型</p>
                  {activeModels.map((item) => <button aria-label={`选择回答模型 ${item.model_name}`} key={item.config_id} type="button" onClick={() => { setSelectedModelId(item.config_id); setModelOpen(false) }}><span><b>{item.model_name}</b><small>{item.provider}</small></span>{selectedModel?.config_id === item.config_id && <Check size={16} />}</button>)}
                  <button type="button" onClick={() => { setModelOpen(false); onOpenModels() }}>管理模型</button>
                </div>
              )}
            </div>
            <Button className="send-button" size="icon" aria-label="发送" disabled={!composer.trim() || busy || uploading}><Send size={17} /></Button>
          </div>
        </form>
        <p className="composer-note">{agentMode ? "将创建可追踪 Agent Task；附件和联网不会绕过 Runtime 治理。" : "模型可能会出错，请核对重要信息。"}</p>
      </div>
      {(preview || previewLoading) && (
        <div className="attachment-preview-backdrop" role="presentation" onMouseDown={() => !previewLoading && setPreview(null)}>
          <section className="attachment-preview" role="dialog" aria-modal="true" aria-label="附件预览" onMouseDown={(event) => event.stopPropagation()}>
            <header>
              <span><FileText size={18} /><b>{preview?.name ?? "正在加载附件…"}</b></span>
              <button type="button" aria-label="关闭附件预览" onClick={() => setPreview(null)} disabled={previewLoading}><X size={18} /></button>
            </header>
            {previewLoading ? (
              <p className="attachment-preview-loading">正在生成安全预览…</p>
            ) : (
              <>
                <pre>{preview?.content || "该附件没有可预览的文本内容。"}</pre>
                {preview?.truncated && <p className="attachment-preview-note">预览内容较长，已显示前 20 万字符。</p>}
              </>
            )}
          </section>
        </div>
      )}
    </section>
  )
}
