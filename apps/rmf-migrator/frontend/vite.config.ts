import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The SPA can be served under a portal sub-path (e.g. /rmf-migrator/). Set
// VITE_BASE_PATH at build time to match the module's app_base_path variable.
export default defineConfig({
  base: process.env.VITE_BASE_PATH ?? "/",
  plugins: [react()],
  test: {
    globals: true,
    environment: "node",
  },
});
