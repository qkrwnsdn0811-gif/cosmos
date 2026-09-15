import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  server: {
    port: 5173,
    proxy: {
      // 실 백엔드(Spring, galaxy_api) 연동 시 VITE_API_MODE=live 로 두고 이 프록시를 사용한다.
      "/api": { target: process.env.VITE_API_PROXY ?? "http://localhost:8080", changeOrigin: true },
    },
  },
  build: {
    target: "es2022",
    chunkSizeWarningLimit: 1400,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          if (/[\/](three|@react-three|postprocessing|maath|troika|three-stdlib)[\/]/.test(id)) return "three";
          return "vendor";
        },
      },
    },
  },
});
