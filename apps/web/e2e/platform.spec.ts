import { expect, test } from "@playwright/test"

test("registers, logs in, and creates an isolated workspace through the real API", async ({
  page,
}) => {
  const nonce = Date.now().toString(36)
  const email = `browser-${nonce}@example.test`
  const workspace = `浏览器验收-${nonce}`
  const model = `browser-model-${nonce}`

  await page.goto("/")
  await expect(
    page.getByRole("heading", { name: "企业智能体工作平台" }),
  ).toBeVisible()
  await page.getByRole("button", { name: "首次使用？创建本地账户" }).click()
  await page.getByLabel("邮箱").fill(email)
  await page.getByLabel("密码").fill("Playwright-password-2026")
  await page.getByRole("button", { name: "注册并登录" }).click()

  await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible()
  await expect(page.getByRole("heading", { name: "今天想处理什么？" })).toBeVisible()
  const workspaceSelector = page.getByLabel("选择工作区")
  await expect(workspaceSelector).toHaveValue(/.+/)
  await expect(workspaceSelector.locator("option:checked")).toHaveText("我的工作区")

  await page.getByRole("button", { name: "模型", exact: true }).click()
  await page.getByRole("button", { name: "添加模型" }).first().click()
  await expect(page.locator("[name=provider-endpoint]")).toHaveValue("")
  await expect(page.locator("[name=provider-model-id]")).toHaveValue("")
  await expect(page.locator("[name=provider-secret-value]")).toHaveValue("")
  await page.locator("[name=provider-endpoint]").fill("http://host.docker.internal:8000/v1")
  await page.locator("[name=provider-model-id]").fill(model)
  await page.getByRole("button", { name: "加密保存" }).click()
  const modelCard = page.locator(".model-card", { hasText: model })
  await expect(modelCard).toBeVisible()

  await page.getByRole("button", { name: "新对话" }).click()
  await expect(page.getByRole("button", { name: new RegExp(model) })).toBeVisible()
  await page.getByRole("button", { name: "添加", exact: true }).click()
  await expect(page.getByRole("button", { name: /添加附件/ })).toBeVisible()
  await expect(page.getByRole("button", { name: /管理 Skills/ })).toBeVisible()
  await page.keyboard.press("Escape")
  await expect(page.getByRole("button", { name: /添加附件/ })).not.toBeVisible()

  await page.getByRole("button", { name: "添加", exact: true }).click()
  await page.getByRole("button", { name: /管理 Skills/ }).click()
  await expect(page.getByRole("button", { name: /添加附件/ })).not.toBeVisible()
  await expect(page.getByRole("button", { name: "Skills", exact: true })).toBeVisible()
  await expect(page.getByText("通用联网搜索")).toBeVisible()

  await page.getByRole("button", { name: "模型", exact: true }).click()
  await modelCard.getByRole("button", { name: "编辑" }).click()
  await expect(page.locator("[name=provider-model-id]")).toHaveValue(model)
  await expect(page.locator("[name=provider-secret-value]")).toHaveValue("")
  await page.getByRole("button", { name: "取消" }).click()
  await modelCard.getByRole("button", { name: "移除" }).click()
  await expect(modelCard).toHaveCount(0)
  await page.getByRole("button", { name: /已移除与配置历史/ }).click()
  await expect(page.locator(".history-list")).toContainText(model)

  await page.getByRole("button", { name: "工作区" }).click()
  await page.getByPlaceholder("工作区名称").fill(workspace)
  await page.getByRole("button", { name: "创建", exact: true }).click()
  await expect(
    page.getByRole("button", { name: `${workspace} WORKSPACE_OWNER` }),
  ).toBeVisible()
  await expect(workspaceSelector.locator("option:checked")).toHaveText(workspace)
})
