import assert from "node:assert/strict";
import test from "node:test";
import { listBooks } from "../src/lib/cps/client";
import { withSyncDeadline } from "../src/lib/sync-deadline";

async function fixture(
  replies: Response[],
  work: (calls: string[]) => Promise<void>,
) {
  const prior = globalThis.fetch;
  const account = process.env.CPS_ACCOUNT;
  const password = process.env.CPS_PASSWORD;
  const calls: string[] = [];
  process.env.CPS_ACCOUNT = "fixture";
  process.env.CPS_PASSWORD = "fixture";
  globalThis.fetch = async (input, init) => {
    if (String(input).endsWith("/user/login"))
      return Response.json({ code: 0, data: { accessToken: "fixture-token" } });
    calls.push(String(init?.body));
    const reply = replies.shift();
    assert.ok(reply, "No unexpected retry");
    return reply;
  };
  try {
    await work(calls);
  } finally {
    globalThis.fetch = prior;
    if (account === undefined) delete process.env.CPS_ACCOUNT;
    else process.env.CPS_ACCOUNT = account;
    if (password === undefined) delete process.env.CPS_PASSWORD;
    else process.env.CPS_PASSWORD = password;
  }
}
const overloaded = () =>
  Response.json({ code: 100000, msg: "service overloaded" });
const success = () =>
  Response.json({ code: 0, data: { books: [], next_cursor: null } });

test("a transient CPS overload retries the same page without dropping its cursor", async () => {
  await fixture([overloaded(), success()], async (calls) => {
    assert.deepEqual(await listBooks({ cursor: "retained-cursor" }), {
      books: [],
      nextCursor: null,
    });
    assert.equal(calls.length, 2);
    assert.equal(calls[0], calls[1]);
  });
});

test("HTTP 503 retries, but invalid parameters never retry", async () => {
  await fixture(
    [new Response("", { status: 503 }), success()],
    async (calls) => {
      await listBooks();
      assert.equal(calls.length, 2);
    },
  );
  await fixture([new Response("", { status: 400 })], async (calls) => {
    await assert.rejects(listBooks(), /HTTP 400/);
    assert.equal(calls.length, 1);
  });
});

test("persistent overload stops after three retries", async () => {
  await fixture(Array.from({ length: 4 }, overloaded), async (calls) => {
    await assert.rejects(listBooks(), /service overloaded/);
    assert.equal(calls.length, 4);
  });
});

test("collection cancellation interrupts backoff before another request", async () => {
  await fixture([overloaded()], async (calls) => {
    await assert.rejects(
      withSyncDeadline(50, () => listBooks()),
      /同步主动超时/,
    );
    assert.equal(calls.length, 1);
  });
});
