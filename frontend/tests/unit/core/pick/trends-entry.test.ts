import { expect, test } from "@rstest/core";

import { parsePickRequest } from "@/core/pick-board/request";

test("trends reference tab is distinct from candidate selection", () => {
  expect(parsePickRequest({ tab: "trends", ts: "order" }).tab).toBe("trends");
});
