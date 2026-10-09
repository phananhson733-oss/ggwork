import type { Source } from "./types";

export function selectedUploadSource(file: File): Source {
  const mediaId = crypto.randomUUID();
  const suffix = /\.(mp4|mov|mkv|m4v|webm)$/i.exec(file.name)?.[0] ?? "";
  return {
    media_id: mediaId,
    name: file.name,
    relative_path: `${mediaId}${suffix}`,
    episode: 0,
    size_bytes: file.size,
    state: "selected",
  };
}
