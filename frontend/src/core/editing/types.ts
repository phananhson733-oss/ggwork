export type Requirements = {
  profile: string;
  instructions: string;
  output_count: number;
  duration_seconds: number;
  aspect_ratio: string;
  language: string;
  review_plan: boolean;
};
export type Source = {
  media_id: string;
  name: string;
  episode: number;
  relative_path: string;
  size_bytes: number;
  state: "selected" | "received" | "verified" | "failed";
  sha256?: string | null;
  duration_seconds?: number | null;
};
export type Manifest = { version: number; grant_id: string; files: Source[] };
export type SourceDirectory = { grant_id: string; relative_path: string };
export type Device = {
  id: string;
  name: string;
  online: boolean;
  ready: boolean;
  revoked: boolean;
  reasons: string[];
  grants: string[];
  platform: string;
  worker_version: string;
};
export type Output = {
  id: string;
  status: string;
  error: string | null;
  access_status: string;
  result: {
    artifact_id: string;
    verified: boolean;
    duration_seconds: number;
    size_bytes: number;
  } | null;
};
export type EditingTask = {
  id: string;
  title: string;
  requirements: Requirements;
  device_id: string | null;
  source_thread_id: string | null;
  parent_task_id: string | null;
  created_at: string;
  updated_at: string;
  status: string;
  stage: string;
  result: string;
  source_manifest: Manifest | null;
  source_directory: SourceDirectory | null;
  manifest_frozen: boolean;
  outputs: Output[];
  requested_count: number;
  completed_count: number;
  preparation_reasons: string[];
  device_status: string;
  access_status: string;
  available_actions: string[];
  plan_confirmed: boolean;
  plan: {
    outputs: {
      output_id: string;
      segments: { media_id: string; start: number; end: number }[];
    }[];
  } | null;
};
export type CreateTask = {
  request_id: string;
  title: string;
  requirements: Requirements;
  device_id?: string;
  source_manifest?: Manifest;
  source_directory?: SourceDirectory;
  parent_task_id?: string;
};
export type TaskPage = {
  items: EditingTask[];
  limit: number;
  offset: number;
  total: number;
  next_offset: number | null;
};
export type Capabilities = {
  profiles: { id: string; available: boolean; reasons: string[] }[];
  skill_enabled: boolean;
  limits: {
    max_sources: number;
    max_outputs: number;
    min_duration_seconds: number;
    max_duration_seconds: number;
  };
};
