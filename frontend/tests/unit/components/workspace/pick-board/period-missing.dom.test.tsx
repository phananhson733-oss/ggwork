import { afterEach, expect, it } from "@rstest/core";
import { cleanup, render, screen } from "@testing-library/react";

import { MirrorNotice } from "@/components/workspace/pick-board/views/notices";
import { parsePickRequest } from "@/core/pick-board/request";
afterEach(cleanup);
it("renders absent period as an actionable source state and preserves query/version controls", () => {
  const { container } = render(
    <MirrorNotice
      notice={{ kind: "period-missing" }}
      req={parsePickRequest({
        tab: "rank",
        rk: "kw",
        v: "7",
        q: "原查询",
        rl: "en",
        week: "1999-01-04",
      })}
    />,
  );
  expect(screen.getByText("当前资料中没有可读取的期次。")).toBeTruthy();
  expect(screen.getByLabelText<HTMLSelectElement>("更换榜单").value).toBe("kw");
  expect(
    container.querySelector<HTMLInputElement>('input[name="v"]')?.value,
  ).toBe("7");
  expect(
    container.querySelector<HTMLInputElement>('input[name="q"]')?.value,
  ).toBe("原查询");
  expect(
    screen.getByRole("link", { name: "查看同步状态" }).getAttribute("href"),
  ).toContain("tab=imports");
  expect(container.textContent).not.toContain("配置有误");
});
it("treats expired session/CSRF as reauthentication with the exact board context, not source misconfiguration", () => {
  render(
    <MirrorNotice
      notice={{ kind: "session-required" }}
      req={parsePickRequest({ tab: "all", v: "7", page: "3", q: "原查询" })}
    />,
  );
  const login = new URL(
    screen.getByRole("link", { name: "重新登录" }).getAttribute("href")!,
    "http://localhost",
  );
  const next = login.searchParams.get("next")!;
  expect(next).toContain("v=7");
  expect(next).toContain("page=3");
  expect(screen.getByRole("alert").textContent).not.toContain("配置有误");
});
