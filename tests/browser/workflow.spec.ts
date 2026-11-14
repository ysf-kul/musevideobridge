import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
const evidence = process.env.MVB_BROWSER_EVIDENCE_DIR ?? ".verification/phase3";
test("real backend workflow, localization, transforms, reconnect and accessible themes", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.addInitScript(() => {
    const listeners = new Map<string, Set<(v: unknown) => void>>();
    let timer: ReturnType<typeof setInterval> | undefined;
    window.__MVB_TEST_BRIDGE__ = {
      async call(command, args) {
        const r = await fetch("http://127.0.0.1:1431", {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ command, args: args ?? {} }),
        });
        const v = await r.json();
        if (!v.ok) throw v.error;
        return v.result;
      },
      async on(name, callback) {
        let group = listeners.get(name);
        if (!group) {
          group = new Set();
          listeners.set(name, group);
        }
        const wrapped = (v: unknown) => callback(v as never);
        group.add(wrapped);
        if (!timer)
          timer = setInterval(async () => {
            const r = await fetch("http://127.0.0.1:1431/events");
            const v = await r.json();
            for (const e of v.result)
              listeners.get(e.name)?.forEach((fn) => fn(e.payload));
          }, 100);
        return () => group!.delete(wrapped);
      },
    };
  });
  await page.goto("/");
  await expect(
    page.getByRole("button", { name: "Video seç", exact: true }),
  ).toBeEnabled();
  await expect(page.locator("html")).toHaveAttribute("lang", "tr");
  await page
    .getByRole("combobox", { name: "Dil", exact: true })
    .selectOption("en");
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await page
    .getByRole("combobox", { name: "Appearance", exact: true })
    .selectOption("dark");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.getByRole("button", { name: "Select video", exact: true }).click();
  await expect(page.getByRole("heading", { name: "input.mp4" })).toBeVisible();
  await expect(page.getByLabel("Encoding FPS", { exact: true })).toHaveValue(
    "25",
  );
  await page.getByLabel("JPEG qscale · lower is higher quality").fill("10");
  await page.getByLabel("JPEG qscale · lower is higher quality").press("Tab");
  await page.getByRole("button", { name: "Convert", exact: true }).hover();
  expect(
    (
      await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
        .analyze()
    ).violations,
  ).toEqual([]);
  await page.getByRole("button", { name: "Convert", exact: true }).click();
  await expect(
    page.getByText("Prepared and verified", { exact: true }),
  ).toBeVisible({ timeout: 40000 });
  await page
    .getByRole("button", { name: "Prepare SD card", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Write and verify" }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "Write and verify" }).click();
  await expect(
    page.getByText(
      "Video written and verified. Eject the card safely, then insert it into the ESP32.",
    ),
  ).toBeVisible();
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(
    page.getByText("Host test simulator · physical hardware unverified"),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Pause", exact: true }),
  ).toBeEnabled();
  await page
    .getByRole("button", { name: "Send over USB", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Disconnect", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByText("Transfer verified by the device.", { exact: true }),
  ).toBeVisible({ timeout: 40000 });
  await expect(
    page.getByRole("button", { name: "Pause", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Resume", exact: true }),
  ).toBeEnabled();
  const seek = page.getByRole("slider", { name: "Seek", exact: true });
  await seek.focus();
  await seek.press("Home");
  await expect(page.locator("figure.preview")).toHaveAttribute(
    "data-frame-index",
    "0",
  );
  await seek.press("ArrowRight");
  await expect(page.locator("figure.preview")).toHaveAttribute(
    "data-frame-index",
    "3", // 0.1-second seek at 25 FPS selects the first frame at/after it.
  );
  await page.locator("details.transform summary").click();
  await page.getByLabel("X position", { exact: true }).fill("15");
  await expect(
    page.getByText("Confirmed by device", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "90°", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "90°", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(
    page.getByText("Confirmed by device", { exact: true }),
  ).toBeVisible();
  await page.getByLabel("Scale", { exact: true }).fill("0.5");
  await expect(
    page.getByText("Confirmed by device", { exact: true }),
  ).toBeVisible();
  await page.getByLabel("Target FPS", { exact: true }).fill("5");
  await page.getByLabel("Target FPS", { exact: true }).press("Tab");
  await expect(
    page.getByText("Confirmed by device", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Resume", exact: true }).click();
  await page.getByRole("button", { name: "Stop", exact: true }).click();
  await page.getByRole("button", { name: "Restart", exact: true }).click();
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await page.getByRole("button", { name: "Select video", exact: true }).click();
  await page.getByLabel("JPEG qscale · lower is higher quality").fill("12");
  await page.getByLabel("JPEG qscale · lower is higher quality").press("Tab");
  await page.getByRole("button", { name: "Convert", exact: true }).click();
  await expect(
    page.getByText("Prepared and verified", { exact: true }),
  ).toBeVisible({ timeout: 40000 });
  await page
    .getByRole("button", { name: "Send over USB", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Disconnect", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByText("Transfer verified by the device.", { exact: true }),
  ).toBeVisible({ timeout: 40000 });
  const queue = page.locator(".video-queue");
  await expect(queue.locator("li")).toHaveCount(2);
  await page.getByRole("button", { name: "Pause", exact: true }).click();
  await page.getByRole("button", { name: "Previous", exact: true }).click();
  await expect(queue.locator("li").first()).toHaveClass("active");
  await expect(
    page.getByRole("button", { name: "Resume", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect(queue.locator("li").last()).toHaveClass("active");
  await queue
    .getByRole("button", { name: "Move up: input.mp4", exact: true })
    .last()
    .click();
  await expect(queue.locator("li").first()).toHaveClass("active");
  await page
    .getByLabel("Playback mode", { exact: true })
    .selectOption("loop_queue");
  await expect(page.getByLabel("Playback mode", { exact: true })).toHaveValue(
    "loop_queue",
  );
  await expect(queue.locator("li:not(.active) .queue-name span")).toContainText(
    "Next",
  );
  await expect(queue.locator("li:not(.active) .queue-name span")).toContainText(
    "Previous",
  );
  await queue
    .getByRole("button", { name: "Remove from queue", exact: true })
    .last()
    .click();
  await expect(queue.locator("li")).toHaveCount(1);
  await page
    .getByLabel("Playback mode", { exact: true })
    .selectOption("loop_current");
  await expect(page.getByLabel("Playback mode", { exact: true })).toHaveValue(
    "loop_current",
  );
  await page
    .getByLabel("Playback mode", { exact: true })
    .selectOption("play_once");
  await expect(page.getByLabel("Playback mode", { exact: true })).toHaveValue(
    "play_once",
  );
  await page.getByRole("button", { name: "Resume", exact: true }).click();
  await page.locator("details.monitor summary").click();
  await expect(
    page.getByText("CPU temperature", { exact: true }),
  ).toBeVisible();
  await expect(
    page
      .locator("details.monitor")
      .getByText("Unavailable", { exact: true })
      .first(),
  ).toBeVisible();
  for (const theme of ["light", "dark"]) {
    await page
      .getByRole("combobox", { name: "Appearance", exact: true })
      .selectOption(theme);
    const result = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
      .analyze();
    expect(result.violations).toEqual([]);
    await page.screenshot({
      path: `${evidence}/${theme}-wide.png`,
      fullPage: true,
    });
  }
  await page.setViewportSize({ width: 620, height: 850 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.screenshot({
    path: `${evidence}/dark-narrow.png`,
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.getByRole("button", { name: "Disconnect", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Connect", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Pause", exact: true }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Pause", exact: true }),
  ).toBeEnabled();
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page
    .getByRole("combobox", { name: "Language", exact: true })
    .selectOption("tr");
  await expect(
    page.getByText("Cihaz bilgileri", { exact: false }).first(),
  ).toBeVisible();
  await page.getByRole("combobox", { name: "Dil", exact: true }).focus();
  await page.keyboard.press("Tab");
  await expect(page.locator(":focus-visible")).toHaveCount(1);
  expect(
    await page
      .locator(":focus-visible")
      .evaluate((e) => getComputedStyle(e).outlineStyle),
  ).toBe("solid");
  expect(
    await page
      .getByRole("button", { name: "Bağlantıyı kes", exact: true })
      .evaluate((e) => getComputedStyle(e).transitionDuration),
  ).toBe("0s");
  await page
    .getByRole("combobox", { name: "Dil", exact: true })
    .selectOption("en");
  const mode = page.getByLabel("Playback mode", { exact: true });
  await expect(mode).toBeEnabled();
  const confirmedMode = await mode.inputValue();
  await page.evaluate(() => {
    const bridge = window.__MVB_TEST_BRIDGE__!;
    const original = bridge.call;
    bridge.call = async <T>(
      command: string,
      args?: Record<string, unknown>,
    ) => {
      if (
        command === "command" &&
        (args?.value as { method?: string } | undefined)?.method ===
          "set_loop_mode"
      ) {
        bridge.call = original;
        await original("disconnect");
      }
      return original<T>(command, args);
    };
  });
  await mode.selectOption(
    confirmedMode === "loop_queue" ? "play_once" : "loop_queue",
  );
  await expect(
    page.getByRole("button", { name: "Connect", exact: true }),
  ).toBeVisible();
  await expect(mode).toHaveValue(confirmedMode);
  await expect(page.getByRole("alert")).toBeVisible();
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(mode).toBeEnabled();
  await expect(mode).toHaveValue("play_once");
  expect(errors).toEqual([]);
});
