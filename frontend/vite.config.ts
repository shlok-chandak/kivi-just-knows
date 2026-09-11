import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Built into the API's own static directory, so shipping is one container:
// whoever clones this runs `docker compose up` and opens one port. During
// development the dev server proxies instead, which keeps the browser on a
// single origin and means server-sent events are not a CORS problem.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: Object.fromEntries(
      [
        "/ask",
        "/events",
        "/evaluation",
        "/health",
        "/ignored",
        "/memories",
        "/profile",
        "/recall",
        "/stream",
        "/traces",
      ].map((path) => [
        path,
        {
          target: "http://localhost:8000",
          changeOrigin: true,
          // Streams must not be buffered into uselessness by the proxy.
          configure: (proxy: any) => {
            proxy.on("proxyRes", (res: any) => {
              if (String(res.headers["content-type"]).includes("event-stream")) {
                res.headers["cache-control"] = "no-cache";
              }
            });
          },
        },
      ]),
    ),
  },
  build: {
    outDir: "../backend/app/static",
    emptyOutDir: true,
  },
});
