import { spawn } from 'node:child_process';
import { mkdtemp, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import net from 'node:net';

const base = process.env.UI_URL || 'http://127.0.0.1:4173';
const out = dirname(fileURLToPath(import.meta.url));
const chrome = process.env.CHROME_PATH || ['C:/Program Files/Google/Chrome/Application/chrome.exe', 'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe'].find(existsSync);
if (!chrome) throw new Error('Installed Chrome was not found; set CHROME_PATH.');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function poll(callback, timeout = 10000) {
  const until = Date.now() + timeout;
  let last;
  while (Date.now() < until) {
    try { const value = await callback(); if (value) return value; } catch (error) { last = error; }
    await pause(100);
  }
  throw last || new Error('Timed out waiting for local browser/server.');
}
await poll(async () => (await fetch(base)).ok);
const port = await new Promise(resolve => { const server = net.createServer(); server.listen(0, '127.0.0.1', () => { const p = server.address().port; server.close(() => resolve(p)); }); });
const profile = await mkdtemp(join(tmpdir(), 'agentautomation-ui-smoke-'));
const child = spawn(chrome, ['--headless=new', '--no-first-run', '--no-default-browser-check', '--disable-background-networking', `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`, 'about:blank'], { stdio: 'ignore' });
let socket;
let command;
const errors = [];
const passed = [];
try {
  const tabs = await poll(async () => { const response = await fetch(`http://127.0.0.1:${port}/json/list`); const list = await response.json(); return list.find(tab => tab.type === 'page')?.webSocketDebuggerUrl; });
  socket = new WebSocket(tabs);
  await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, { once: true }); socket.addEventListener('error', reject, { once: true }); });
  let next = 0;
  const pending = new Map();
  socket.addEventListener('message', event => {
    const data = JSON.parse(event.data);
    if (data.method === 'Runtime.exceptionThrown') errors.push(data.params.exceptionDetails.exception?.description || data.params.exceptionDetails.text);
    if (data.method === 'Runtime.consoleAPICalled' && data.params.type === 'error') errors.push(data.params.args.map(x => x.value || x.description).join(' '));
    if (data.id) { const item = pending.get(data.id); if (item) { pending.delete(data.id); clearTimeout(item.timer); data.error ? item.reject(new Error(data.error.message)) : item.resolve(data.result); } }
  });
  command = (method, params = {}) => new Promise((resolve, reject) => { const id = ++next; const timer = setTimeout(() => { pending.delete(id); reject(new Error(`CDP timeout: ${method}`)); }, 10000); pending.set(id, { resolve, reject, timer }); socket.send(JSON.stringify({ id, method, params })); });
  const evaluate = async expression => { const result = await command('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }); if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text); return result.result.value; };
  const click = selector => evaluate(`(() => { const el = document.querySelector(${JSON.stringify(selector)}); if (!el) throw new Error('Missing target: ' + ${JSON.stringify(selector)}); el.click(); return true; })()`);
  const fill = (selector, value, change = false) => evaluate(`(() => { const el = document.querySelector(${JSON.stringify(selector)}); if (!el) throw new Error('Missing field'); el.value = ${JSON.stringify(value)}; el.dispatchEvent(new Event(${JSON.stringify(change ? 'change' : 'input')}, { bubbles: true })); return true; })()`);
  const assert = async (name, expression) => { if (!await evaluate(expression)) throw new Error(`Assertion failed: ${name}`); passed.push(name); console.log(`PASS ${name}`); };
  await command('Runtime.enable');
  await command('Page.enable');
  await command('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1080, deviceScaleFactor: 1, mobile: false });
  await command('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-color-scheme', value: 'light' }] });
  await command('Page.navigate', { url: base });
  await poll(() => evaluate(`!!document.querySelector('.task-row')`));
  await assert('initial task list and simulation notice', `document.querySelectorAll('.task-row').length === 7 && document.querySelector('.demo-banner').textContent.includes('模拟数据')`);
  await assert('desktop no horizontal overflow', `document.documentElement.scrollWidth <= innerWidth`);
  if (!existsSync(join(out, 'preview-desktop.png'))) {
    const desktop = await command('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    await writeFile(join(out, 'preview-desktop.png'), Buffer.from(desktop.data, 'base64'));
  }

  await click('[data-task="TASK-103"]');
  await click('[data-detail="review"]');
  await assert('task selection and review tab', `document.querySelector('.inspector').textContent.includes('Review 证据预览')`);
  await click('[data-detail="logs"]');
  await assert('logs tab', `document.querySelector('.log-box').textContent.includes('独立 Review 会话')`);
  await click('[data-task="TASK-104"]');
  await click('.inspector-footer [data-action="merge"]');
  await assert('integration approval dialog', `document.querySelector('dialog[open]').textContent.includes('批准模拟合并')`);
  await evaluate(`document.querySelector('#dialog-form').requestSubmit()`);
  await assert('approval marks task completed', `document.querySelector('[data-task="TASK-104"]').textContent.includes('已完成')`);

  await click('[data-action="new-task"]');
  await fill('#new-task-title', '浏览器交互验证任务');
  await fill('#new-task-acceptance', '创建记录后可在列表查看');
  await assert('new task has submit button', `!!document.querySelector('#dialog-form button[type="submit"]')`);
  await click('.modal-footer [data-action="close-dialog"]');
  await assert('cancel does not submit valid new task form', `!document.querySelector('dialog[open]') && document.querySelectorAll('.task-row').length === 7`);
  await click('[data-action="new-task"]');
  await fill('#new-task-title', '浏览器交互验证任务');
  await fill('#new-task-acceptance', '创建记录后可在列表查看');
  await click('#dialog-form button[type="submit"]');
  await assert('new task created', `document.querySelectorAll('.task-row').length === 8 && document.querySelector('.inspector h2').textContent.includes('浏览器交互验证任务')`);
  await click('[data-action="toggle-run"]');
  await click('[data-action="start-task"]');
  await assert('pause blocks starting queued task', `document.querySelector('.run-strip').textContent.includes('运行已暂停') && document.querySelector('.inspector .status').textContent.includes('待执行')`);
  await click('[data-action="toggle-run"]');

  await click('.nav-item[data-view="flow"]');
  await fill('#node-agent', 'claude', true);
  await click('[data-action="save-node"]');
  await assert('flow node agent configuration saved', `document.querySelector('.flow-node.selected').textContent.includes('Claude Code')`);
  await click('[data-action="add-node"]');
  await fill('#new-node-label', '浏览器验证审批');
  await evaluate(`document.querySelector('#dialog-form').requestSubmit()`);
  await assert('flow node appended', `document.querySelector('.flow-node.selected').textContent.includes('浏览器验证审批')`);

  await click('.nav-item[data-view="integration"]');
  await click('[data-action="cleanup"][data-id="TASK-101"]');
  await assert('cleanup requires confirmation', `document.querySelector('dialog[open]').textContent.includes('确认模拟清理')`);
  await evaluate(`document.querySelector('#dialog-form').requestSubmit()`);
  await assert('cleanup recorded without erasing result', `document.querySelector('.cleanup-row').textContent.includes('已清理') && document.querySelector('.cleanup-row').textContent.includes('TASK-101')`);

  await fill('#project-select', 'project-paper', true);
  await click('.nav-item[data-view="tasks"]');
  await assert('project switch shows empty task state', `document.querySelector('h1').textContent.includes('任务工作台') && document.querySelector('.task-list').textContent.includes('没有匹配的任务')`);
  await click('[data-action="reset"]');
  await command('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  await evaluate(`document.documentElement.dataset.theme = 'dark'`);
  await assert('mobile no horizontal overflow', `document.documentElement.scrollWidth <= innerWidth`);
  await assert('dark mode applied', `getComputedStyle(document.documentElement).colorScheme.includes('dark')`);
  await poll(() => evaluate(`document.querySelectorAll('#toasts .toast').length === 0`), 6000);
  const mobile = await command('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  await writeFile(join(out, 'preview-mobile.png'), Buffer.from(mobile.data, 'base64'));
  if (errors.length) throw new Error(`Browser exceptions: ${errors.join('\n')}`);
  console.log(`PASS ${passed.length} browser interactions/layout checks; no JS exceptions. Screenshots: ui/tests/preview-desktop.png and preview-mobile.png`);
} finally {
  if (command && socket?.readyState === WebSocket.OPEN) {
    try { await command('Browser.close'); } catch {}
  }
  socket?.close();
  if (child.exitCode === null) child.kill();
  console.log(`Chrome used isolated temporary profile: ${profile}`);
}
