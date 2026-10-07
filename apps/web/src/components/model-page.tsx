import { Archive, CheckCircle2, ChevronDown, History, Pencil, Plus, Server, Trash2, X } from "lucide-react"
import { type FormEvent, useMemo, useState } from "react"

import { ApiClient, type ModelConfiguration } from "../api"
import { Badge } from "./ui/badge"
import { Button } from "./ui/button"
import { Card, CardContent } from "./ui/card"
import { Input } from "./ui/input"

type Props = {
  api: ApiClient
  workspace: string
  models: ModelConfiguration[]
  onChanged: () => Promise<void>
  setNotice: (value: string) => void
}

export function ModelPage({ api, workspace, models, onChanged, setNotice }: Props) {
  const [modalOpen, setModalOpen] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [editingId, setEditingId] = useState("")
  const [kind, setKind] = useState<ModelConfiguration["kind"]>("CHAT")
  const [provider, setProvider] = useState<ModelConfiguration["provider"]>("OpenAICompatible")
  const [baseUrl, setBaseUrl] = useState("")
  const [modelName, setModelName] = useState("")
  const [apiKey, setApiKey] = useState("")
  const [external, setExternal] = useState(false)
  const [probes, setProbes] = useState<Record<string, string>>({})

  const current = useMemo(() => models.filter((item) => !item.superseded), [models])
  const active = current.filter((item) => item.enabled)
  const history = models.filter((item) => item.superseded || !item.enabled)

  function openAdd() {
    setEditingId("")
    setKind("CHAT")
    setProvider("OpenAICompatible")
    setBaseUrl("")
    setModelName("")
    setApiKey("")
    setExternal(false)
    setModalOpen(true)
  }

  function openEdit(item: ModelConfiguration) {
    setEditingId(item.config_id)
    setKind(item.kind)
    setProvider(item.provider)
    setBaseUrl(item.base_url)
    setModelName(item.model_name)
    setApiKey("")
    setExternal(item.external)
    setModalOpen(true)
  }

  async function save(event: FormEvent) {
    event.preventDefault()
    if (!baseUrl.trim() || !modelName.trim()) return
    setBusy(true)
    try {
      const value = {
        workspace_id: workspace,
        kind,
        provider,
        base_url: baseUrl.trim(),
        model_name: modelName.trim(),
        external,
        api_key: apiKey,
      }
      const editing = models.find((item) => item.config_id === editingId)
      if (editing) {
        await api.updateModel(editing.config_id, {
          ...value,
          expected_version: editing.version,
          ...(apiKey ? { api_key: apiKey } : {}),
        })
      } else {
        await api.createModel(value)
      }
      setModalOpen(false)
      setApiKey("")
      setNotice("")
      await onChanged()
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "模型配置失败")
    } finally {
      setBusy(false)
    }
  }

  async function probe(item: ModelConfiguration) {
    setBusy(true)
    try {
      const result = await api.probeModel(workspace, item.config_id)
      setProbes((currentProbes) => ({ ...currentProbes, [item.config_id]: result.content }))
      setNotice("")
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "模型连通测试失败")
    } finally {
      setBusy(false)
    }
  }

  async function changeState(item: ModelConfiguration, enabled: boolean) {
    setBusy(true)
    try {
      await api.setModelEnabled(workspace, item, enabled)
      await onChanged()
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : "模型状态更新失败")
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="model-page">
      <div className="page-heading">
        <div><h2>模型</h2><p>管理这个工作区用于对话、Embedding 和 Reranker 的真实模型连接。</p></div>
        <Button onClick={openAdd}><Plus size={16} />添加模型</Button>
      </div>

      {active.length ? (
        <div className="model-grid">
          {active.map((item) => (
            <Card key={item.config_id} className="model-card">
              <CardContent>
                <div className="model-card-top"><div className="model-icon"><Server size={20} /></div><div className="model-card-title"><b>{item.model_name}</b><span><Badge>{item.kind}</Badge><Badge>{item.provider}</Badge></span></div><Badge className="status-enabled">已启用</Badge></div>
                <dl><div><dt>Base URL</dt><dd>{item.base_url}</dd></div><div><dt>连接</dt><dd>{item.external ? "外部 HTTPS" : "本地 / 私有网络"}</dd></div><div><dt>密钥</dt><dd>{item.has_secret ? "已加密保存" : "未设置"}</dd></div></dl>
                {probes[item.config_id] && <p className="probe-success"><CheckCircle2 size={15} />{probes[item.config_id]}</p>}
                <div className="model-actions"><Button size="sm" variant="secondary" disabled={busy} onClick={() => void probe(item)}>测试连接</Button><Button size="sm" variant="secondary" disabled={busy} onClick={() => openEdit(item)}><Pencil size={14} />编辑</Button><Button size="sm" variant="danger" disabled={busy} onClick={() => void changeState(item, false)}><Trash2 size={14} />移除</Button></div>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : (
        <button className="empty-action" onClick={openAdd}><div><Plus size={22} /></div><h3>尚未配置模型</h3><p>添加一个 OpenAI-compatible 模型后即可开始真实对话。</p><span>添加模型</span></button>
      )}

      {history.length > 0 && (
        <section className="history-section">
          <button className="history-toggle" onClick={() => setHistoryOpen((value) => !value)}><span><History size={17} />已移除与配置历史 <Badge>{history.length}</Badge></span><ChevronDown className={historyOpen ? "rotate-180" : ""} size={17} /></button>
          {historyOpen && <div className="history-list">{history.map((item) => <div key={item.config_id}><Archive size={16} /><span><b>{item.model_name}</b><small>{item.base_url} · v{item.version}</small></span><Badge>{item.superseded ? "历史版本" : "已移除"}</Badge>{!item.superseded && <Button size="sm" variant="secondary" disabled={busy} onClick={() => void changeState(item, true)}>恢复</Button>}</div>)}</div>}
        </section>
      )}

      {modalOpen && (
        <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setModalOpen(false) }}>
          <section className="model-modal" role="dialog" aria-modal="true" aria-labelledby="model-dialog-title">
            <div className="modal-header"><div><h2 id="model-dialog-title">{editingId ? "编辑模型" : "添加模型"}</h2><p>{editingId ? "保存时会创建可审计的新配置版本；API Key 留空会保留原密钥。" : "连接信息只在当前工作区内生效。"}</p></div><Button type="button" variant="ghost" size="icon" aria-label="关闭" onClick={() => setModalOpen(false)}><X size={19} /></Button></div>
            <form onSubmit={save} autoComplete="off">
              <div className="form-grid">
                <label><span>用途</span><select value={kind} onChange={(event) => setKind(event.target.value as ModelConfiguration["kind"])}><option value="CHAT">Chat</option><option value="EMBEDDING">Embedding</option><option value="RERANKER">Reranker</option></select></label>
                <label><span>提供方</span><select value={provider} onChange={(event) => setProvider(event.target.value as ModelConfiguration["provider"])}><option value="OpenAICompatible">OpenAI Compatible</option><option value="Qwen">Qwen</option><option value="DeepSeek">DeepSeek</option></select></label>
                <label className="form-wide"><span>Base URL</span><Input name="provider-endpoint" autoComplete="off" placeholder="http://host.docker.internal:8000/v1" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} required /></label>
                <label className="form-wide"><span>模型名称</span><Input name="provider-model-id" autoComplete="off" placeholder="Qwen/Qwen3.8-27B" value={modelName} onChange={(event) => setModelName(event.target.value)} required /></label>
                <label className="form-wide"><span>API Key</span><Input name="provider-secret-value" autoComplete="new-password" data-1p-ignore="true" data-lpignore="true" type="password" placeholder={editingId ? "留空以保留现有密钥" : external ? "外部模型必填" : "本地模型可留空"} value={apiKey} onChange={(event) => setApiKey(event.target.value)} /></label>
                <label className="checkbox-row form-wide"><input type="checkbox" checked={external} onChange={(event) => setExternal(event.target.checked)} /><span><b>外部模型</b><small>外部地址仅允许 HTTPS；本机 Docker 地址不要勾选。</small></span></label>
              </div>
              <div className="modal-actions"><Button type="button" variant="secondary" onClick={() => setModalOpen(false)}>取消</Button><Button disabled={busy}>{busy ? "保存中…" : editingId ? "保存新版本" : "加密保存"}</Button></div>
            </form>
          </section>
        </div>
      )}
    </div>
  )
}
