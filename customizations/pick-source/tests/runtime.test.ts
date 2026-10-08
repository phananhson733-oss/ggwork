import assert from "node:assert/strict";
import test from "node:test";
import { routeRequest } from "../runtime/http";
import { jobDue, runLocked } from "../runtime/jobs";

const deps = {
  feedToken: "read-v1",
  exportToken: "read-v2",
  feed: async () => {
    throw new Error("database password must never escape");
  },
  export: async () => {
    throw new Error("database password must never escape");
  },
};
test("the migrated source refuses anonymous and wrong-token requests before loading data", async () => {
  for (const path of ["/api/pick-feed", "/api/pick-feed/v2/manifest"]) {
    const r = await routeRequest(new Request("http://localhost" + path), deps);
    assert.equal(r.status, 401);
  }
});
test("read failures return a safe source failure, not a falsely fresh version", async () => {
  const r = await routeRequest(
    new Request("http://localhost/api/pick-feed", {
      headers: { authorization: "Bearer read-v1" },
    }),
    deps,
  );
  assert.equal(r.status, 503);
  assert.deepEqual(await r.json(), { ok: false, error: "read_failed" });
});
test("the data service exposes no website, playback, sync, or arbitrary resource execution", async () => {
  for (const path of [
    "/",
    "/api/sync",
    "/admin",
    "/api/pick-feed/v2/../../sync",
  ]) {
    assert.equal(
      (await routeRequest(new Request("http://localhost" + path), deps)).status,
      404,
    );
  }
  assert.equal(
    (
      await routeRequest(
        new Request("http://localhost/api/pick-feed", { method: "POST" }),
        deps,
      )
    ).status,
    405,
  );
});
test("a failed or stale collection remains due; a current success is not repeated", () => {
  const now = new Date("2026-10-08T04:00:00Z");
  assert.equal(jobDue(null, now, 6), true);
  assert.equal(
    jobDue({ status: "failed", completedAt: "2026-10-08T03:00:00Z" }, now, 6),
    true,
  );
  assert.equal(
    jobDue({ status: "success", completedAt: "2026-10-08T03:00:00Z" }, now, 6),
    false,
  );
  assert.equal(
    jobDue({ status: "success", completedAt: "2026-10-07T03:00:00Z" }, now, 6),
    true,
  );
});
test("a held collector lock prevents concurrent import; a failure releases only its own lock", async () => {
  let worked = 0,
    unlocked = 0;
  const locked = {
    acquire: async () => false,
    release: async () => {
      unlocked++;
    },
  };
  assert.equal(
    await runLocked(locked, async () => {
      worked++;
    }),
    false,
  );
  assert.equal(worked, 0);
  assert.equal(unlocked, 0);
  const owned = {
    acquire: async () => true,
    release: async () => {
      unlocked++;
    },
  };
  await assert.rejects(
    runLocked(owned, async () => {
      throw new Error("source incomplete");
    }),
  );
  assert.equal(unlocked, 1);
});

test("restored ranking history retains source dates, never today's date", async () => {
  const { rankHistory } = await import("../runtime/queyu-history");
  const days = rankHistory([
    {
      kind: "qc",
      platform: "kalos",
      title: "Synthetic drama",
      lang: "英语",
      evidence_on: "2026-10-06",
      rank: 2,
      production_type: "真人",
      tags: "爱情 · 都市",
      listed_on: "2026-09-12",
      payload: {
        qy: 1,
        pid: "p1",
        h: [
          ["2026-10-05", 5],
          ["2026-10-06", 2],
        ],
      },
    },
  ]);
  assert.deepEqual(
    days.map((d) => d.date),
    ["2026-10-05", "2026-10-06"],
  );
  assert.equal(days[0]!.conv[0]!.rank, 5);
  assert.equal(days[1]!.conv[0]!.rank, 2);
  assert.equal(days[1]!.conv[0]!.productionType, "真人");
  assert.equal(days[1]!.conv[0]!.labels, "爱情 · 都市");
  assert.equal(days[1]!.conv[0]!.publishTime, "2026-09-12");
});

test("collector status requires the read token and returns only receipts", async () => {
  assert.equal(
    (await routeRequest(new Request("http://localhost/status"), deps)).status,
    401,
  );
  const r = await routeRequest(
    new Request("http://localhost/status", {
      headers: { authorization: "Bearer read-v2" },
    }),
    { ...deps, status: async () => ({ enabled: true, jobs: [] }) },
  );
  assert.deepEqual(await r.json(), { enabled: true, jobs: [] });
});

test("timeout waits for a SIGTERM-resistant child group to stop", async () => {
  const { command } = await import("../runtime/command");
  const signal = AbortSignal.timeout(150);
  const start = Date.now();
  await assert.rejects(
    command(
      process.execPath,
      ["-e", "process.on('SIGTERM',()=>{});setInterval(()=>{},1000)"],
      process.cwd(),
      process.env,
      signal,
    ),
  );
  assert.ok(
    Date.now() - start >= 1900,
    "must retain the lock during TERM grace period",
  );
});

test("resource links reject executable schemes and URL credentials", async () => {
  const { safeResourceUrl } = await import("../runtime/resources");
  for (const s of [
    "javascript:alert(1)",
    "data:text/html,x",
    "https://user:password@example.com",
  ])
    assert.equal(safeResourceUrl(s), null);
  assert.equal(
    safeResourceUrl("https://example.com/resource"),
    "https://example.com/resource",
  );
  assert.equal(
    (await routeRequest(new Request("http://localhost/resource?row=x"), deps))
      .status,
    401,
  );
});

test("empty optional CPS configuration uses the provider defaults", async () => {
  const keys = [
    "CPS_BASE_URL",
    "CPS_APP",
    "CPS_DETAIL_BOOK_TYPE",
    "CPS_ACCOUNT",
    "CPS_PASSWORD",
  ];
  const prior = Object.fromEntries(keys.map((k) => [k, process.env[k]]));
  const fetcher = globalThis.fetch;
  const seen: string[] = [];
  try {
    process.env.CPS_BASE_URL = "";
    process.env.CPS_APP = "";
    process.env.CPS_DETAIL_BOOK_TYPE = "";
    process.env.CPS_ACCOUNT = "fixture";
    process.env.CPS_PASSWORD = "fixture";
    globalThis.fetch = async (input) => {
      seen.push(String(input));
      return new Response("", { status: 500 });
    };
    const { getAccountTerms } = await import("../src/lib/cps/client");
    await assert.rejects(getAccountTerms());
    assert.equal(seen[0], "https://cps.reelshort.com/api/v1/user/login");
  } finally {
    globalThis.fetch = fetcher;
    for (const k of keys) {
      if (prior[k] === undefined) delete process.env[k];
      else process.env[k] = prior[k];
    }
  }
});
