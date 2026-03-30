/**
 * app/server.js
 * ─────────────
 * §4.2.2 — mandatory /health endpoint.
 * Returns {"status":"ok","cloud":"<provider>"} when healthy.
 * The cloud.provider value is injected by the Helm chart (§4.5.2).
 */

const http = require('http');

const PORT     = parseInt(process.env.PORT  || '8080', 10);
const PROVIDER = process.env.CLOUD_PROVIDER || 'unknown';

const server = http.createServer((req, res) => {
  // §4.2.2 / Algorithm 2 — health endpoint
  if (req.url === '/health') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({
      status:    'ok',
      cloud:     PROVIDER,
      timestamp: new Date().toISOString(),
      uptime_s:  Math.floor(process.uptime()),
    }));
    return;
  }

  // Application root
  if (req.url === '/') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({
      message:  'Smart Multi-Cloud Workload Migrator — sample app',
      provider: PROVIDER,
    }));
    return;
  }

  res.writeHead(404);
  res.end('Not found');
});

server.listen(PORT, () => {
  console.log(`[${new Date().toISOString()}] Running on :${PORT}  provider=${PROVIDER}`);
});

// Graceful shutdown — §4.6.3 terminationGracePeriodSeconds
process.on('SIGTERM', () => {
  console.log('SIGTERM received — draining connections');
  server.close(() => {
    console.log('Server closed');
    process.exit(0);
  });
});