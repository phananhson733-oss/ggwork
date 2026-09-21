"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAuth } from "@/core/auth/AuthProvider";
import { importPickData, listPickBatches } from "@/core/pick/api";

export function DataImports() {
  const { user } = useAuth();
  const client = useQueryClient();
  const key = ["pick-imports", user?.id];
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => listPickBatches(signal),
    enabled: !!user,
  });
  const [kind, setKind] = useState<"catalog" | "knowledge">("catalog");
  const [files, setFiles] = useState<File[]>([]);
  const [source, setSource] = useState("");
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [error, setError] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const submit = async () => {
    setBusy(true);
    setError("");
    setFeedback("");
    try {
      const batch = await importPickData(kind, files, source);
      setFeedback(`导入完成：${String(batch.validation_json.rows)} 条记录`);
      setFiles([]);
      if (input.current) input.current.value = "";
      await client.invalidateQueries({ queryKey: key });
    } catch (e) {
      setError(e instanceof Error ? e.message : "导入失败");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="mx-auto w-full max-w-4xl space-y-6 p-6">
      <header>
        <h1 className="text-2xl font-semibold">选剧资料</h1>
        <p className="text-muted-foreground mt-2 text-sm">
          剧库用于筛选，知识资料用于解释规则。每次导入保存独立版本。
        </p>
      </header>
      <section className="space-y-4 rounded-xl border p-5">
        <label className="block text-sm">
          资料类型
          <select
            className="bg-background ml-3 rounded-md border p-2"
            value={kind}
            disabled={busy}
            onChange={(event) => {
              setKind(event.target.value as "catalog" | "knowledge");
              setFiles([]);
              if (input.current) input.current.value = "";
            }}
          >
            <option value="catalog">剧库 CSV / JSON</option>
            <option value="knowledge">知识 Markdown</option>
          </select>
        </label>
        <p className="text-muted-foreground text-sm">
          {kind === "catalog"
            ? "每次选择一份完整剧库，必需列：source、source_id、language、title。"
            : "选择本次完整知识集合（最多50份）；成功后成为当前知识版本。"}
        </p>
        <Input
          ref={input}
          aria-label="选择资料文件"
          type="file"
          accept={kind === "catalog" ? ".csv,.json" : ".md"}
          multiple={kind === "knowledge"}
          disabled={busy}
          onChange={(event) => setFiles(Array.from(event.target.files ?? []))}
        />
        <Input
          aria-label="资料来源"
          placeholder="资料来源说明或链接（可选）"
          value={source}
          onChange={(event) => setSource(event.target.value)}
          maxLength={1800}
          disabled={busy}
        />
        <p className="text-muted-foreground text-xs">
          每批最多25MB。导入失败会保留上一份可用资料；文件不会自动同步回原知识库。
        </p>
        <Button
          onClick={() => void submit()}
          disabled={busy || files.length === 0}
        >
          {busy ? "正在校验与导入…" : "导入资料"}
        </Button>
        {feedback && (
          <p role="status" className="text-sm">
            {feedback}
          </p>
        )}
        {error && (
          <p role="alert" className="text-sm text-red-600">
            {error}
          </p>
        )}
      </section>
      <section>
        <h2 className="mb-3 font-semibold">导入记录</h2>
        {query.isPending && <p role="status">正在读取…</p>}
        {query.error && <p role="alert">{query.error.message}</p>}
        {query.data?.length === 0 && (
          <p className="text-muted-foreground">还没有导入资料。</p>
        )}
        <ul className="divide-y rounded-xl border">
          {query.data?.map((batch) => (
            <li
              key={batch.id}
              className="flex justify-between gap-4 p-4 text-sm"
            >
              <span>
                {batch.kind === "catalog" ? "剧库" : "知识"} ·{" "}
                {String(batch.validation_json.rows ?? 0)} 条 ·{" "}
                {batch.status === "published" ? "可用" : "未发布"}
              </span>
              <time className="text-muted-foreground">
                {new Date(batch.created_at).toLocaleString()}
              </time>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
