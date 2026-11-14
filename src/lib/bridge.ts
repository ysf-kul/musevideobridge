import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
export interface Bridge {
  call<T>(command: string, args?: Record<string, unknown>): Promise<T>;
  on<T>(event: string, callback: (v: T) => void): Promise<() => void>;
}
declare global {
  interface Window {
    __MVB_TEST_BRIDGE__?: Bridge;
    __TAURI_INTERNALS__?: unknown;
  }
}
export const available = () =>
  Boolean(
    window.__TAURI_INTERNALS__ ||
      (import.meta.env.DEV && window.__MVB_TEST_BRIDGE__),
  );
export const bridge: Bridge = {
  call: (cmd, args) =>
    import.meta.env.DEV && window.__MVB_TEST_BRIDGE__
      ? (import.meta.env.DEV && window.__MVB_TEST_BRIDGE__).call(cmd, args)
      : invoke(cmd, args),
  on: async (event, callback) =>
    import.meta.env.DEV && window.__MVB_TEST_BRIDGE__
      ? (import.meta.env.DEV && window.__MVB_TEST_BRIDGE__).on(event, callback)
      : listen(event, (e) => callback(e.payload as never)),
};
