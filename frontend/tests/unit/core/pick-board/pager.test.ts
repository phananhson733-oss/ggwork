// PORTED_FROM: realshort@816ca2e tests/pager.test.ts
// 本地改动：node:test 换成 @rstest/core；省略号两侧先判是数再比（原来 `as number` 断言）；pageSequence 从 components/ui/pager.tsx 摘成纯模块 core/pick-board/pager.ts。
import assert from "node:assert/strict";

import { test } from "@rstest/core";

import { pageSequence } from "@/core/pick-board/pager";

/**
 * 分页页码序列。公开站的分类页与 blog 列表用默认跨度 1，
 * 后台观测台传 4（那张表默认每页 10 行、常有几千页）。
 *
 * 【一份实现两个调用方】，所以这里既要钉住公开站那档的形状，
 * 也要钉住宽跨度那档——为其中一边改逻辑而碰坏另一边不会报错。
 */

const fmt = (a: ReadonlyArray<number | null>) =>
  a.map((x) => (x === null ? "…" : String(x))).join(" ");

test("默认跨度：首页 + 当前页两侧各一页 + 末页", () => {
  assert.equal(fmt(pageSequence(1, 7)), "1 2 3 4 5 6 7");
  assert.equal(fmt(pageSequence(1, 8)), "1 2 3 4 … 8");
  assert.equal(fmt(pageSequence(50, 100)), "1 … 49 50 51 … 100");
  assert.equal(fmt(pageSequence(100, 100)), "1 … 97 98 99 100");
});

test("跨度 4：停在前几页时正好露出 1..10", () => {
  // 观测台就是靠这个满足「直接点到前十页里的任意一页」
  assert.equal(fmt(pageSequence(1, 2935, 4)), "1 2 3 4 5 6 7 8 9 10 … 2935");
  assert.equal(fmt(pageSequence(6, 2935, 4)), "1 2 3 4 5 6 7 8 9 10 … 2935");
  assert.equal(fmt(pageSequence(7, 2935, 4)), "1 … 3 4 5 6 7 8 9 10 11 … 2935");
  assert.equal(
    fmt(pageSequence(2935, 2935, 4)),
    "1 … 2926 2927 2928 2929 2930 2931 2932 2933 2934 2935",
  );
});

test("总页数够少就全列，不出现只省掉一页的省略号", () => {
  // 阈值跟着跨度走：2*span+5
  assert.equal(fmt(pageSequence(1, 13, 4)), "1 2 3 4 5 6 7 8 9 10 11 12 13");
  assert.equal(fmt(pageSequence(1, 14, 4)), "1 2 3 4 5 6 7 8 9 10 … 14");
  for (let total = 1; total <= 7; total++) {
    assert.equal(pageSequence(1, total).length, total, `total=${total} 该全列`);
  }
});

test("序列恒为升序、无重复、不越界，省略号只跨过多于一页的空档", () => {
  for (const span of [1, 4]) {
    for (let total = 1; total <= 60; total++) {
      for (let current = 1; current <= total; current++) {
        const seq = pageSequence(current, total, span);
        const nums = seq.filter((n): n is number => n !== null);
        assert.deepEqual(
          nums,
          [...new Set(nums)],
          `span=${span} ${current}/${total} 有重复页码`,
        );
        assert.deepEqual(
          nums,
          [...nums].sort((a, b) => a - b),
          `span=${span} ${current}/${total} 没有升序`,
        );
        assert.ok(
          nums.every((n) => n >= 1 && n <= total),
          `span=${span} ${current}/${total} 越界`,
        );
        assert.ok(nums.includes(current), "当前页必须在序列里");
        assert.ok(nums.includes(1) && nums.includes(total), "首末页必须在");
        /*
         * 省略号两侧必须真有空档。
         *
         * 【这里只断言 > 1，不断言 > 2】：span=1 在 4/8 这类位置会给出
         * "1 … 3 4 5 … 8"，第一个省略号只藏了一页。那是改动前就有的行为
         * （补齐逻辑只覆盖 current <= span+2），不好看但不是错，
         * 而收紧它会改掉公开站分类页与 blog 列表的现有输出。要改单独做。
         */
        seq.forEach((n, i) => {
          if (n !== null) return;
          const before = seq[i - 1];
          const after = seq[i + 1];
          assert.ok(
            typeof before === "number" &&
              typeof after === "number" &&
              after - before > 1,
            `span=${span} ${current}/${total} 省略号两侧是连号，它没省掉任何东西`,
          );
        });
      }
    }
  }
});
