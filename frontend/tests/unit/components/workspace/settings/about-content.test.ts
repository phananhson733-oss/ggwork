import { afterEach, expect, test, rs } from "@rstest/core";

const original = process.env.NEXT_PUBLIC_APP_VERSION;

afterEach(() => {
  rs.resetModules();
  if (original === undefined) {
    delete process.env.NEXT_PUBLIC_APP_VERSION;
  } else {
    process.env.NEXT_PUBLIC_APP_VERSION = original;
  }
});

test("aboutMarkdown heading interpolates the app version", async () => {
  process.env.NEXT_PUBLIC_APP_VERSION = "9.9.9-test";
  const { aboutMarkdown } =
    await import("@/components/workspace/settings/about-content");
  // The heading carries the version stamp.
  expect(aboutMarkdown).toContain("# About GGWork 9.9.9-test\n");
  // The upstream attribution credits DeerFlow and keeps its MIT notice.
  expect(aboutMarkdown).toContain(
    "GGWork is built on the open-source DeerFlow project, which is distributed under the **MIT License**.",
  );
  // The internal app page makes no external requests or upstream promotion.
  expect(aboutMarkdown).not.toMatch(/star-history|deerflow\.tech|github\.com/);
});

test("aboutMarkdown heading reflects the package version when env is unset", async () => {
  delete process.env.NEXT_PUBLIC_APP_VERSION;
  const { APP_VERSION } = await import("@/version");
  const { aboutMarkdown } =
    await import("@/components/workspace/settings/about-content");
  // Positive: the heading carries the real resolved version. This catches an
  // empty or undefined APP_VERSION interpolation (`About GGWork \n` /
  // `About GGWork undefined`), not just removal of the old literal.
  expect(aboutMarkdown).toContain(`# About GGWork ${APP_VERSION}\n`);
});
