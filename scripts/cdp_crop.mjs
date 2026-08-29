// 用法: node cdp_crop.mjs <targetId> <输出png> —— 裁剪 3x3 网格元素（1:1）
import fs from 'node:fs';
const [,, targetId, out] = process.argv;
const targets = await (await fetch('http://127.0.0.1:9222/json/list')).json();
const page = targets.find(t => t.type === 'page' && t.id === targetId);
if (!page) { console.log(JSON.stringify({ error: 'no page by id' })); process.exit(1); }
const ws = new WebSocket(page.webSocketDebuggerUrl);
let id = 0; const pending = new Map();
ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); } };
await new Promise(r => ws.onopen = r);
const send = (method, params) => new Promise(res => { const mid = ++id; pending.set(mid, res); ws.send(JSON.stringify({ id: mid, method, params })); });
const probe = await send('Runtime.evaluate', { expression: `(() => {
  const all = [...document.querySelectorAll("div")];
  const grid = all.find(d => {
    const kids = [...d.children];
    return kids.length === 9 && kids.every(k => { const r = k.getBoundingClientRect(); return r.width > 60 && r.width < 140 && r.height > 60 && r.height < 140; });
  });
  if (!grid) return null;
  const r = grid.getBoundingClientRect();
  return { x: r.x, y: r.y, w: r.width, h: r.height };
})()`, returnByValue: true });
const rect = probe.result.result.value;
if (!rect) { console.log(JSON.stringify({ error: 'grid not found' })); process.exit(1); }
await send('Page.enable');
const shot = await send('Page.captureScreenshot', { format: 'png', clip: { x: rect.x, y: rect.y, width: rect.w, height: rect.h, scale: 1 }, captureBeyondViewport: false });
fs.writeFileSync(out, Buffer.from(shot.result.data, 'base64'));
console.log(JSON.stringify({ saved: out, rect }));
ws.close();
