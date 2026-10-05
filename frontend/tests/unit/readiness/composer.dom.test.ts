import { afterEach, expect, it } from "@rstest/core";

import { CHAT_COMPOSER_SELECTOR } from "../../e2e-pick/support/readiness-protocol";

afterEach(() => {
  document.body.innerHTML = "";
});

it("the opened candidate save note cannot receive the next chat prompt", () => {
  document.body.innerHTML =
    '<form><textarea aria-label="Chat prompt"></textarea><button aria-label="Submit">Send</button></form><aside><label>保存备注<textarea aria-label="保存备注"></textarea></label></aside>';
  const targets = document.querySelectorAll<HTMLTextAreaElement>(
    CHAT_COMPOSER_SELECTOR,
  );
  expect(targets).toHaveLength(1);
  targets[0]!.value = "Synthetic next batch prompt";
  expect(
    document.querySelector<HTMLTextAreaElement>('[aria-label="Chat prompt"]')!
      .value,
  ).toBe("Synthetic next batch prompt");
  expect(
    document.querySelector<HTMLTextAreaElement>('[aria-label="保存备注"]')!
      .value,
  ).toBe("");
});
