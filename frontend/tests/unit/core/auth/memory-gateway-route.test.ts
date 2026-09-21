import { afterEach, expect, rs, test } from "@rstest/core";
import { NextRequest } from "next/server";

rs.mock("@/core/auth/gateway-config", () => ({
  getGatewayConfig: () => ({
    internalGatewayUrl: "https://gateway.example",
    trustedOrigins: ["https://workbench.example"],
  }),
}));

import { POST } from "@/app/api/memory/[...path]/route";
import { GET } from "@/app/api/memory/route";

afterEach(() => {
  rs.restoreAllMocks();
});

test("memory routes use the private Gateway URL and preserve session/CSRF", async () => {
  const fetchMock = rs
    .spyOn(globalThis, "fetch")
    .mockImplementation(async () => new Response("{}", { status: 200 }));
  await GET(
    new NextRequest("https://workbench.example/api/memory", {
      headers: { cookie: "access_token=synthetic" },
    }),
  );
  expect(fetchMock.mock.calls[0]?.[0]).toEqual(
    new URL("https://gateway.example/api/memory"),
  );
  expect(new Headers(fetchMock.mock.calls[0]?.[1]?.headers).get("cookie")).toBe(
    "access_token=synthetic",
  );
  await POST(
    new NextRequest("https://workbench.example/api/memory/search", {
      method: "POST",
      headers: {
        cookie: "access_token=synthetic",
        "x-csrf-token": "synthetic-csrf",
      },
      body: "{}",
    }),
    { params: Promise.resolve({ path: ["search"] }) },
  );
  expect(fetchMock.mock.calls[1]?.[0]).toEqual(
    new URL("https://gateway.example/api/memory/search"),
  );
  expect(
    new Headers(fetchMock.mock.calls[1]?.[1]?.headers).get("x-csrf-token"),
  ).toBe("synthetic-csrf");
});
