/**
 * Client boundaries for the pick-board source contracts (contracts.test;
 * plan TR-25, registry kept by TR-25b).
 *
 * - A client component is a file whose directive prologue says "use client",
 *   read on the syntax tree, so comments of either kind may come first.
 * - What a module reaches at run time: its local imports, resolved the way
 *   TypeScript resolves them with the frontend's tsconfig (the @/ alias, index
 *   files, .js specifiers), followed through every module that is not a
 *   client component and stopping at each one that is. Only whole-statement
 *   `import type` / `export type` are skipped: under verbatimModuleSyntax an
 *   inline `{ type X }` still loads the module. A local import that does not
 *   resolve is reported, never taken as clean.
 *
 * Everything reads through a SourceHost, so each check can be shown red on
 * sources that exist only in a test.
 */
import path from "node:path";

import ts from "typescript";

export interface SourceHost {
  /** The text of a file, by its path relative to the frontend root; undefined when there is none. */
  read(file: string): string | undefined;
}

export interface Boundary {
  readonly root: string;
  readonly host: SourceHost;
  readonly options: ts.CompilerOptions;
}

/** The frontend's own compiler options (paths, module resolution), without listing its files. */
export function compilerOptions(root: string): ts.CompilerOptions {
  const { config } = ts.readConfigFile(
    path.join(root, "tsconfig.json"),
    (file) => ts.sys.readFile(file),
  );
  return ts.convertCompilerOptionsFromJson(
    (config as { compilerOptions?: unknown }).compilerOptions,
    root,
  ).options;
}

function parse(file: string, text: string): ts.SourceFile {
  const kind = /\.[jt]sx$/.test(file) ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  return ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, kind);
}

/** Whether the directive prologue (the leading string statements) holds "use client". */
export function hasUseClient(file: string, text: string): boolean {
  for (const statement of parse(file, text).statements) {
    if (
      !ts.isExpressionStatement(statement) ||
      !ts.isStringLiteral(statement.expression)
    )
      return false;
    if (statement.expression.text === "use client") return true;
  }
  return false;
}

/** A radar component: under one of `roots`, named obs-*, or inside an obs/ folder. */
export function isRadarComponent(
  file: string,
  roots: readonly string[],
): boolean {
  const root = roots.find((dir) => file.startsWith(`${dir}/`));
  if (root === undefined) return false;
  const parts = file.slice(root.length + 1).split("/");
  const name = parts[parts.length - 1] ?? "";
  return (
    /\.[jt]sx?$/.test(name) &&
    (name.startsWith("obs-") || parts.slice(0, -1).includes("obs"))
  );
}

/** What is wrong with a registry: unlisted client components, entries that are not one, missing reasons. */
export function registryProblems(
  files: readonly string[],
  boundary: Pick<Boundary, "host">,
  roots: readonly string[],
  registry: Readonly<Record<string, string>>,
): string[] {
  const clients = files.filter(
    (file) =>
      isRadarComponent(file, roots) &&
      hasUseClient(file, boundary.host.read(file) ?? ""),
  );
  return [
    ...clients
      .filter((file) => !(file in registry))
      .map((file) => `${file}: a client component the registry does not list`),
    ...Object.keys(registry)
      .filter((file) => !clients.includes(file))
      .map((file) => `${file}: listed, but not a radar client component`),
    ...Object.entries(registry)
      .filter(([, why]) => why.trim().length < 10)
      .map(([file]) => `${file}: no reason given`),
  ];
}

const isLocal = (spec: string) => spec.startsWith("@/") || spec.startsWith(".");

/** The module specifiers that load code at run time. */
function runtimeSpecifiers(file: ts.SourceFile): string[] {
  const found: string[] = [];
  const visit = (node: ts.Node) => {
    if (
      ts.isImportDeclaration(node) &&
      ts.isStringLiteral(node.moduleSpecifier) &&
      node.importClause?.phaseModifier !== ts.SyntaxKind.TypeKeyword
    )
      found.push(node.moduleSpecifier.text);
    else if (
      ts.isExportDeclaration(node) &&
      node.moduleSpecifier !== undefined &&
      ts.isStringLiteral(node.moduleSpecifier) &&
      !node.isTypeOnly
    )
      found.push(node.moduleSpecifier.text);
    else if (
      ts.isCallExpression(node) &&
      (node.expression.kind === ts.SyntaxKind.ImportKeyword ||
        (ts.isIdentifier(node.expression) &&
          node.expression.text === "require")) &&
      node.arguments[0] !== undefined &&
      ts.isStringLiteral(node.arguments[0])
    )
      found.push(node.arguments[0].text);
    ts.forEachChild(node, visit);
  };
  visit(file);
  return found;
}

function resolveLocal(
  from: string,
  spec: string,
  { root, host, options }: Boundary,
): string | null {
  const key = (absolute: string) =>
    path.relative(root, absolute).split(path.sep).join("/");
  const resolutionHost: ts.ModuleResolutionHost = {
    fileExists: (absolute) => host.read(key(absolute)) !== undefined,
    readFile: (absolute) => host.read(key(absolute)),
  };
  const resolved = ts.resolveModuleName(
    spec,
    path.join(root, from),
    options,
    resolutionHost,
  ).resolvedModule;
  if (resolved === undefined || resolved.isExternalLibraryImport) return null;
  const file = key(resolved.resolvedFileName);
  return file.startsWith("src/") ? file : null;
}

export interface Reach {
  /** Client components reachable from the entry, sorted. */
  readonly clients: string[];
  /** Local imports that did not resolve, as "file: specifier". */
  readonly unresolved: string[];
}

/** The client components `entry` reaches at run time through local modules. */
export function reachableClients(entry: string, boundary: Boundary): Reach {
  const seen = new Set<string>([entry]);
  const pending = [entry];
  const clients: string[] = [];
  const unresolved: string[] = [];
  for (let file = pending.shift(); file !== undefined; file = pending.shift()) {
    const text = boundary.host.read(file) ?? "";
    for (const spec of runtimeSpecifiers(parse(file, text)).filter(isLocal)) {
      const target = resolveLocal(file, spec, boundary);
      if (target === null) {
        if (!spec.endsWith(".css")) unresolved.push(`${file}: ${spec}`);
        continue;
      }
      if (seen.has(target)) continue;
      seen.add(target);
      if (hasUseClient(target, boundary.host.read(target) ?? ""))
        clients.push(target);
      else pending.push(target);
    }
  }
  return { clients: clients.sort(), unresolved };
}

/** Views that must stay server components and reach only registered client components. */
export function viewProblems(
  views: readonly string[],
  boundary: Boundary,
  registry: Readonly<Record<string, string>>,
): string[] {
  return views.flatMap((view) => {
    const { clients, unresolved } = reachableClients(view, boundary);
    return [
      ...(hasUseClient(view, boundary.host.read(view) ?? "")
        ? [`${view}: "use client"`]
        : []),
      ...clients
        .filter((client) => !(client in registry))
        .map((client) => `${view}: reaches ${client}`),
      ...unresolved.map((where) => `${view}: cannot resolve ${where}`),
    ];
  });
}
