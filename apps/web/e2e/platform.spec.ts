import { expect, test } from "@playwright/test"

test("registers, logs in, and creates an isolated workspace through the real API", async ({
  page,
}) => {
  const nonce = Date.now().toString(36)
  const email = `browser-${nonce}@example.test`
  const workspace = `浏览器验收-${nonce}`

  await page.goto("/")
  await expect(
    page.getByRole("heading", { name: "企业智能体工作平台" }),
  ).toBeVisible()
  await page.getByRole("button", { name: "首次使用？创建本地账户" }).click()
  await page.getByLabel("邮箱").fill(email)
  await page.getByLabel("密码").fill("Playwright-password-2026")
  await page.getByRole("button", { name: "注册并登录" }).click()

  await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible()
  await page.getByRole("button", { name: "工作区" }).click()
  await page.getByPlaceholder("工作区名称").fill(workspace)
  await page.getByRole("button", { name: "创建", exact: true }).click()
  await expect(page.getByText(workspace, { exact: true })).toBeVisible()
  await expect(page.getByLabel("选择工作区")).toHaveValue(/.+/)
})
