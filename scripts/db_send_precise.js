(() => {
  // 找发送按钮：箭头图标（svg path d 以 M4.93934 开头）
  const all = [...document.querySelectorAll("button")];
  const send = all.find(b => {
    const p = b.querySelector("svg path");
    return p && (p.getAttribute("d")||"").startsWith("M4.93934");
  });
  if (!send) return { error: "no send btn", candidates: all.filter(b=>b.querySelector("svg")).length };
  send.click();
  return { ok: true };
})()
