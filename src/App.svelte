<script lang="ts">
  import { onMount } from "svelte";
  import Icon from "./lib/Icon.svelte";
  import Preview from "./lib/Preview.svelte";
  import { bridge, available } from "./lib/bridge";
  import { t, errorText, bytes, number, time, type Key } from "./lib/i18n";
  import {
    defaults,
    type Settings,
    type Media,
    type Device,
    type Job,
    type Failure,
    type Snapshot,
    type Transform,
    type Status,
  } from "./lib/types";
  import { acceptFrame, acceptStatus, normalize } from "./lib/sync";
  let settings = $state<Settings>({ ...defaults }),
    media = $state<Media | null>(null),
    device = $state<Device>({ connection: "disconnected" }),
    job = $state<Job | null>(null),
    error = $state<Failure | null>(null),
    ready = $state(false),
    working = $state(false),
    ports = $state<string[]>([]),
    port = $state(""),
    card = $state<{
      path: string;
      filesystem: string;
      available_bytes: number;
      fat32_compatible: boolean;
    } | null>(null),
    estimate = $state<number | null>(null),
    now = $state(Date.now());
  let transform = $state<Transform>({
      x: 0,
      y: 0,
      width: 320,
      height: 240,
      rotation_deg: 0,
      lock_aspect: true,
    }),
    pending = $state(false),
    sentVersion = 0,
    version = 0,
    revision = 0,
    sending = false,
    timer: ReturnType<typeof setTimeout>,
    seek = $state(0),
    requestedFps = $state(defaults.fps),
    toolOpen = $state(false),
    fpsPending = $state(false),
    seekPending = $state(false);
  let seekVersion = 0,
    sentSeekVersion = 0,
    seekRevision = 0,
    seekGeneration = 0,
    seekSending = false;
  let seekTimer: ReturnType<typeof setTimeout>;
  let fpsRevision = 0,
    fpsVersion = 0;
  const locale = $derived(settings.language);
  const status = $derived(device.status);
  const transferring = $derived(job?.running && job.kind === "upload");
  const fresh = $derived(
    device.connection === "connected" &&
      !!device.received_at_ms &&
      now - device.received_at_ms < 3500,
  );
  const live = $derived(
    fresh &&
      !transferring &&
      !!status &&
      ["playing", "paused", "stopped", "ended"].includes(status.state),
  );
  const busy = $derived(Boolean(job?.running) || working);
  const matching = $derived(
    !!media?.artifact && media.artifact.sha256 === status?.content_sha256,
  );
  const previewIndex = $derived(matching ? (status?.frame_index ?? null) : 0);
  const previewDigest = $derived(media?.artifact?.sha256 ?? null);
  const previewLabel = $derived(
    !fresh && device.status
      ? t(locale, "preview.stale")
      : pending
        ? t(locale, "preview.pending")
        : fresh && matching
          ? t(locale, "preview.device")
          : t(locale, "preview.local"),
  );
  function fail(e: unknown) {
    error =
      typeof e === "object" && e !== null && "code" in e
        ? (e as Failure)
        : { code: "generic", details: String(e) };
  }
  async function action(fn: () => Promise<unknown>) {
    working = true;
    error = null;
    try {
      await fn();
    } catch (e) {
      fail(e);
    } finally {
      working = false;
    }
  }
  let saveQueue: Promise<unknown> = Promise.resolve();
  async function save() {
    const next = $state.snapshot(settings);
    const request = saveQueue.then(() =>
      bridge.call("save_settings", { settings: next }),
    );
    saveQueue = request.catch(() => undefined);
    try {
      await request;
    } catch (e) {
      fail(e);
    }
  }
  function options() {
    return {
      fps: { numerator: settings.fps, denominator: 1 },
      qscale: settings.qscale,
      fit: settings.crop ? "Crop" : "Contain",
    };
  }
  async function updateEstimate() {
    void save();
    if (!media) return;
    try {
      estimate = (
        await bridge.call<{ worst_case_bytes: number | null }>("estimate", {
          options: options(),
        })
      ).worst_case_bytes;
    } catch (e) {
      fail(e);
    }
  }
  function adopt(next: Device) {
    const prior = device.session;
    if (next.status && next.session != null && prior != null) {
      if (next.session !== prior && !next.hello) return;
      if (
        next.session === prior &&
        status &&
        !acceptStatus(status, next.status)
      )
        return;
      if (
        next.session === prior &&
        status &&
        next.status.state_revision === status.state_revision &&
        next.status.playhead_generation === status.playhead_generation &&
        (status.frame_index ?? -1) > (next.status.frame_index ?? -1)
      ) {
        next = {
          ...next,
          status: {
            ...next.status,
            frame_index: status.frame_index,
            frame_pts_us: status.frame_pts_us,
          },
        };
      }
    }
    device = { ...device, ...next };
    if (next.error) fail(next.error);
    if (next.connection && next.connection !== "connected") {
      pending = false;
      fpsPending = false;
      seekPending = false;
      seekVersion++;
      clearTimeout(seekTimer);
      version++;
      clearTimeout(timer);
    }
    if (next.session != null && prior !== next.session) {
      pending = false;
      version++;
      revision = 0;
    }
    if (next.status) {
      if (
        !pending ||
        (revision > 0 &&
          next.status.state_revision >= revision &&
          sentVersion === version)
      ) {
        transform = { ...next.status.transform };
        pending = false;
        revision = 0;
      }
      if (
        !seekPending ||
        (seekRevision > 0 &&
          next.status.state_revision >= seekRevision &&
          next.status.playhead_generation >= seekGeneration &&
          sentSeekVersion === seekVersion)
      ) {
        seek = next.status.position_us;
        seekPending = false;
        seekRevision = 0;
      }
      if (
        !fpsPending ||
        (fpsRevision > 0 && next.status.state_revision >= fpsRevision)
      ) {
        requestedFps =
          next.status.target_fps.numerator / next.status.target_fps.denominator;
        fpsPending = false;
        fpsRevision = 0;
      }
      if (next.status.error) fail({ code: next.status.error, details: "" });
    }
  }
  async function choose() {
    await action(async () => {
      const selected = await bridge.call<Media | null>("select_video");
      if (selected) {
        media = selected;
        estimate = selected.estimate.worst_case_bytes;
        card = null;
        transform = {
          x: 0,
          y: 0,
          width: 320,
          height: 240,
          rotation_deg: 0,
          lock_aspect: true,
        };
      }
    });
  }
  async function refresh() {
    try {
      ports = await bridge.call<string[]>("ports");
      if (!ports.includes(port))
        port = ports.includes(settings.last_port)
          ? settings.last_port
          : (ports[0] ?? "");
    } catch (e) {
      fail(e);
    }
  }
  async function connect() {
    error = null;
    settings.last_port = port;
    void save();
    try {
      await bridge.call("connect", { port });
    } catch (e) {
      fail(e);
    }
  }
  async function control(method: string, extra: Record<string, unknown> = {}) {
    await action(async () => {
      await bridge.call("command", { value: { method, ...extra } });
    });
  }
  function change(key: keyof Transform, value: number | boolean) {
    if (
      typeof value === "number" &&
      (!Number.isFinite(value) || !Number.isInteger(value))
    )
      return;
    transform = normalize(
      { ...transform, [key]: value },
      media?.artifact?.header.width ?? 320,
      media?.artifact?.header.height ?? 240,
      key,
    );
    pending = true;
    version++;
    clearTimeout(timer);
    timer = setTimeout(() => void apply(), 100);
  }
  async function apply() {
    if (sending || !live || !matching) return;
    sending = true;
    const sent = version;
    sentVersion = sent;
    const requested = $state.snapshot(transform);
    try {
      const ack = await bridge.call<{ state_revision: number }>("command", {
        value: { method: "set_transform", ...requested },
      });
      if (sent === version) revision = ack.state_revision;
    } catch (e) {
      fail(e);
      if (sent === version) {
        pending = false;
        if (status) transform = { ...status.transform };
      }
    } finally {
      sending = false;
      if (sent !== version) void apply();
    }
  }
  function rotate(angle: number) {
    const next = { ...transform };
    if (next.lock_aspect && angle % 180 !== next.rotation_deg % 180) {
      const cx = next.x + next.width / 2,
        cy = next.y + next.height / 2;
      [next.width, next.height] = [next.height, next.width];
      next.x = Math.round(cx - next.width / 2);
      next.y = Math.round(cy - next.height / 2);
    }
    next.rotation_deg = angle;
    transform = next;
    pending = true;
    version++;
    clearTimeout(timer);
    timer = setTimeout(() => void apply(), 100);
  }
  function scale(value: number) {
    const h = media?.artifact?.header;
    if (!h || !Number.isFinite(value) || value <= 0) return;
    const turn = transform.rotation_deg % 180 !== 0;
    transform = {
      ...transform,
      width: Math.round((turn ? h.height : h.width) * value),
      height: Math.round((turn ? h.width : h.height) * value),
    };
    pending = true;
    version++;
    clearTimeout(timer);
    timer = setTimeout(() => void apply(), 100);
  }
  function seekChanged() {
    seekPending = true;
    seekRevision = 0;
    seekVersion++;
    clearTimeout(seekTimer);
    seekTimer = setTimeout(() => void applySeek(), 100);
  }
  async function applySeek() {
    if (seekSending || !live || !matching) return;
    seekSending = true;
    const sent = seekVersion;
    sentSeekVersion = sent;
    const requested = Math.round(seek);
    try {
      const ack = await bridge.call<{
        state_revision: number;
        playhead_generation: number;
      }>("command", { value: { method: "seek", position_us: requested } });
      if (sent === seekVersion) {
        seekRevision = ack.state_revision;
        seekGeneration = ack.playhead_generation;
      }
    } catch (e) {
      fail(e);
      if (sent === seekVersion) {
        seekPending = false;
        seek = status?.position_us ?? 0;
      }
    } finally {
      seekSending = false;
      if (sent !== seekVersion) void applySeek();
    }
  }
  async function applyFps() {
    const sent = ++fpsVersion;
    const requested = requestedFps;
    fpsPending = true;
    fpsRevision = 0;
    try {
      const ack = await bridge.call<{ state_revision: number }>("command", {
        value: { method: "set_fps", numerator: requested, denominator: 1 },
      });
      if (sent === fpsVersion) fpsRevision = ack.state_revision;
    } catch (e) {
      fail(e);
      if (sent === fpsVersion) {
        fpsPending = false;
        if (status)
          requestedFps =
            status.target_fps.numerator / status.target_fps.denominator;
      }
    }
  }
  function reset() {
    const h = media?.artifact?.header;
    if (!h) return;
    transform = {
      x: Math.floor((320 - h.width) / 2),
      y: Math.floor((240 - h.height) / 2),
      width: h.width,
      height: h.height,
      rotation_deg: 0,
      lock_aspect: true,
    };
    pending = true;
    version++;
    void apply();
  }
  async function chooseCard() {
    await action(async () => {
      const result = await bridge.call<
        | [
            string,
            {
              filesystem: string;
              available_bytes: number;
              fat32_compatible: boolean;
            },
          ]
        | null
      >("select_card");
      if (result) card = { path: result[0], ...result[1] };
    });
  }
  const stateLabels: Record<string, Key> = {
    idle: "state.idle",
    loading: "state.loading",
    playing: "state.playing",
    paused: "state.paused",
    stopped: "state.stopped",
    ended: "state.ended",
    error: "state.error",
  };
  const jobs: Record<string, Key> = {
    starting: "job.starting",
    converting: "job.converting",
    verifying: "job.verifying",
    copying: "job.copying",
    uploading: "job.uploading",
    resuming_upload: "job.resuming_upload",
    verifying_upload: "job.verifying_upload",
    complete: "job.complete",
    cancelled: "job.cancelled",
    error: "job.error",
  };
  const telemetry: [Key, string, string][] = [
    ["monitor.cpu", "cpu_utilization_percent", " %"],
    ["monitor.cpu_mhz", "cpu_mhz", " MHz"],
    ["monitor.temperature", "temperature_c", " °C"],
    ["monitor.heap", "free_heap_bytes", "bytes"],
    ["monitor.min_heap", "minimum_free_heap_bytes", "bytes"],
    ["monitor.flash", "flash_bytes", "bytes"],
    ["monitor.sd", "sd_total_bytes", "bytes"],
    ["monitor.sd_free", "sd_free_bytes", "bytes"],
    ["monitor.rssi", "wifi_rssi_dbm", " dBm"],
    ["monitor.uptime", "uptime_us", "time"],
  ];
  function metric(key: string, unit: string) {
    const value = fresh ? status?.hardware?.[key] : null;
    return typeof value === "number"
      ? unit === "bytes"
        ? bytes(locale, value)
        : unit === "time"
          ? time(value)
          : number(locale, value, unit)
      : t(locale, "telemetry.unavailable");
  }
  onMount(() => {
    let alive = true;
    const unlisten: (() => void)[] = [];
    const clock = setInterval(() => (now = Date.now()), 1000);
    if (!available()) {
      error = { code: "desktop_required", details: "" };
      return () => clearInterval(clock);
    }
    void (async () => {
      try {
        for (const name of [
          "device",
          "frame",
          "media",
          "job",
          "device_error",
        ]) {
          const stop = await bridge.on<unknown>(name, (payload) => {
            if (!alive) return;
            if (name === "device") adopt(payload as Device);
            else if (name === "media") {
              media = payload as Media;
              if (!live && media?.artifact) {
                const h = media.artifact.header;
                transform = {
                  x: Math.floor((320 - h.width) / 2),
                  y: Math.floor((240 - h.height) / 2),
                  width: h.width,
                  height: h.height,
                  rotation_deg: 0,
                  lock_aspect: true,
                };
              }
            } else if (name === "job") {
              job = payload as Job;
              if (job.error) error = job.error;
            } else if (name === "device_error")
              fail({ code: (payload as { code: string }).code, details: "" });
            else {
              const f = payload as {
                frame: Status;
                session: number;
                received_at_ms: number;
              };
              if (
                status &&
                f.session === device.session &&
                f.frame.content_sha256 === status.content_sha256 &&
                acceptFrame(status, f.frame)
              ) {
                device = {
                  ...device,
                  received_at_ms: f.received_at_ms,
                  status: {
                    ...status,
                    frame_index: f.frame.frame_index,
                    frame_pts_us: f.frame.frame_pts_us,
                  },
                };
              }
            }
          });
          if (alive) unlisten.push(stop);
          else stop();
        }
        const snapshot = await bridge.call<Snapshot>("snapshot");
        if (!alive) return;
        settings = snapshot.settings;
        media = snapshot.media;
        adopt(snapshot.device);
        job = snapshot.job;
        error = snapshot.startup_error;
        ready = true;
        await refresh();
      } catch (e) {
        fail(e);
      }
    })();
    return () => {
      alive = false;
      clearInterval(clock);
      clearTimeout(timer);
      clearTimeout(seekTimer);
      unlisten.forEach((fn) => fn());
    };
  });
  $effect(() => {
    document.documentElement.lang = locale;
    document.title = t(locale, "app.name");
    const query = matchMedia("(prefers-color-scheme: dark)");
    function setTheme() {
      document.documentElement.dataset.theme =
        settings.theme === "system"
          ? query.matches
            ? "dark"
            : "light"
          : settings.theme;
    }
    setTheme();
    query.addEventListener("change", setTheme);
    return () => query.removeEventListener("change", setTheme);
  });
</script>

<header>
  <div class="brand">
    <Icon name="image" /><strong>{t(locale, "app.name")}</strong>
  </div>
  <div class="top-status">
    <span class:connected={fresh} class="status-dot"></span><span
      >{t(
        locale,
        device.connection === "connecting"
          ? "device.connecting"
          : transferring
            ? "job.uploading"
            : fresh
              ? "device.connected"
              : device.connection === "unresponsive" ||
                  device.connection === "connected"
                ? "device.unresponsive"
                : "device.disconnected",
      )}</span
    >{#if fresh}<small>{device.hello?.configured_board}</small>{/if}
  </div>
  <div class="preferences">
    <label
      ><span class="sr-only">{t(locale, "settings.language")}</span><select
        disabled={!ready}
        bind:value={settings.language}
        onchange={save}
        ><option value="tr">{t(locale, "settings.turkish")}</option><option
          value="en">{t(locale, "settings.english")}</option
        ></select
      ></label
    ><label
      ><span class="sr-only">{t(locale, "settings.theme")}</span><select
        disabled={!ready}
        bind:value={settings.theme}
        onchange={save}
        ><option value="system">{t(locale, "settings.system")}</option><option
          value="light">{t(locale, "settings.light")}</option
        ><option value="dark">{t(locale, "settings.dark")}</option></select
      ></label
    >
  </div>
</header>
<main>
  {#if error}<div class="error" role="alert">
      <span
        >{error.code === "desktop_required"
          ? t(locale, "app.desktop_required")
          : errorText(locale, error.code)}</span
      ><button
        class="text"
        aria-label={t(locale, "action.dismiss")}
        onclick={() => (error = null)}>×</button
      >
      <details>
        <summary>{t(locale, "action.details")}</summary>
        <pre>{error.code}{error.details ? `\n${error.details}` : ""}</pre>
      </details>
    </div>{/if}
  {#if device.hello?.hardware?.simulator}<p class="notice">
      {t(locale, "device.simulator")}
    </p>{/if}
  <section class="connection" aria-label={t(locale, "device.connect")}>
    <label
      >{t(locale, "device.ports")}<select
        bind:value={port}
        disabled={!ready || transferring || device.connection === "connecting"}
        >{#if !ports.length}<option value=""
            >{t(locale, "device.no_ports")}</option
          >{/if}{#each ports as name}<option value={name}>{name}</option
          >{/each}</select
      ></label
    ><button class="text" disabled={!ready} onclick={refresh}
      >{t(locale, "device.refresh")}</button
    >{#if device.connection === "connected" || device.connection === "connecting"}<button
        disabled={!ready || transferring}
        onclick={() => action(() => bridge.call("disconnect"))}
        >{t(locale, "device.disconnect")}</button
      >{:else}<button
        disabled={!ready || !port || transferring}
        onclick={connect}
        >{t(
          locale,
          device.connection === "unresponsive"
            ? "device.reconnect"
            : "device.connect",
        )}</button
      >{/if}{#if fresh && status?.state === "idle"}<span class="waiting"
        >{t(locale, "device.waiting")}</span
      >{/if}{#if fresh && status && !status.sd_mounted}<span class="waiting"
        >{t(locale, "error.no_card")}</span
      >{/if}
  </section>
  {#if !media}<section class="empty">
      <div class="empty-icon"><Icon name="image" size={48} /></div>
      <h1>{t(locale, "empty.title")}</h1>
      <p>{t(locale, "empty.body")}</p>
      <button class="primary" disabled={!ready || busy} onclick={choose}
        ><Icon name="folder" />{t(locale, "video.select")}</button
      >
    </section>{:else}
    <div class="workspace">
      <section class="video-column">
        <div class="video-heading">
          <h1 title={media.filename}>{media.filename}</h1>
          <button class="text" disabled={busy} onclick={choose}
            >{t(locale, "video.select")}</button
          >
        </div>
        <Preview
          {media}
          blank={Boolean(matching && status?.frame_index == null)}
          index={previewIndex}
          digest={previewDigest}
          {transform}
          {locale}
          label={previewLabel}
        />{#if fresh && status?.content_sha256 && !matching}<p class="notice">
            {t(locale, "preview.mismatch")}
          </p>{/if}
        <section class="playback" aria-label={t(locale, "playback.state")}>
          <div class="button-row">
            <button
              class="primary"
              disabled={!live || busy}
              onclick={() =>
                control(status?.state === "paused" ? "resume" : "play")}
              ><Icon name="image" />{t(
                locale,
                status?.state === "paused"
                  ? "playback.resume"
                  : "playback.play",
              )}</button
            ><button
              disabled={!live || busy || status?.state !== "playing"}
              onclick={() => control("pause")}
              >{t(locale, "playback.pause")}</button
            ><button disabled={!live || busy} onclick={() => control("stop")}
              >{t(locale, "playback.stop")}</button
            ><button
              class="text"
              disabled={!live || busy}
              onclick={() => control("restart")}
              >{t(locale, "playback.restart")}</button
            >
          </div>
          <label class="seek"
            >{t(locale, "playback.seek")}<input
              type="range"
              min="0"
              max={media.artifact?.header.duration_us ?? 1}
              step="100000"
              bind:value={seek}
              disabled={!live || !matching || busy}
              oninput={seekChanged}
            /><output
              >{time(seek)}{#if seekPending}<span class="sr-only"
                  >{t(locale, "transform.pending")}</span
                >{/if}</output
            ></label
          >
        </section>
        {#if seekPending}<p class="notice" role="status">
            {t(locale, "transform.pending")}
          </p>{/if}
        <details class="transform">
          <summary
            >{t(locale, "live.title")}
            <span
              >{pending || fpsPending
                ? t(locale, "transform.pending")
                : live
                  ? t(locale, "live.confirmed")
                  : t(locale, "device.disconnected")}</span
            ></summary
          >
          {#if live && !matching}<p class="notice">
              {t(locale, "preview.mismatch")}
            </p>{/if}
          <fieldset disabled={!live || !matching || busy}>
            <div class="numeric-grid">
              {#each [{ key: "x", label: "live.x", min: -640, max: 640 }, { key: "y", label: "live.y", min: -480, max: 480 }, { key: "width", label: "transform.width", min: 1, max: 640 }, { key: "height", label: "transform.height", min: 1, max: 480 }] as field}<label
                  >{t(locale, field.label as Key)}<input
                    type="number"
                    min={field.min}
                    max={field.max}
                    value={transform[
                      field.key as "x" | "y" | "width" | "height"
                    ]}
                    oninput={(e) =>
                      change(
                        field.key as keyof Transform,
                        e.currentTarget.valueAsNumber,
                      )}
                  /></label
                >{/each}<label
                >{t(locale, "live.scale")}<input
                  type="number"
                  min="0.01"
                  max="8"
                  step="0.05"
                  value={transform.width /
                    (transform.rotation_deg % 180
                      ? (media.artifact?.header.height ?? 240)
                      : (media.artifact?.header.width ?? 320))}
                  oninput={(e) => scale(e.currentTarget.valueAsNumber)}
                /></label
              ><label
                >{t(locale, "playback.target_fps")}<input
                  type="number"
                  min="1"
                  max="30"
                  step="1"
                  bind:value={requestedFps}
                  oninput={() => {
                    fpsPending = true;
                    fpsRevision = 0;
                  }}
                  onchange={applyFps}
                /></label
              >
            </div>
            <label class="check"
              ><input
                type="checkbox"
                checked={transform.lock_aspect}
                onchange={(e) => change("lock_aspect", e.currentTarget.checked)}
              />{t(locale, "transform.lock_aspect")}</label
            >
            <div
              class="rotation"
              role="group"
              aria-label={t(locale, "transform.rotation")}
            >
              {#each [0, 90, 180, 270] as angle}<button
                  aria-pressed={transform.rotation_deg === angle}
                  onclick={() => rotate(angle)}>{angle}°</button
                >{/each}
            </div>
            <button class="text" onclick={reset}
              >{t(locale, "live.reset")}</button
            >
          </fieldset>
        </details>
      </section>
      <aside>
        <section class="conversion">
          <h2>{t(locale, "conversion.options")}</h2>
          <fieldset disabled={busy}>
            <label
              >{t(locale, "video.quality")}<input
                type="number"
                min="2"
                max="31"
                bind:value={settings.qscale}
                onchange={updateEstimate}
              /></label
            ><label
              >{t(locale, "video.encoding_fps")}<input
                type="number"
                min="1"
                max="30"
                bind:value={settings.fps}
                onchange={updateEstimate}
              /></label
            ><label class="check"
              ><input
                type="checkbox"
                bind:checked={settings.crop}
                onchange={updateEstimate}
              />{t(locale, "video.crop")}</label
            >
          </fieldset>
          <p class="muted">
            {t(locale, "video.estimate")}: {bytes(locale, estimate)}
          </p>
          <button
            class="primary full"
            disabled={busy || !ready}
            onclick={() =>
              action(() => bridge.call("convert", { options: options() }))}
            >{t(locale, "video.convert")}</button
          >{#if media.artifact}<p class="verified">
              <Icon name="check" />{t(locale, "video.ready")}
            </p>
            <button
              class="primary full"
              disabled={busy || !fresh || !status?.sd_mounted}
              onclick={() => action(() => bridge.call("upload"))}
              >{t(locale, "storage.upload")}</button
            >
            <p>{t(locale, "storage.usb_hint")}</p>
            <button class="full" disabled={busy} onclick={chooseCard}
              ><Icon name="folder" />{t(locale, "storage.prepare")}</button
            >{#if card}<p class="path" title={card.path}>{card.path}</p>
              <dl>
                <dt>{t(locale, "storage.filesystem")}</dt>
                <dd>{card.filesystem}</dd>
                <dt>{t(locale, "storage.capacity")}</dt>
                <dd>{bytes(locale, card.available_bytes)}</dd>
              </dl>
              {#if !card.fat32_compatible}<p class="notice">
                  {t(locale, "storage.fat32")}
                </p>{/if}<button
                class="full"
                disabled={busy ||
                  !card.fat32_compatible ||
                  card.available_bytes <
                    media.artifact.header.file_bytes + 65536}
                onclick={() =>
                  action(() =>
                    bridge.call("export", { destination: card!.path }),
                  )}>{t(locale, "storage.write")}</button
              >{/if}{/if}
        </section>
        <details open class="video-info">
          <summary>{t(locale, "video.info")}</summary>
          <dl>
            <dt>{t(locale, "video.original")}</dt>
            <dd>{media.metadata.width} × {media.metadata.height}</dd>
            <dt>{t(locale, "video.prepared")}</dt>
            <dd>
              {media.artifact
                ? `${media.artifact.header.width} × ${media.artifact.header.height}`
                : t(locale, "telemetry.unavailable")}
            </dd>
            <dt>{t(locale, "video.duration")}</dt>
            <dd>
              {media.metadata.duration_us == null
                ? t(locale, "telemetry.unavailable")
                : time(media.metadata.duration_us)}
            </dd>
            <dt>{t(locale, "video.original_fps")}</dt>
            <dd>
              {number(
                locale,
                media.metadata.original_fps
                  ? media.metadata.original_fps.numerator /
                      media.metadata.original_fps.denominator
                  : null,
              )}
            </dd>
            <dt>{t(locale, "video.size")}</dt>
            <dd>{bytes(locale, media.artifact?.header.file_bytes)}</dd>
            <dt>{t(locale, "playback.target_fps")}</dt>
            <dd>
              {number(
                locale,
                fresh && status
                  ? status.target_fps.numerator / status.target_fps.denominator
                  : null,
              )}
            </dd>
            <dt>{t(locale, "playback.actual_fps")}</dt>
            <dd>{number(locale, fresh ? status?.actual_fps : null)}</dd>
            <dt>{t(locale, "video.frame")}</dt>
            <dd>
              {fresh && status?.frame_index != null
                ? `${status.frame_index + 1} / ${status.frame_count}`
                : t(locale, "telemetry.unavailable")}
            </dd>
            <dt>{t(locale, "video.timestamp")}</dt>
            <dd>
              {fresh && status
                ? time(status.frame_pts_us)
                : t(locale, "telemetry.unavailable")}
            </dd>
          </dl>
        </details>
      </aside>
    </div>{/if}
  {#if job}<section class="job" aria-live="polite">
      <div>
        <strong>{t(locale, jobs[job.stage] ?? "job.starting")}</strong
        >{#if job.running}<button
            class="text"
            onclick={() => action(() => bridge.call("cancel"))}
            >{t(locale, "action.cancel")}</button
          >{/if}
      </div>
      {#if job.running}<progress
          max={job.total ?? 1}
          value={job.total ? job.completed : undefined}
        ></progress>{#if job.total}<small
            >{number(
              locale,
              Math.min(100, ((job.completed ?? 0) / job.total) * 100),
            )} %</small
          >{/if}{:else if job.stage === "complete" && job.kind === "export"}<p>
          {t(locale, "storage.complete")}
        </p>{:else if job.stage === "complete" && job.kind === "upload"}<p>
          {t(locale, "storage.upload_complete")}
        </p>{/if}
    </section>{/if}
  <details class="monitor">
    <summary
      >{t(locale, "device.monitor")}<span
        >{fresh
          ? t(locale, stateLabels[status?.state ?? "idle"] ?? "state.idle")
          : t(locale, "device.disconnected")}</span
      ></summary
    >
    <dl class="monitor-grid">
      {#each telemetry as [label, key, unit]}<div>
          <dt>{t(locale, label)}</dt>
          <dd
            title={key === "temperature_c"
              ? t(locale, "monitor.unsupported")
              : undefined}
          >
            {metric(key, unit)}
          </dd>
        </div>{/each}
      <div>
        <dt>{t(locale, "monitor.wifi")}</dt>
        <dd>
          {fresh && status?.hardware?.wifi_status
            ? t(
                locale,
                (`wifi.${status.hardware.wifi_status}` in
                {
                  "wifi.disabled": 1,
                  "wifi.connected": 1,
                  "wifi.disconnected": 1,
                  "wifi.access_point": 1,
                  "wifi.not_emulated": 1,
                }
                  ? `wifi.${status.hardware.wifi_status}`
                  : "telemetry.unavailable") as Key,
              )
            : t(locale, "telemetry.unavailable")}
        </dd>
      </div>
      <div>
        <dt>{t(locale, "monitor.drops")}</dt>
        <dd>{number(locale, fresh ? status?.dropped_frames : null)}</dd>
      </div>
      <div>
        <dt>{t(locale, "device.firmware")}</dt>
        <dd>
          {device.hello?.firmware_version ?? t(locale, "telemetry.unavailable")}
        </dd>
      </div>
      <div>
        <dt>{t(locale, "device.usb")}</dt>
        <dd>{t(locale, fresh ? "device.connected" : "device.disconnected")}</dd>
      </div>
      <div>
        <dt>{t(locale, "playback.actual_fps")}</dt>
        <dd>{number(locale, fresh ? status?.actual_fps : null)}</dd>
      </div>
    </dl>
  </details>
  <details bind:open={toolOpen} class="tools">
    <summary>{t(locale, "settings.tools")}</summary><label
      >{t(locale, "settings.ffmpeg")}<input
        disabled={!ready || busy}
        bind:value={settings.ffmpeg}
      /></label
    ><label
      >{t(locale, "settings.ffprobe")}<input
        disabled={!ready || busy}
        bind:value={settings.ffprobe}
      /></label
    ><button disabled={!ready || busy} onclick={save}
      >{t(locale, "settings.save")}</button
    >
  </details>
</main>
