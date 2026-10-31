'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { PORT, backendEnvironment, startBackend } = require('./backend.cjs');

function fakeChild() {
  const child = new EventEmitter();
  child.stdout = new EventEmitter(); child.stderr = new EventEmitter();
  child.kill = () => { child.killed = true; }; return child;
}

test('backend state is separate and desktop port is stable', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rizq-test-'));
  try {
    const env = backendEnvironment('/project', dir, {});
    assert.equal(PORT, 47192);
    assert.equal(env.RIZQ_DESKTOP, '1');
    assert.equal(env.RIZQ_SESSION_DB, path.join(dir, 'agent', 'sessions.sqlite'));
    assert.equal(env.RIZQ_RESULTS_DIR, path.join(dir, 'results'));
    assert.ok(fs.statSync(path.join(dir, 'agent')).isDirectory());
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test('only opens after its own loopback backend reports ready', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rizq-test-'));
  const child = fakeChild(); let command;
  try {
    const backend = startBackend('/project', dir, { python: 'python-test', spawn: (...args) => { command = args; return child; } });
    assert.deepEqual(command[1].slice(-4), ['--port', '47192', '--host', '127.0.0.1']);
    child.stdout.emit('data', Buffer.from('Rizq is running at http://127.0.0.1:47192 (this device only).'));
    await backend.ready;
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test('backend startup failure never connects to a different process', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rizq-test-'));
  const child = fakeChild();
  try {
    const backend = startBackend('/project', dir, { spawn: () => child });
    child.stderr.emit('data', Buffer.from('port busy'));
    child.emit('exit', 1);
    await assert.rejects(backend.ready, /port busy/);
    assert.equal(child.killed, true);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});
