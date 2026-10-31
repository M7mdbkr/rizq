'use strict';
const { spawn } = require('node:child_process');
const path = require('node:path');
const fs = require('node:fs');

const PORT = 47192; // The database is tied to this origin; never choose a random port.

function backendEnvironment(root, dataDir, baseEnv = process.env) {
  const runtime = path.join(root, 'agent', 'runtime');
  const data = path.join(dataDir, 'agent');
  const results = path.join(dataDir, 'results');
  for (const dir of [data, results]) fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  return { ...baseEnv, PYTHONUNBUFFERED: '1', RIZQ_DESKTOP: '1',
    RIZQ_ASSET_RUNTIME: runtime, RIZQ_DATA_DIR: data, RIZQ_RESULTS_DIR: results,
    RIZQ_MEMORY_DB: path.join(data, 'chat_memory.sqlite'),
    RIZQ_AUDIT_LOG: path.join(data, 'audit.jsonl'),
    RIZQ_SESSION_DB: path.join(data, 'sessions.sqlite'),
    RIZQ_SCHEDULE_DB: path.join(data, 'schedule.sqlite') };
}

function startBackend(root, dataDir, options = {}) {
  const python = options.python || (process.platform === 'win32' ? 'python' : 'python3');
  const runtime = path.join(root, 'agent', 'runtime');
  const child = (options.spawn || spawn)(python,
    ['-u', '-m', 'rizq_agent', 'web', '--port', String(PORT), '--host', '127.0.0.1'],
    { cwd: runtime, env: backendEnvironment(root, dataDir), stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
  let stderr = '';
  child.stderr.on('data', chunk => { stderr = (stderr + chunk.toString()).slice(-3000); });
  const ready = new Promise((resolve, reject) => {
    let settled = false, output = '';
    const timer = setTimeout(() => finish(new Error('The local agent did not start in time.')), 15000);
    function finish(err) {
      if (settled) return;
      settled = true; clearTimeout(timer);
      if (err) { child.kill(); reject(err); } else resolve();
    }
    child.on('error', err => finish(new Error(`Could not start Python: ${err.message}`)));
    child.on('exit', code => finish(new Error(`The local agent stopped (${code}). ${stderr}`)));
    child.stdout.on('data', chunk => {
      output += chunk.toString();
      if (output.includes('Rizq is running at http://127.0.0.1:47192')) finish();
      if (output.length > 5000) output = output.slice(-5000);
    });
  });
  return { child, ready };
}

module.exports = { PORT, backendEnvironment, startBackend };
