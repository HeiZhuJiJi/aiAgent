import { createReadStream, statSync } from "node:fs";
import { createServer, request as httpRequest } from "node:http";
import { extname, join, normalize } from "node:path";

const publicRoot = "/app/public";
const port = Number(process.env.PORT ?? "8080");
const upstream = new URL(process.env.API_UPSTREAM ?? "http://api:8080");
const contentTypes = new Map([
  [".css", "text/css; charset=utf-8"],
  [".html", "text/html; charset=utf-8"],
  [".ico", "image/x-icon"],
  [".js", "text/javascript; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".map", "application/json; charset=utf-8"],
  [".png", "image/png"],
  [".svg", "image/svg+xml"],
  [".woff2", "font/woff2"],
]);

function proxyApi(req, res) {
  const incoming = new URL(req.url ?? "/", "http://localhost");
  const path = incoming.pathname.slice(4) || "/";
  const options = {
    protocol: upstream.protocol,
    hostname: upstream.hostname,
    port: upstream.port,
    method: req.method,
    path: path + incoming.search,
    headers: { ...req.headers, host: upstream.host },
  };
  const proxied = httpRequest(options, (upstreamResponse) => {
    res.writeHead(upstreamResponse.statusCode ?? 502, upstreamResponse.headers);
    upstreamResponse.pipe(res);
  });
  proxied.on("error", () => {
    if (!res.headersSent) {
      res.writeHead(502, { "content-type": "application/json" });
    }
    res.end('{"detail":"API upstream unavailable"}');
  });
  req.pipe(proxied);
}

function serveWeb(req, res) {
  const incoming = new URL(req.url ?? "/", "http://localhost");
  let decoded;
  try {
    decoded = decodeURIComponent(incoming.pathname);
  } catch {
    res.writeHead(400);
    res.end();
    return;
  }
  const relative = normalize(decoded).replace(/^([/\\])+/, "");
  let candidate = join(publicRoot, relative);
  if (!candidate.startsWith(publicRoot)) {
    res.writeHead(403);
    res.end();
    return;
  }
  try {
    if (statSync(candidate).isDirectory()) {
      candidate = join(candidate, "index.html");
    }
    statSync(candidate);
  } catch {
    candidate = join(publicRoot, "index.html");
  }
  res.writeHead(200, {
    "content-type": contentTypes.get(extname(candidate)) ?? "application/octet-stream",
    "x-content-type-options": "nosniff",
  });
  if (req.method === "HEAD") {
    res.end();
    return;
  }
  createReadStream(candidate).pipe(res);
}

createServer((req, res) => {
  if ((req.url ?? "").startsWith("/api/")) {
    proxyApi(req, res);
  } else {
    serveWeb(req, res);
  }
}).listen(port, "0.0.0.0");
