export interface Settings {
  version: number;
  language: "tr" | "en";
  theme: "system" | "light" | "dark";
  qscale: number;
  fps: number;
  crop: boolean;
  last_port: string;
  ffmpeg: string;
  ffprobe: string;
}
export interface Rate {
  numerator: number;
  denominator: number;
}
export interface Metadata {
  width: number;
  height: number;
  duration_us: number | null;
  original_fps: Rate | null;
  rotation_deg: number;
}
export interface Header {
  width: number;
  height: number;
  frames: number;
  duration_us: number;
  file_bytes: number;
  fps: Rate;
}
export interface Artifact {
  sha256: string;
  header: Header;
}
export interface Media {
  selection_id: string;
  filename: string;
  metadata: Metadata;
  artifact: Artifact | null;
  estimate: { worst_case_bytes: number | null };
}
export interface Transform {
  x: number;
  y: number;
  width: number;
  height: number;
  rotation_deg: number;
  lock_aspect: boolean;
}
export interface Status {
  state: string;
  frame_index: number | null;
  frame_count: number;
  frame_pts_us: number | null;
  position_us: number;
  actual_fps: number | null;
  target_fps: Rate;
  content_sha256: string | null;
  sd_mounted: boolean;
  transform: Transform;
  state_revision: number;
  playhead_generation: number;
  dropped_frames: number;
  hardware: Record<string, number | string | null>;
  error: string | null;
}
export interface Device {
  connection: string;
  port?: string;
  received_at_ms?: number;
  session?: number;
  status?: Status;
  hello?: {
    firmware_version: string;
    configured_board: string;
    hardware: { simulator: boolean };
  };
  error?: Failure;
}
export interface Failure {
  code: string;
  details: string;
}
export interface Job {
  running: boolean;
  kind: string;
  stage: string;
  completed?: number;
  total?: number;
  error?: Failure;
  result?: unknown;
}
export interface Snapshot {
  settings: Settings;
  media: Media | null;
  device: Device;
  job: Job | null;
  startup_error: Failure | null;
}
export const defaults: Settings = {
  version: 1,
  language: "tr",
  theme: "system",
  qscale: 8,
  fps: 25,
  crop: false,
  last_port: "",
  ffmpeg: "ffmpeg",
  ffprobe: "ffprobe",
};
