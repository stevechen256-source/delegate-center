// 用法: node cdp_enter.mjs <targetId> —— 聚焦 textarea 并真实按 Enter
const [,, targetId] = process.argv;
const targets = await (await fetch('http://127.0.0.1:9222/json/list')).json();
const page = targets.find(t => t.type === 'page' && t.id === targetId);
if (!page) { console.log(JSON.stringify({ error: 'no page' })); process.exit(1); }
const ws = new WebSocket(page.webSocketDebuggerUrl);
let id = 0; const pending = new Map();
ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } };
await new Promise(r => ws.onopen = r);
const send = (method, params) => new Promise(res => { const mid = ++id; pending.set(mid, res); ws.send(JSON.stringify({ id: mid, method, params })); });
await send('Runtime.evaluate', { expression: 'document.querySelector("textarea").focus()' });
await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 });
await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 });
console.log(JSON.stringify({ entered: true }));
ws.close();
