// Path: tools/campaign_t3_native.cjs | Purpose: Deliver durable handoffs through installed T3 native RPC; no browser or credentials in evidence.
'use strict';
const fs = require('fs'), cp = require('child_process'), crypto = require('crypto');
const [statePath, messagePath, threadId, selectionPath] = process.argv.slice(2);
if (!statePath || !messagePath || !threadId) {
  throw new Error('usage: node campaign_t3_native.cjs STATE MESSAGE THREAD');
}
const runtime = JSON.parse(fs.readFileSync('/home/djsm/.t3/userdata/server-runtime.json', 'utf8'));
if (runtime.host !== '127.0.0.1' || runtime.origin !== `http://127.0.0.1:${runtime.port}`) throw new Error('Unexpected local T3 origin');
let state = fs.existsSync(statePath) ? JSON.parse(fs.readFileSync(statePath, 'utf8')) : {
  threadId, commandId: crypto.randomUUID(), messageId: crypto.randomUUID(), sent: false
};
if (state.threadId !== threadId) throw new Error('handoff thread drift');
const persist = () => {
  fs.writeFileSync(statePath + '.tmp', JSON.stringify(state, null, 2) + '\n');
  fs.renameSync(statePath + '.tmp', statePath);
};
persist();
let auth, ws;
(async () => {
  try {
    if (state.sent) return;
    auth = JSON.parse(cp.execFileSync('/usr/bin/t3', ['auth', 'session', 'issue', '--base-dir', '/home/djsm/.t3',
      '--ttl', '5m', '--label', 'Authorized Laya campaign handoff', '--json'], {encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe']}));
    const response = await fetch(runtime.origin + '/api/auth/websocket-ticket', {
      method: 'POST', headers: {Authorization: 'Bearer ' + auth.token}
    });
    if (!response.ok) throw new Error('ticket issuance failed: ' + response.status);
    const ticket = await response.json();
    ws = new WebSocket(`ws://127.0.0.1:${runtime.port}/ws?wsTicket=${encodeURIComponent(ticket.ticket)}`);
    const result = new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Native RPC timeout')), 45000);
      ws.addEventListener('error', () => {clearTimeout(timer); reject(new Error('Native T3 connection failed'));});
      ws.addEventListener('message', event => {
        let rows; try {rows = JSON.parse(event.data);} catch {return;}
        for (const row of Array.isArray(rows) ? rows : [rows]) {
          if (row._tag === 'Ping') {ws.send(JSON.stringify({_tag: 'Pong'})); continue;}
          if (row._tag !== 'Exit' || String(row.requestId) !== '1') continue;
          clearTimeout(timer);
          if (row.exit?._tag === 'Success') resolve(row.exit.value);
          else reject(new Error('Native RPC rejected: ' + JSON.stringify(row.exit)));
        }
      });
      ws.addEventListener('open', () => {
        const payload = {type: 'thread.turn.start', commandId: state.commandId, threadId,
          message: {messageId: state.messageId, role: 'user', text: fs.readFileSync(messagePath, 'utf8'), attachments: []},
          createdAt: new Date().toISOString()};
        if (selectionPath) payload.modelSelection = JSON.parse(fs.readFileSync(selectionPath, 'utf8'));
        Object.assign(payload, {runtimeMode: 'auto', interactionMode: 'default'});
        ws.send(JSON.stringify({_tag: 'Request', id: '1', tag: 'orchestration.dispatchCommand', payload, headers: []}));
      }, {once: true});
    });
    state.response = await result;
    state.sent = true;
    state.deliveredAt = new Date().toISOString(); persist();
  } finally {
    if (ws) ws.close();
    if (auth?.sessionId) {
      cp.execFileSync('/usr/bin/t3', ['auth', 'session', 'revoke', '--base-dir', '/home/djsm/.t3', auth.sessionId], {stdio: ['ignore', 'pipe', 'pipe']});
      state.temporaryAuthRevoked = true; persist();
    }
  }
  process.stdout.write(JSON.stringify({threadId, sent: state.sent, messageId: state.messageId}) + '\n');
})().catch(error => {process.stderr.write(error.message + '\n'); process.exitCode = 1;});
