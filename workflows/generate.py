"""Generate static n8n workflow JSON only; never reads env or manages credentials."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CREDENTIAL = {"httpHeaderAuth": {"id": "aa-runner-header", "name": "aa-runner-header"}}
TERMINAL = ["completed", "rejected", "cancelled", "failed", "timed_out", "interrupted"]
PHASES = ["created", "planned", "coded", "checked", "reviewed", "committed", "integrated", "archived"]


def generate(template, stages):
    nodes, connections = [], {}

    def node(name, kind, version, params, x, y=0, **extra):
        nodes.append({"id": name.lower().replace(" ", "-"), "name": name,
                      "type": "n8n-nodes-base." + kind, "typeVersion": version,
                      "position": [x, y], "parameters": params, **extra})

    def link(source, target, output=0):
        outputs = connections.setdefault(source, {"main": []})["main"]
        while len(outputs) <= output:
            outputs.append([])
        outputs[output].append({"node": target, "type": "main", "index": 0})

    def condition(name, action, x, y):
        node(name, "if", 2.2, {"conditions": {"options": {"caseSensitive": True,
             "leftValue": "", "typeValidation": "strict", "version": 2},
             "conditions": [{"id": name, "leftValue": "={{ $json._gate }}", "rightValue": action,
                             "operator": {"type": "string", "operation": "equals"}}],
             "combinator": "and"}, "options": {}}, x, y)

    node("Production webhook", "webhook", 2.1, {"httpMethod": "POST",
         "path": "agentautomation-" + template, "authentication": "headerAuth",
         "responseMode": "onReceived", "options": {"responseCode": {"values": {"responseCode": 202}}}}, 0,
         webhookId="agentautomation-" + template,
         credentials={"httpHeaderAuth": {"id": "aa-webhook-header", "name": "aa-webhook-header"}})
    node("Validate webhook", "code", 2, {"mode": "runOnceForAllItems", "language": "javaScript",
         "jsCode": "const body = $input.first().json.body;\nif (!body || typeof body.run_id !== 'string' || !/^[A-Za-z0-9_-]{1,100}$/.test(body.run_id)) { throw new Error('Invalid webhook run_id'); }\nreturn [{json: {run_id: body.run_id}}];"}, 220)
    node("Stopped terminal", "noOp", 1, {}, 220, -300)
    link("Production webhook", "Validate webhook")
    previous = "Validate webhook"
    for i, (stage, before, after, approval) in enumerate(stages):
        label = stage + (" (" + approval + " approval)" if approval else "")
        get, gate = "GET before " + stage, "Gate " + label
        stop, wait, advance = "Terminal " + stage, "Waiting " + stage, "Finished " + stage
        delay, post = "Wait 2s " + stage, "POST " + stage
        x, y = 500 + i * 1400, i * 180
        node(get, "httpRequest", 4.2, {"method": "GET",
             "url": "={{ 'http://host.docker.internal:8000/api/runs/' + encodeURIComponent($('Validate webhook').first().json.run_id) }}",
             "authentication": "genericCredentialType", "genericAuthType": "httpHeaderAuth",
             "options": {"timeout": 10000, "response": {"response": {"responseFormat": "json", "neverError": False}}}},
             x, y, credentials=CREDENTIAL)
        script = f"""const run = $input.first().json;
const runId = $('Validate webhook').first().json.run_id;
const terminal = {json.dumps(TERMINAL)};
const statuses = ['dispatching', 'running', 'awaiting_approval', 'paused', ...terminal];
const phases = {json.dumps(PHASES)};
if (!run || run.id !== runId || run.template !== {json.dumps(template)} || !['real','fake'].includes(run.mode)) throw new Error('Run identity/template/mode mismatch');
if (!statuses.includes(run.status)) throw new Error('Unknown run status');
if (terminal.includes(run.status)) return [{{json: {{...run, _gate: 'stop'}}}}];
if (run.inflight != null && run.inflight !== {json.dumps(stage)}) throw new Error('Unexpected inflight stage');
if (!phases.includes(run.phase) || ![{json.dumps(before)}, {json.dumps(after)}].includes(run.phase)) throw new Error('Illegal phase before {stage}');
if (!Array.isArray(run.approvals) || run.approvals.some(a => !a || !['plan','integration','artifact'].includes(a.kind) || !['pending','approved','rejected'].includes(a.status))) throw new Error('Malformed approvals');
if (new Set(run.approvals.map(a => a.kind)).size !== run.approvals.length) throw new Error('Duplicate approval kinds');
if (run.approvals.some(a => a.status === 'rejected')) throw new Error('Rejected approval without terminal status');
const pending = run.approvals.some(a => a.status === 'pending');
if (run.status === 'awaiting_approval' && !pending) throw new Error('Awaiting approval without pending approval');
const requestId = runId + ':{stage}';
if (run.inflight === {json.dumps(stage)}) return [{{json: {{...run, _gate: 'wait'}}}}];
if (run.phase === {json.dumps(after)}) {{
  if (!run.requests || run.requests[requestId] !== {json.dumps(stage)} || !run.stages || run.stages[{json.dumps(stage)}]?.status !== 'completed') throw new Error('Phase advanced without matching stage receipt');
  return [{{json: {{...run, _gate: 'advance'}}}}];
}}
const candidatePresent = evidence => run.mode !== 'real' || (typeof evidence?.candidate === 'string' && evidence.candidate.trim().length > 0);
if (['review','commit','integrate','archive'].includes({json.dumps(stage)}) && (run.checks?.status !== 'passed' || run.checks?.source !== run.mode || !candidatePresent(run.checks))) throw new Error('Missing passing check evidence for run mode');
if (['commit','integrate','archive'].includes({json.dumps(stage)}) && (run.review?.verdict !== 'pass' || run.review?.source !== run.mode || !candidatePresent(run.review))) throw new Error('Missing passing review evidence for run mode');
const requiredKind = {json.dumps(approval)};
if (requiredKind && !run.approvals.some(a => a.kind === requiredKind)) throw new Error('Missing required approval');
if (run.status === 'paused' || pending) return [{{json: {{...run, _gate: 'wait'}}}}];
if (!['dispatching','running'].includes(run.status)) throw new Error('Illegal executable status');
if (requiredKind && !run.approvals.some(a => a.kind === requiredKind && a.status === 'approved')) throw new Error('Required approval not approved');
return [{{json: {{...run, _gate: 'execute'}}}}];"""
        node(gate, "code", 2, {"mode": "runOnceForAllItems", "language": "javaScript", "jsCode": script}, x+220, y)
        condition(stop, "stop", x+440, y)
        condition(advance, "advance", x+660, y)
        condition(wait, "wait", x+880, y)
        node(delay, "wait", 1.1, {"resume": "timeInterval", "amount": 2, "unit": "seconds"}, x+1100, y+130,
             webhookId="aa-wait-" + template + "-" + stage)
        node(post, "httpRequest", 4.2, {"method": "POST",
             "url": "={{ 'http://host.docker.internal:8000/api/runs/' + encodeURIComponent($('Validate webhook').first().json.run_id) + '/stages/" + stage + "' }}",
             "authentication": "genericCredentialType", "genericAuthType": "httpHeaderAuth",
             "sendBody": True, "contentType": "json", "specifyBody": "json",
             "jsonBody": "={{ { request_id: $('Validate webhook').first().json.run_id + ':" + stage + "', execution_id: $execution.id } }}",
             "options": {"timeout": 10000, "response": {"response": {"responseFormat": "json", "neverError": False}}}},
             x+1100, y, credentials=CREDENTIAL)
        link(previous, get, 0 if i == 0 else 0)
        link(get, gate)
        link(gate, stop)
        link(stop, "Stopped terminal", 0)
        link(stop, advance, 1)
        link(advance, wait, 1)
        link(wait, delay, 0)
        link(wait, post, 1)
        link(delay, get)
        link(post, get)
        previous = advance
    # Archive ends in terminal completed. This branch is a defensive assertion.
    node("Unexpected nonterminal archive", "stopAndError", 1,
         {"errorType": "errorMessage", "errorMessage": "Archive receipt must have terminal status"}, 500 + len(stages)*1400)
    link(previous, "Unexpected nonterminal archive")
    data = {"id": "agentautomation-" + template, "name": "AgentAutomation / " + template,
            "active": False, "nodes": nodes, "connections": connections,
            "settings": {"executionOrder": "v1", "executionTimeout": 3900}, "pinData": {}, "tags": []}
    (ROOT / (template + ".json")).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


generate("development", [("plan", "created", "planned", None), ("code", "planned", "coded", "plan"),
         ("check", "coded", "checked", None), ("review", "checked", "reviewed", None),
         ("commit", "reviewed", "committed", None), ("integrate", "committed", "integrated", "integration"),
         ("archive", "integrated", "archived", None)])
generate("local-document", [("plan", "created", "planned", None), ("code", "planned", "coded", "plan"),
         ("check", "coded", "checked", None), ("review", "checked", "reviewed", None),
         ("archive", "reviewed", "archived", "artifact")])
