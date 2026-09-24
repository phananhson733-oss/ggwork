import assert from "node:assert/strict";
import { readdirSync, statSync } from "node:fs";
import path from "node:path";

import { test } from "@rstest/core";

import { FRONTEND_ROOT, readSource } from "./ported-source";

const CORE_DIR = "src/core/pick-board";

function walk(relativeDir: string): string[] {
  const absolute = path.join(FRONTEND_ROOT, relativeDir);
  return readdirSync(absolute).flatMap((name) => {
    const child = `${relativeDir}/${name}`;
    if (statSync(path.join(absolute, name)).isDirectory()) return walk(child);
    return /\.(ts|tsx)$/.test(name) ? [child] : [];
  });
}

test("core/pick-board 是纯模块：不 import server-only、src/server、next、react 或 node 内置模块", () => {
  const files = walk(CORE_DIR);
  assert.ok(files.length >= 15, `只找到 ${files.length} 个文件`);
  for (const file of files) {
    const source = readSource(file);
    for (const banned of [
      /from "server-only"|import "server-only"/,
      /from "@\/server\//,
      /from "next\//,
      /from "react"/,
      /from "node:/,
      /process\.env/,
    ])
      assert.doesNotMatch(source, banned, `${file} 引了 ${banned}`);
  }
});

test("静态 IN_USE 只作类型参照：core/pick-board 之外没有任何代码引用它（在用剧场随版本规则走）", () => {
  const offenders = walk("src")
    .filter((file) => !file.startsWith(`${CORE_DIR}/`))
    .filter((file) => /\bIN_USE\b/.test(readSource(file)));
  assert.deepEqual(offenders, []);
});

test("移植文件都带 PORTED_FROM 来源注释，记着 RealShort 的 commit", () => {
  const ported = walk(CORE_DIR).filter((file) =>
    readSource(file).includes("PORTED_FROM"),
  );
  for (const file of ported)
    assert.match(
      readSource(file),
      /^\/\/ PORTED_FROM: realshort@816ca2e src\/\S+/m,
      file,
    );
  assert.ok(ported.length >= 13, `只有 ${ported.length} 个文件带来源注释`);
});
