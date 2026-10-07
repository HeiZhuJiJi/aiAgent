import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { afterEach, expect, test, vi } from "vitest"

import App from "./App"
import type { KnowledgeIngestion } from "./api"

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

test("shows the local authentication entry and can switch registration mode", () => {
  render(<App />)
  expect(screen.getByRole("heading", { name: "企业智能体工作平台" })).toBeInTheDocument()
  fireEvent.click(screen.getByRole("button", { name: "首次使用？创建本地账户" }))
  expect(screen.getByRole("button", { name: "注册并登录" })).toBeInTheDocument()
})

test("explains an embedding outage and retries the same failed ingestion version", async () => {
  const workspace = {
    workspace_id: "11111111-1111-4111-8111-111111111111",
    name: "测试工作区",
    role: "OWNER",
    version: 1,
  }
  const failed: KnowledgeIngestion = {
    ingestion_id: "22222222-2222-4222-8222-222222222222",
    workspace_id: workspace.workspace_id,
    document_id: "33333333-3333-4333-8333-333333333333",
    name: "original.pdf",
    media_type: "application/pdf",
    source_hash: "a".repeat(64),
    parser_version: "parser-1.0.0+bge-m3",
    state: "FAILED",
    version: 5,
    document_version_id: null,
    document_version: null,
    safe_error: "LLM_SERVER_ERROR [EMBED]: model transport attempts exhausted",
    created_at: "2026-09-14T00:00:00Z",
    completed_at: "2026-09-14T00:00:10Z",
  }
  let currentKnowledge = failed
  const response = (value: unknown, status = 200) => new Response(
    JSON.stringify(value),
    { status, headers: { "Content-Type": "application/json" } },
  )
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(
    async (input, init) => {
      const path = String(input)
      if (path === "/api/auth/login") return response({ access_token: "test-token" })
      if (path === "/api/workspaces") return response([workspace])
      if (
        path === "/api/knowledge/documents/" + failed.ingestion_id + "/retry"
        && init?.method === "POST"
      ) {
        currentKnowledge = {
          ...failed,
          state: "PENDING",
          version: failed.version + 1,
          safe_error: null,
          completed_at: null,
        }
        return response(currentKnowledge, 202)
      }
      if (
        path
        === "/api/knowledge/documents?workspace_id=" + workspace.workspace_id
      ) {
        return response([currentKnowledge])
      }
      return response([])
    },
  )

  render(<App />)
  fireEvent.change(screen.getByLabelText("邮箱"), {
    target: { value: "owner@example.test" },
  })
  fireEvent.change(screen.getByLabelText("密码"), {
    target: { value: "long-test-password" },
  })
  fireEvent.click(screen.getByRole("button", { name: "登录" }))
  fireEvent.click(await screen.findByRole("button", { name: "知识库" }))

  expect(await screen.findByText(/向量模型服务不可用/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole("button", { name: /重试摄取/ }))

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/knowledge/documents/" + failed.ingestion_id + "/retry",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          workspace_id: workspace.workspace_id,
          expected_version: failed.version,
        }),
      }),
    )
  })
  expect(screen.queryByRole("button", { name: /重试摄取/ })).not.toBeInTheDocument()
})
