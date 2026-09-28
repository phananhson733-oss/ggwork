import { describe, expect, it } from "@rstest/core";

import { loadTranslations } from "@/core/i18n/translations";
import { parseCron } from "@/core/scheduled-tasks/cron";
import { RECIPES } from "@/core/scheduled-tasks/recipes";

// Scheduled runs are unattended, so these prompts must be complete, carry no
// placeholders, and never promise what the pick tools cannot do.
const FORBIDDEN = ["{{", "[", "web_search", "保存", "飞书", "从未", "发布到"];

describe("pick scheduled-task recipes", () => {
  it("have unique ids that match their title keys", () => {
    const ids = RECIPES.map((recipe) => recipe.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const recipe of RECIPES) {
      expect(recipe.id).toBe(recipe.titleKey);
    }
  });

  it("have a title in both locales", async () => {
    const [english, chinese] = await Promise.all([
      loadTranslations("en-US"),
      loadTranslations("zh-CN"),
    ]);
    for (const recipe of RECIPES) {
      expect(
        english.scheduledTasks.recipes[recipe.titleKey].title,
      ).toBeTruthy();
      expect(
        chinese.scheduledTasks.recipes[recipe.titleKey].title,
      ).toBeTruthy();
    }
  });

  it("run on a preset cron in Asia/Shanghai, never at the same minute", () => {
    const minutes = new Set<string>();
    for (const recipe of RECIPES) {
      expect(recipe.schedule.schedule_type).toBe("cron");
      expect(recipe.schedule.timezone).toBe("Asia/Shanghai");
      const cron = String(recipe.schedule.schedule_spec.cron);
      expect(["daily", "weekly"]).toContain(parseCron(cron).preset);
      const [minute, hour] = cron.split(" ");
      minutes.add(`${hour}:${minute}`);
    }
    expect(minutes.size).toBe(RECIPES.length);
  });

  it("carry complete prompts without placeholders or unsupported promises", () => {
    for (const recipe of RECIPES) {
      for (const word of FORBIDDEN) {
        expect(recipe.prompt).not.toContain(word);
      }
      expect(recipe.prompt).toContain("data_as_of");
    }
  });

  it("pin the tool arguments each recipe depends on", () => {
    const byId = Object.fromEntries(RECIPES.map((r) => [r.id, r.prompt]));
    expect(byId.dailyCandidates).toContain("exclude_posted=true");
    expect(byId.kdDaily).toContain("signal_kind=kd");
    expect(byId.kdDaily).toContain("sort=rank");
    // kw has no ranks; the server rejects sort=rank for it.
    expect(byId.kwWeekly).toContain("signal_kind=kw");
    expect(byId.kwWeekly).not.toContain("sort=rank");
    expect(byId.poolWeekly).toContain("pick_count_candidates");
    expect(byId.poolWeekly).toContain("exclude_selected=false");
  });
});
