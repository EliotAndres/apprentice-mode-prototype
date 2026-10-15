import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { viteSingleFile } from "vite-plugin-singlefile";

// Builds one self-contained page into the viewer template; replicate.viewer then fills in the data.
export default defineConfig({
  plugins: [react(), tailwindcss(), viteSingleFile()],
  build: { outDir: "../replicate/viewer", emptyOutDir: false, rollupOptions: { input: "dashboard.html" } },
});
