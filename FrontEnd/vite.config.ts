import { defineConfig, loadEnv, type HtmlTagDescriptor, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";

const root = fileURLToPath(new URL(".", import.meta.url));

/**
 * 씬 청크 미리 받기. GalaxyPage 가 Scene 을 lazy 로 불러 three 청크가 첫 번들의 정적 의존에서 빠졌는데,
 * 첫 화면(/)에서는 곧바로 필요하다 — Vite 는 동적 import 청크를 HTML 에 modulepreload 로 넣지 않으므로 여기서 넣는다.
 * (HUD 는 three 를 기다리지 않고 먼저 그려지고, three 다운로드는 예전처럼 HTML 시점에 시작된다)
 */
function preloadSceneChunks(): Plugin {
  return {
    name: "cosmos:preload-scene-chunks",
    transformIndexHtml: {
      order: "post",
      handler(html, ctx) {
        const tags: HtmlTagDescriptor[] = [];
        for (const out of Object.values(ctx.bundle ?? {})) {
          if (out.type !== "chunk") continue;
          const facade = out.facadeModuleId?.replace(/\\/g, "/") ?? "";
          const wanted = out.name === "three" || facade.endsWith("/src/three/Scene.tsx");
          if (!wanted || html.includes(out.fileName)) continue;
          tags.push({ tag: "link", attrs: { rel: "modulepreload", crossorigin: true, href: `/${out.fileName}` }, injectTo: "head" });
        }
        return tags;
      },
    },
  };
}

export default defineConfig(({ mode }) => {
  // .env 는 import.meta.env 로만 들어오고 process.env 에는 실리지 않는다 — 설정 파일에서는 loadEnv 로 직접 읽는다
  const env = loadEnv(mode, root, "VITE_");
  // /api 는 항상 실 백엔드로 넘긴다. 로컬 Spring 은 18081(application.yml SERVER_PORT 기본값), 배포 서버는 https://j15c205.p.ssafy.io
  const apiProxy = env.VITE_API_PROXY || "http://localhost:18081";

  return {
    plugins: [react(), preloadSceneChunks()],
    resolve: {
      alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
    },
    server: {
      port: 5173,
      proxy: {
        "/api": { target: apiProxy, changeOrigin: true },
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
  };
});
