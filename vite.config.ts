import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";
export default defineConfig({
  plugins: [svelte()],
  clearScreen: false,
  server: {
    host: "127.0.0.1",
    port: 1420,
    strictPort: true,
    watch: {
      // Build/tool caches contain SDK symlinks and never drive frontend HMR.
      ignored: [
        "**/.verification/**",
        "**/target/**",
        "**/dist-packages/**",
        "**/firmware/.pio/**",
        "**/.dev-tools/**",
      ],
    },
  },
  build: { target: "es2022" },
});
