import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import {
  chromium,
  expect,
  type BrowserContext,
  type Page,
  type TestInfo,
} from "@playwright/test";

/** Measured DOM colors, not an accessibility certification or screen-reader run. */
export async function visualMeasurements(page: Page) {
  return page.evaluate(() => {
    const heading = [...document.querySelectorAll("h1")].find(
      (h) => h.textContent === "排期草稿",
    );
    const root =
      heading?.closest("section") ??
      document.querySelector("main") ??
      document.body;
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 1;
    const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
    const rgba = (color: string) => {
      ctx.clearRect(0, 0, 1, 1);
      ctx.fillStyle = color;
      ctx.fillRect(0, 0, 1, 1);
      return [...ctx.getImageData(0, 0, 1, 1).data];
    };
    const over = (fg: number[], bg: number[]) => {
      const a = fg[3]! / 255;
      return [0, 1, 2].map((i) => fg[i]! * a + bg[i]! * (1 - a)).concat(255);
    };
    const background = (element: Element): number[] => {
      const chain: Element[] = [];
      let current: Element | null = element;
      while (current) {
        chain.unshift(current);
        current = current.parentElement;
      }
      return chain.reduce(
        (color, node) =>
          over(rgba(getComputedStyle(node).backgroundColor), color),
        [255, 255, 255, 255],
      );
    };
    const luminance = (color: number[]) =>
      color
        .slice(0, 3)
        .map((v) => {
          const n = v / 255;
          return n <= 0.04045 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4;
        })
        .reduce((total, v, i) => total + v * [0.2126, 0.7152, 0.0722][i]!, 0);
    const ratio = (a: number[], b: number[]) => {
      const x = luminance(a),
        y = luminance(b);
      return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
    };
    const text: { sample: string; ratio: number; fontSize: string }[] = [];
    const controls: {
      label: string;
      width: number;
      height: number;
      borderContrast: number | null;
    }[] = [];
    for (const element of root.querySelectorAll<HTMLElement>("*")) {
      const rect = element.getBoundingClientRect();
      const style = getComputedStyle(element);
      if (
        !element.checkVisibility() ||
        !rect.width ||
        !rect.height ||
        style.visibility === "hidden" ||
        element.closest("[hidden]")
      )
        continue;
      const sample = [...element.childNodes]
        .filter((n) => n.nodeType === Node.TEXT_NODE)
        .map((n) => n.textContent)
        .join(" ")
        .trim();
      const disabled =
        element.matches(":disabled") || !!element.closest("fieldset:disabled");
      if (sample && !disabled && style.backgroundImage === "none")
        text.push({
          sample: sample.slice(0, 100),
          ratio: +ratio(
            over(rgba(style.color), background(element)),
            background(element),
          ).toFixed(2),
          fontSize: style.fontSize,
        });
      if (
        element.matches("button,a,input,select,textarea,summary") &&
        !disabled
      )
        controls.push({
          label: (
            element.getAttribute("aria-label") ??
            element.textContent ??
            element.tagName
          )
            .trim()
            .slice(0, 100),
          width: +rect.width.toFixed(1),
          height: +rect.height.toFixed(1),
          borderContrast:
            parseFloat(style.borderTopWidth) > 0
              ? +ratio(
                  over(rgba(style.borderTopColor), background(element)),
                  background(element),
                ).toFixed(2)
              : null,
        });
    }
    return {
      innerWidth,
      scrollWidth: document.documentElement.scrollWidth,
      devicePixelRatio,
      text,
      controls,
      limitations: [
        "Gradient/image backgrounds and opacity are not fully composited",
        "Border contrast does not establish complete control or focus contrast",
        "Accessibility tree is not a screen reader",
      ],
    };
  });
}

/** Native Chromium tab zoom, verified by chrome.tabs.getZoom and layout metrics.
 * Uses a temporary test-only extension, never changes the user's browser/profile.
 * https://playwright.dev/docs/chrome-extensions
 * https://developer.chrome.com/docs/extensions/reference/api/tabs#method-setZoom
 */
export async function actualBrowserZoom(
  source: BrowserContext,
  url: string,
  info: TestInfo,
) {
  const extension = mkdtempSync(join(tmpdir(), "pick-qa-zoom-"));
  writeFileSync(
    join(extension, "manifest.json"),
    JSON.stringify({
      manifest_version: 3,
      name: "Local QA zoom only",
      version: "1.0",
      host_permissions: ["http://127.0.0.1/*"],
      background: { service_worker: "worker.js" },
    }),
  );
  writeFileSync(
    join(extension, "worker.js"),
    "chrome.runtime.onInstalled.addListener(() => {});\n",
  );
  let context: BrowserContext | undefined;
  try {
    context = await chromium.launchPersistentContext("", {
      channel: "chromium",
      executablePath: process.env.PICK_COMPLETION_CHROMIUM,
      viewport: { width: 1280, height: 900 },
      args: [
        `--disable-extensions-except=${extension}`,
        `--load-extension=${extension}`,
      ],
    });
    await context.route("**/*", (route) =>
      new URL(route.request().url()).hostname === "127.0.0.1"
        ? route.continue()
        : route.abort(),
    );
    await context.addCookies(await source.cookies());
    const worker =
      context.serviceWorkers()[0] ??
      (await context.waitForEvent("serviceworker"));
    const page = await context.newPage();
    await page.goto(url);
    await expect(
      page.getByRole("heading", { name: "排期草稿", exact: true }),
    ).toBeVisible();
    const before = await page.evaluate(() => ({
      innerWidth,
      devicePixelRatio,
    }));
    const zoom = await worker.evaluate(async (target) => {
      const tabs = (
        globalThis as unknown as {
          chrome: {
            tabs: {
              query: (query: object) => Promise<{ id: number; url: string }[]>;
              setZoom: (id: number, zoom: number) => Promise<void>;
              getZoom: (id: number) => Promise<number>;
            };
          };
        }
      ).chrome.tabs;
      const tab = (await tabs.query({})).find((item) => item.url === target)!;
      await tabs.setZoom(tab.id, 2);
      return tabs.getZoom(tab.id);
    }, url);
    expect(zoom).toBe(2);
    await expect
      .poll(() => page.evaluate(() => devicePixelRatio))
      .toBe(before.devicePixelRatio * 2);
    const after = await page.evaluate(() => ({
      innerWidth,
      devicePixelRatio,
      scrollWidth: document.documentElement.scrollWidth,
    }));
    expect(after.innerWidth).toBe(before.innerWidth / 2);
    expect(after.scrollWidth).toBeLessThanOrEqual(after.innerWidth);
    await page.screenshot({
      path: info.outputPath("plan-native-200-percent.png"),
      fullPage: true,
    });
    return { zoom, before, after };
  } finally {
    await context?.close();
    rmSync(extension, { recursive: true, force: true });
  }
}
