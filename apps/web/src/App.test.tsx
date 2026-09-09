import { fireEvent, render, screen } from "@testing-library/react"
import { expect, test } from "vitest"

import App from "./App"

test("shows the local authentication entry and can switch registration mode", () => {
  render(<App />)
  expect(screen.getByRole("heading", { name: "企业智能体工作平台" })).toBeInTheDocument()
  fireEvent.click(screen.getByRole("button", { name: "首次使用？创建本地账户" }))
  expect(screen.getByRole("button", { name: "注册并登录" })).toBeInTheDocument()
})
