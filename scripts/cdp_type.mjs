// 用法: node cdp_type.mjs <url片段> <文本> [--enter]
const [,, urlPart, textArg, flag] = process.argv;
const text = textArg || '';
const targets = await (await fetch('http://127.0.0.1:9222/json/list')).json();
const page = targets.find(t => t.type === 'page' && t.url.includes(urlPart));
if (!page) { console.log(JSON.stringify({ error: 'no page: ' + urlPart })); process.exit(1); }
const ws = new WebSocket(page.webSocketDebuggerUrl);
let id = 0;
const pending = new Map();
ws.onmessage = (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
};
await new Promise(r => ws.onopen = r);
const send = (method, params) => new Promise(res => {
  const mid = ++id; pending.set(mid, res);
  ws.send(JSON.stringify({ id: mid, method, params }));
});
// 1. 聚焦输入框
await send('Runtime.evaluate', { expression: 'document.querySelector("[contenteditable=true]").focus()' });
// 2. 真实键盘输入（模拟真人打字）
await send('Input.insertText', { text });
// 3. 可选: 真实 Enter
if (flag === '--enter') {
  await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 });
  await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 });
}
// 4. 检查输入框内容
const chk = await send('Runtime.evaluate', { expression: '({ len: document.querySelector("[contenteditable=true]").innerText.length, sendDisabled: (document.querySelector("button[aria-label=发送消息]")||{}).disabled })', returnByValue: true });
console.log(JSON.stringify(chk.result.result.value));
ws.close();
