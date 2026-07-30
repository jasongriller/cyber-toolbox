import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The SPA can be served under a portal sub-path (e.g. /rmf-migrator/). Set
// VITE_BASE_PATH at build time to match the path your portal serves it under.
export default defineConfig({
  base: process.env.VITE_BASE_PATH ?? "/",
  // amazon-cognito-identity-js drags in Node's `buffer` polyfill, which
  // references `global` at module init. The production bundle happens to
  // tolerate it, but the dev-server prebundle does not — without this the dev
  // server renders a blank page with no console error.
  define: { global: "globalThis" },
  plugins: [react()],
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
  },
});
