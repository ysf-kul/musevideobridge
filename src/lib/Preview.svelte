<script lang="ts">
  import { bridge } from "./bridge";
  import { t, type Locale } from "./i18n";
  import type { Media, Transform } from "./types";
  let {
    media,
    index,
    digest,
    transform,
    locale,
    label,
    blank = false,
  }: {
    media: Media | null;
    index: number | null;
    digest: string | null;
    transform: Transform;
    locale: Locale;
    label: string;
    blank?: boolean;
  } = $props();
  let canvas: HTMLCanvasElement;
  let unavailable = $state(false);
  let bitmap: ImageBitmap | null = null;
  let generation = 0;
  let loading = false;
  let wanted = "";
  let drawn = "";
  let alive = true;
  function draw() {
    if (!canvas) return;
    const ctx = canvas.getContext("2d")!;
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, 320, 240);
    if (!bitmap || blank) return;
    if (!media?.artifact) {
      ctx.drawImage(
        bitmap,
        (320 - bitmap.width) / 2,
        (240 - bitmap.height) / 2,
        bitmap.width,
        bitmap.height,
      );
      return;
    }
    ctx.save();
    ctx.imageSmoothingEnabled = false;
    ctx.translate(
      transform.x + transform.width / 2,
      transform.y + transform.height / 2,
    );
    ctx.rotate((transform.rotation_deg * Math.PI) / 180);
    const turn = transform.rotation_deg % 180 !== 0;
    ctx.drawImage(
      bitmap,
      -(turn ? transform.height : transform.width) / 2,
      -(turn ? transform.width : transform.height) / 2,
      turn ? transform.height : transform.width,
      turn ? transform.width : transform.height,
    );
    ctx.restore();
  }
  async function load() {
    if (loading || !media || blank) return;
    loading = true;
    const current = wanted;
    const token = generation;
    try {
      const prepared = media.artifact;
      const data =
        prepared && digest === prepared.sha256 && index != null
          ? await bridge.call<ArrayBuffer | number[]>("frame", {
              index,
              digest,
            })
          : await bridge.call<ArrayBuffer | number[]>("source_preview");
      const blob = new Blob(
        [data instanceof ArrayBuffer ? data : new Uint8Array(data)],
        { type: "image/jpeg" },
      );
      const next = await createImageBitmap(blob);
      if (token !== generation || !alive) {
        next.close();
        return;
      }
      bitmap?.close();
      bitmap = next;
      unavailable = false;
      drawn = current;
      draw();
    } catch {
      if (token !== generation || !alive) return;
      unavailable = true;
      bitmap?.close();
      bitmap = null;
      drawn = current;
      draw();
    } finally {
      loading = false;
      if (alive && !blank && wanted !== drawn) void load();
    }
  }
  $effect(() => {
    const key = media
      ? `${media.selection_id}:${media.artifact?.sha256 ?? "source"}:${digest}:${index}`
      : "";
    if (key !== wanted) {
      generation++;
      wanted = key;
      if (!media) {
        generation++;
        bitmap?.close();
        bitmap = null;
        drawn = "";
        draw();
      } else void load();
    }
  });
  $effect(() => {
    transform;
    blank;
    if (!blank && wanted !== drawn) void load();
    canvas;
    draw();
  });
  $effect(() => () => {
    alive = false;
    generation++;
    bitmap?.close();
  });
</script>

<figure class="preview" data-frame-index={blank ? undefined : index}>
  <canvas
    bind:this={canvas}
    width="320"
    height="240"
    aria-label={t(locale, "preview.canvas")}
  ></canvas>
  <figcaption>
    <strong>{unavailable ? t(locale, "preview.unavailable") : label}</strong
    ><span>{t(locale, "preview.notCapture")}</span>
  </figcaption>
</figure>
