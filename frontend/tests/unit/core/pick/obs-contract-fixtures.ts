/**
 * Reading the observation radar's shared contract from the frontend's tests
 * (plan TR-33, TR-16): the fixtures in
 * customizations/pick-workbench/tests/fixtures/obs_contract/, the contract
 * document and the Python modules that hold the wording. Python's
 * tests/observe/test_contract.py reads the same files.
 */
import { readFileSync } from "node:fs";
import path from "node:path";

import type { z } from "zod";

export const REPO_ROOT = path.resolve(__dirname, "../../../../..");
const FIXTURES = "customizations/pick-workbench/tests/fixtures/obs_contract";

/** What a refused case says it is refused for (contract section 15); pydantic's names. */
export type FixtureError = Readonly<{
  type: string;
  loc?: string;
  msg?: string;
}>;

export type ContractCase = Readonly<{
  name: string;
  value: unknown;
  why?: string;
  error?: FixtureError;
}>;

export type ModelFixture = Readonly<{
  about: readonly string[];
  model?: string;
  valid: readonly ContractCase[];
  invalid: readonly ContractCase[];
}>;

export function repoText(relative: string): string {
  return readFileSync(path.join(REPO_ROOT, relative), "utf8");
}

export function obsFixture<T = ModelFixture>(name: string): T {
  return JSON.parse(repoText(`${FIXTURES}/${name}`)) as T;
}

export function validCase(fixture: ModelFixture, name: string): unknown {
  const found = fixture.valid.find((c) => c.name === name);
  if (!found) throw new Error(`no valid case ${name}`);
  return found.value;
}

/** pydantic's loc without the parts zod has no counterpart for (union branches, the [key] of a dict key). */
function locParts(loc: string): string[] {
  return loc
    .split(".")
    .filter((part) => part !== "[key]" && !part.startsWith("constrained-"));
}

function endsWith(path: readonly (string | number)[], parts: string[]) {
  const tail = path.slice(path.length - parts.length).map(String);
  return parts.length <= path.length && tail.every((p, i) => p === parts[i]);
}

/** One zod issue against a case's "error": the same field, key or message. */
export function issueMatches(issue: z.ZodIssue, error: FixtureError): boolean {
  if (error.msg !== undefined) return issue.message.includes(error.msg);
  if (error.loc === undefined) return true;
  const parts = locParts(error.loc);
  if (error.type === "extra_forbidden")
    return (
      issue.code === "unrecognized_keys" &&
      issue.keys.includes(parts.at(-1) ?? "") &&
      endsWith(issue.path, parts.slice(0, -1))
    );
  return endsWith(issue.path, parts);
}

/** Why a refused case's issues do not pin its rule: null when one of them matches. */
export function unmatchedReason(
  issues: readonly z.ZodIssue[],
  error: FixtureError | undefined,
): string | null {
  if (error === undefined) return "the case names no error";
  if (issues.length === 0) return "accepted";
  if (issues.some((issue) => issueMatches(issue, error))) return null;
  return `refused for another reason: ${JSON.stringify(
    issues.map((i) => [i.code, i.path.join("."), i.message]),
  )}`;
}

/** `#### 枚举 \`NAME\`` followed by `取值：\`a\`、\`b\`` in the contract document. */
export function docEnum(doc: string, name: string): string[] {
  const found = new RegExp(`#### 枚举 \`${name}\`\\n取值：(.+)`).exec(doc);
  if (!found?.[1]) throw new Error(`no enum ${name} in the contract document`);
  return [...found[1].matchAll(/`([^`]+)`/g)].map((m) => m[1] ?? "");
}

/** The field column of `#### \`Model\``'s table in the contract document. */
export function docFields(doc: string, model: string): string[] {
  const start = doc.indexOf(`#### \`${model}\`\n`);
  if (start < 0)
    throw new Error(`no table for ${model} in the contract document`);
  const table = doc.slice(start).split("\n").slice(2);
  const rows = table.slice(
    2,
    table.findIndex((line, i) => i > 0 && !line),
  );
  return rows.map((row) => /^\| `([^`]+)` \|/.exec(row)?.[1] ?? row);
}
