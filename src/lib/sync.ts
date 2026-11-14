import type { Status, Transform } from "./types.ts";
export function acceptFrame(
  status: Status,
  frame: {
    playhead_generation: number;
    state_revision: number;
    frame_index: number | null;
    frame_pts_us: number | null;
  },
): boolean {
  return (
    frame.playhead_generation === status.playhead_generation &&
    frame.state_revision === status.state_revision &&
    frame.frame_index != null &&
    (status.frame_index == null || frame.frame_index >= status.frame_index) &&
    frame.frame_pts_us != null &&
    (status.frame_pts_us == null || frame.frame_pts_us >= status.frame_pts_us)
  );
}
export function normalize(
  t: Transform,
  width: number,
  height: number,
  changed: keyof Transform,
): Transform {
  const next = { ...t };
  if (next.lock_aspect) {
    const turn = next.rotation_deg % 180 !== 0;
    const w = turn ? height : width,
      h = turn ? width : height;
    if (changed === "height") next.width = Math.round((next.height * w) / h);
    else if (changed === "width")
      next.height = Math.round((next.width * h) / w);
  }
  return next;
}

export function acceptStatus(current: Status, next: Status): boolean {
  return (
    next.state_revision >= current.state_revision &&
    next.playhead_generation >= current.playhead_generation
  );
}
