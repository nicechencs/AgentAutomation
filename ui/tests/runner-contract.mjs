import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import { creationIssue, parseRunForm } from '../src/runner.js';

const healthy = { status: 'ok', mode: 'real', modes: ['real', 'fake'], templates: ['development', 'local-document'], agents: { codex: true, claude: true }, n8n: { reachable: true, configured: true, url: 'http://localhost:5678' } };
const form = overrides => {
  const values = { goal: 'Build a tested feature', template: 'development', repository_path: 'D:\\projects\\repo', target_branch: '', agent: 'codex', model: 'explicit-code-model', reviewer: 'claude', review_model: 'explicit-review-model', check_commands: '[["python","-m","pytest"]]', max_cycles: '20', max_fixes: '1', deadline_seconds: '1800', ...overrides };
  return { get: key => values[key] ?? null };
};
const payload = parseRunForm(form());
assert.equal(payload.mode, 'real');
assert.equal(payload.max_concurrency, 1);
assert.equal(payload.target_branch, null);
assert.equal(payload.deadline_seconds, 1800);
assert.deepEqual(payload.check_commands, [['python', '-m', 'pytest']]);
assert.equal(payload.model, 'explicit-code-model');
assert.equal(payload.review_model, 'explicit-review-model');
assert.equal(parseRunForm(form({ template: 'local-document', repository_path: '' })).repository_path, null);
assert.equal(parseRunForm(form({ mode: 'fake', model: '', review_model: '', repository_path: '', check_commands: '[]' })).mode, 'fake');
for (const overrides of [ { goal: ' ' }, { mode: 'other' }, { repository_path: '' }, { model: '' }, { review_model: '' }, { reviewer: 'other' }, { check_commands: 'python -m pytest' }, { check_commands: '["python -m pytest"]' }, { check_commands: '[[]]' }, { check_commands: '[["python",3]]' }, { check_commands: '[]' }, { max_fixes: '4' }, { max_fixes: '-1' }, { max_fixes: '1.5' }, { deadline_seconds: '3601' }, { max_cycles: '31' } ]) assert.throws(() => parseRunForm(form(overrides)), JSON.stringify(overrides));
assert.equal(parseRunForm(form({ max_fixes: '0', deadline_seconds: '3600' })).max_fixes, 0);
assert.equal(creationIssue(healthy, 'real', 'codex', 'claude'), '');
assert.ok(creationIssue({ ...healthy, n8n: undefined }, 'real', 'codex', 'claude'));
assert.ok(creationIssue({ ...healthy, agents: undefined }, 'real', 'codex', 'claude'));
assert.ok(creationIssue({ ...healthy, modes: ['fake'] }, 'real', 'codex', 'claude'));
assert.equal(creationIssue({ ...healthy, agents: {} }, 'fake', 'codex', 'claude'), '');
assert.ok(creationIssue({ ...healthy, n8n: { configured: true, reachable: false } }, 'fake', 'codex', 'claude'));

const stageSpecs = {
  development: [['plan', 'created', 'planned', null], ['code', 'planned', 'coded', 'plan'], ['check', 'coded', 'checked', null], ['review', 'checked', 'reviewed', null], ['commit', 'reviewed', 'committed', null], ['integrate', 'committed', 'integrated', 'integration'], ['archive', 'integrated', 'archived', null]],
  'local-document': [['plan', 'created', 'planned', null], ['code', 'planned', 'coded', 'plan'], ['check', 'coded', 'checked', null], ['review', 'checked', 'reviewed', null], ['archive', 'reviewed', 'archived', 'artifact']],
};
let gates = 0;
for (const [template, stages] of Object.entries(stageSpecs)) {
  const workflow = JSON.parse(await readFile(new URL(`../../workflows/${template}.json`, import.meta.url), 'utf8'));
  assert.equal(workflow.settings.executionTimeout, 3900);
  for (const node of workflow.nodes.filter(node => node.type === 'n8n-nodes-base.httpRequest')) {
    assert.ok(node.parameters.url.includes('http://host.docker.internal:8000'));
    assert.equal(node.parameters.options.timeout, 10000);
  }
  for (const [stage, before, after, approval] of stages) {
    const gate = workflow.nodes.find(node => node.name.startsWith(`Gate ${stage}`));
    assert.ok(gate);
    const evaluate = run => vm.runInNewContext(`(function () { ${gate.parameters.jsCode}\n})()`, { $input: { first: () => ({ json: run }) }, $: () => ({ first: () => ({ json: { run_id: 'run_1' } }) }) })[0].json._gate;
    for (const mode of ['real', 'fake']) {
      const base = { id: 'run_1', template, mode, phase: before, status: 'running', approvals: approval ? [{ kind: approval, status: 'approved' }] : [], checks: { status: 'passed', source: mode, candidate: 'candidate-1' }, review: { verdict: 'pass', source: mode, candidate: 'candidate-1' } };
      assert.equal(evaluate(base), 'execute');
      assert.equal(evaluate({ ...base, inflight: stage }), 'wait');
      assert.throws(() => evaluate({ ...base, inflight: 'other' }), /Unexpected inflight/);
      assert.equal(evaluate({ ...base, status: 'paused' }), 'wait');
      assert.equal(evaluate({ ...base, status: 'failed' }), 'stop');
      assert.equal(evaluate({ ...base, phase: after, requests: { [`run_1:${stage}`]: stage }, stages: { [stage]: { status: 'completed' } } }), 'advance');
      assert.throws(() => evaluate({ ...base, phase: after }), /receipt/);
      assert.throws(() => evaluate({ ...base, mode: 'unknown' }), /mode mismatch/);
      if (['review', 'commit', 'integrate', 'archive'].includes(stage)) {
        assert.throws(() => evaluate({ ...base, checks: { ...base.checks, source: mode === 'real' ? 'fake' : 'real' } }), /check evidence/);
        if (mode === 'real') {
          for (const candidate of [undefined, null, '', ' ', 2]) assert.throws(() => evaluate({ ...base, checks: { ...base.checks, candidate } }), /check evidence/);
          assert.equal(evaluate({ ...base, inflight: stage, checks: null }), 'wait');
        }
      }
      if (['commit', 'integrate', 'archive'].includes(stage)) {
        assert.throws(() => evaluate({ ...base, review: { ...base.review, source: mode === 'real' ? 'fake' : 'real' } }), /review evidence/);
        if (mode === 'real') assert.throws(() => evaluate({ ...base, review: { ...base.review, candidate: '' } }), /review evidence/);
      }
      if (approval) {
        assert.equal(evaluate({ ...base, status: 'awaiting_approval', approvals: [{ kind: approval, status: 'pending' }] }), 'wait');
        assert.throws(() => evaluate({ ...base, approvals: [] }), /Missing required approval/);
      }
      gates++;
    }
    const post = workflow.nodes.find(node => node.name === `POST ${stage}`);
    assert.ok(post.parameters.jsonBody.includes(`run_id + ':${stage}'`));
    assert.deepEqual(workflow.connections[`POST ${stage}`].main[0].map(link => link.node), [`GET before ${stage}`]);
  }
}
console.log(`Runner contract passed: form validation, health prerequisites, ${gates} real/fake workflow gates.`);
