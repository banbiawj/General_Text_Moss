import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const html = readFileSync(new URL('../index.html', import.meta.url), 'utf8');

const answerBranchMatch = html.match(
  /<template v-if="isAnswerMessage\(msg\)">([\s\S]*?)<\/template>\s*<template v-else>/
);

assert.ok(answerBranchMatch, 'answer message branch should be present');

const answerBranch = answerBranchMatch[1];

assert.ok(
  answerBranch.includes('<div class="flex gap-4 max-w-[85%]">'),
  'answer messages should use the shared left-aligned AI message shell'
);

assert.ok(
  !answerBranch.includes('class="max-w-3xl mx-auto flex gap-4"'),
  'answer messages should not use the old centered shell'
);

assert.ok(
  answerBranch.includes('chat-markdown text-[15px] leading-relaxed text-gray-800'),
  'answer body should keep the unbubbled markdown styling'
);

assert.ok(
  html.includes('const waitingStageDefinitions = {'),
  'index should define SSE waiting stage definitions'
);

for (const [node, label] of [
  ['intent', 'Understanding request'],
  ['task_assemble', 'Reading context'],
  ['execute', 'Preparing response'],
  ['tools', 'Applying document changes'],
  ['task_advance', 'Checking next step']
]) {
  assert.ok(
    html.includes(`${node}: '${label}'`),
    `waiting stage definitions should map ${node} to "${label}"`
  );
}

assert.ok(
  html.includes("if (event === 'node_start')"),
  'stream reader should handle node_start events'
);

assert.ok(
  html.includes('startWaitingStage(data.node);'),
  'node_start should start the matching waiting stage'
);

assert.ok(
  html.includes('completeWaitingStage(data.node);'),
  'node_end should mark the matching waiting stage complete'
);

assert.ok(
  html.includes('v-for="stage in waitingStages"'),
  'waiting UI should render the current SSE-driven stages'
);

assert.ok(
  html.includes('moss-waiting-avatar'),
  'waiting UI should include the breathing Moss avatar'
);

assert.ok(
  html.includes('fa-solid fa-asterisk'),
  'waiting UI should reuse the existing Font Awesome Moss asterisk icon'
);
