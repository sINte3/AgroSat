#!/usr/bin/env node

import { createReadStream, existsSync, statSync } from 'node:fs';
import http from 'node:http';
import path from 'node:path';


const host = '127.0.0.1';
const port = Number(process.env.QUALIFICATION_FRONTEND_PORT || 54181);
const backendPort = Number(process.env.QUALIFICATION_BACKEND_PORT || 58081);
const configuredRoot = process.env.QUALIFICATION_DIST_ROOT;

if (!Number.isSafeInteger(port) || port < 1024 || port > 65535) {
  throw new Error('Invalid qualification frontend port');
}
if (!Number.isSafeInteger(backendPort) || backendPort < 1024 || backendPort > 65535) {
  throw new Error('Invalid qualification backend port');
}
if (!configuredRoot || !path.isAbsolute(configuredRoot)) {
  throw new Error('QUALIFICATION_DIST_ROOT must be an absolute path');
}

const root = path.resolve(configuredRoot);
const indexPath = path.join(root, 'index.html');
if (!existsSync(indexPath) || !statSync(indexPath).isFile()) {
  throw new Error('Production frontend bundle is unavailable');
}

const contentTypes = new Map([
  ['.css', 'text/css; charset=utf-8'],
  ['.html', 'text/html; charset=utf-8'],
  ['.ico', 'image/x-icon'],
  ['.jpeg', 'image/jpeg'],
  ['.jpg', 'image/jpeg'],
  ['.js', 'text/javascript; charset=utf-8'],
  ['.json', 'application/json; charset=utf-8'],
  ['.map', 'application/json; charset=utf-8'],
  ['.png', 'image/png'],
  ['.svg', 'image/svg+xml'],
  ['.webmanifest', 'application/manifest+json; charset=utf-8'],
  ['.woff', 'font/woff'],
  ['.woff2', 'font/woff2'],
]);

const hopByHop = new Set([
  'connection',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'te',
  'trailer',
  'transfer-encoding',
  'upgrade',
]);

function sanitizedLog(event) {
  process.stdout.write(`${JSON.stringify(event)}\n`);
}

function proxyApi(request, response, requestUrl) {
  const started = Date.now();
  const headers = {};
  for (const [name, value] of Object.entries(request.headers)) {
    if (!hopByHop.has(name.toLowerCase()) && name.toLowerCase() !== 'host') {
      headers[name] = value;
    }
  }
  headers.host = `${host}:${backendPort}`;

  const upstream = http.request({
    host,
    port: backendPort,
    method: request.method,
    path: `${requestUrl.pathname}${requestUrl.search}`,
    headers,
  }, (upstreamResponse) => {
    const responseHeaders = {};
    for (const [name, value] of Object.entries(upstreamResponse.headers)) {
      if (!hopByHop.has(name.toLowerCase())) responseHeaders[name] = value;
    }
    response.writeHead(upstreamResponse.statusCode || 502, responseHeaders);
    upstreamResponse.pipe(response);
    upstreamResponse.on('end', () => sanitizedLog({
      kind: 'api_proxy',
      method: request.method,
      path: requestUrl.pathname,
      status: upstreamResponse.statusCode || 502,
      durationMs: Date.now() - started,
    }));
  });

  upstream.setTimeout(65_000, () => upstream.destroy(new Error('upstream timeout')));
  upstream.on('error', () => {
    if (!response.headersSent) {
      response.writeHead(502, { 'content-type': 'application/json; charset=utf-8' });
    }
    response.end(JSON.stringify({ detail: 'Qualification backend unavailable' }));
    sanitizedLog({
      kind: 'api_proxy',
      method: request.method,
      path: requestUrl.pathname,
      status: 502,
      durationMs: Date.now() - started,
    });
  });
  request.pipe(upstream);
}

function resolveStaticPath(pathname) {
  const decoded = decodeURIComponent(pathname);
  const relative = decoded === '/' ? 'index.html' : decoded.replace(/^\/+/, '');
  const candidate = path.resolve(root, relative);
  if (candidate !== root && !candidate.startsWith(`${root}${path.sep}`)) return null;
  if (!existsSync(candidate) || !statSync(candidate).isFile()) return null;
  return candidate;
}

function sendFile(request, response, filename) {
  const extension = path.extname(filename).toLowerCase();
  const headers = {
    'content-type': contentTypes.get(extension) || 'application/octet-stream',
    'x-content-type-options': 'nosniff',
  };
  if (filename === indexPath || filename.endsWith(`${path.sep}sw.js`)) {
    headers['cache-control'] = 'no-store';
  } else if (filename.includes(`${path.sep}assets${path.sep}`)) {
    headers['cache-control'] = 'public, max-age=31536000, immutable';
  } else {
    headers['cache-control'] = 'public, max-age=300';
  }
  response.writeHead(200, headers);
  if (request.method === 'HEAD') response.end();
  else createReadStream(filename).pipe(response);
}

const server = http.createServer((request, response) => {
  let requestUrl;
  try {
    requestUrl = new URL(request.url || '/', `http://${host}:${port}`);
  } catch {
    response.writeHead(400, { 'content-type': 'text/plain; charset=utf-8' });
    response.end('Bad request');
    return;
  }

  if (requestUrl.pathname.startsWith('/api/') || requestUrl.pathname.startsWith('/health')) {
    proxyApi(request, response, requestUrl);
    return;
  }
  if (!['GET', 'HEAD'].includes(request.method || '')) {
    response.writeHead(405, { allow: 'GET, HEAD' });
    response.end();
    return;
  }

  let filename;
  try {
    filename = resolveStaticPath(requestUrl.pathname);
  } catch {
    filename = null;
  }
  if (!filename) filename = indexPath;
  sendFile(request, response, filename);
});

server.listen(port, host, () => sanitizedLog({
  kind: 'qualification_frontend_ready',
  address: host,
  port,
  backendAddress: host,
  backendPort,
  distRootClass: 'exact_worktree_production_bundle',
  credentialValuesLogged: false,
}));

function shutdown() {
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(1), 5_000).unref();
}

process.on('SIGINT', shutdown);
process.on('SIGTERM', shutdown);
