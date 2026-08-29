// 用法: node cdp_click.mjs <targetId> <x,y> [<x,y> ...] —— 真实鼠标点击坐标
const [,, targetId, ...points] = process.argv;
const targets = await (await fetch('http://127.0.0.1:9222/json/list')).json();
const page = targets.find(t => t.type === 'page' && t.id === targetId);
if (!page) { console.log(JSON.stringify({ error: 'no page by id' })); process.exit(1); }
const ws = new WebSocket(page.webSocketDebuggerUrl);
let id = 0; const pending = new Map();
ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } };
await new Promise(r => ws.onopen = r);
const send = (method, params) => new Promise(res => { const mid = ++id; pending.set(mid, res); ws.send(JSON.stringify({ id: mid, method, params })); });
const results = [];
for (const p of points) {
  const [x, y] = p.split(',').map(Number);
  await send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 });
  await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 });
  results.push({ x, y });
  await new Promise(r => setTimeout(r, 400));
}
console.log(JSON.stringify({ clicked: results }));
ws.close();
