import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig(({ mode }) => {
  // Load .env from the parent directory (project root)
  const env = loadEnv(mode, path.resolve(__dirname, ".."), "");

  // Make all loaded env vars available to import.meta.env
  Object.assign(process.env, env);

  return {
    plugins: [react()],
    envDir: path.resolve(__dirname, ".."),
    server: {
      host: "0.0.0.0",
      port: 5173,
      strictPort: true,
      proxy: {
        "/api": {
          target: "http://127.0.0.1:5000",
          changeOrigin: true,
        },
      },
    },
    preview: {
      host: "0.0.0.0",
      port: 4173,
    },
  };
});