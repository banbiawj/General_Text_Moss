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
  html.includes('.chat-markdown :where(ul) { margin: 0.5rem 0 0.75rem; padding-left: 1.25rem; list-style: disc; }'),
  'unordered markdown lists should keep the compact chat indentation'
);

assert.ok(
  html.includes('.chat-markdown :where(ol) { margin: 0.5rem 0 0.75rem; padding-left: 2.5rem; list-style: decimal outside; }'),
  'ordered markdown lists should reserve enough left padding for two-digit markers'
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
  html.includes('moss-waiting-avatar-slot'),
  'waiting UI should reserve layout space around the breathing Moss avatar'
);

assert.ok(
  html.includes('.moss-waiting-avatar-slot'),
  'waiting avatar slot styles should define the reserved halo space'
);

assert.ok(
  html.includes('fa-solid fa-asterisk'),
  'waiting UI should reuse the existing Font Awesome Moss asterisk icon'
);

assert.ok(
  html.includes('.editor-loading-overlay'),
  'editor waiting overlay should define its own scheme-A loading overlay style'
);

assert.ok(
  html.includes('editor-loading-stage-inline'),
  'editor document overlay should use the selected B2 inline avatar/text layout'
);

assert.ok(
  html.includes('.editor-loading-text'),
  'editor waiting overlay should include startup-style loading text'
);

assert.ok(
  html.includes('moss working ...'),
  'editor document overlay should use Moss working copy'
);

assert.ok(
  !html.includes('bg-white/40 backdrop-blur-[1px]'),
  'editor waiting overlay should not use the old translucent Vue-like mask'
);

assert.ok(
  !html.includes('fa-solid fa-circle-notch fa-spin text-gray-400"></i> AI is thinking'),
  'editor waiting overlay should not use the old spinner pill'
);

const editorOverlayStyleMatch = html.match(/\.editor-loading-overlay \{([\s\S]*?)\n        \}/);

assert.ok(
  editorOverlayStyleMatch,
  'editor waiting overlay style block should be present'
);

const editorOverlayStyle = editorOverlayStyleMatch[1];

assert.ok(
  editorOverlayStyle.includes('align-items: center;'),
  'editor waiting overlay should center content within the current editor area'
);

assert.ok(
  editorOverlayStyle.includes('padding: clamp(1rem, 3vh, 1.5rem);'),
  'editor waiting overlay should use bounded padding instead of viewport-based top offset'
);

assert.ok(
  !editorOverlayStyle.includes('align-items: flex-start;'),
  'editor waiting overlay should not top-align loading content'
);

assert.ok(
  !editorOverlayStyle.includes('padding-top: clamp(8rem, 36vh, 19rem);'),
  'editor waiting overlay should not use viewport-height top padding that clips in short panels'
);

assert.ok(
  html.includes('<div class="moss-waiting-avatar-slot editor-loading-avatar-slot">'),
  'editor document overlay should reuse the chat waiting avatar slot'
);

assert.ok(
  html.includes('<div class="moss-waiting-avatar w-8 h-8 rounded-full border border-gray-200 bg-white flex items-center justify-center">'),
  'editor document overlay should reuse the exact chat waiting Moss avatar classes'
);

assert.ok(
  html.includes('<i class="fa-solid fa-asterisk text-gray-600 text-xs"></i>'),
  'editor document overlay should reuse the Font Awesome Moss asterisk icon'
);

assert.ok(
  !html.includes('.editor-loading-mark'),
  'editor document overlay should not use a separate hand-drawn mark style'
);

assert.ok(
  !html.includes('editor-loading-steps'),
  'editor document overlay should not show waiting stage rows'
);

assert.ok(
  !html.includes('editor-loading-step'),
  'editor document overlay should remove per-stage row styles'
);

assert.ok(
  !html.includes(':class="{ \'is-compact\': panelHeight < 360 }"'),
  'editor document overlay should not need compact state after removing stage rows'
);

assert.ok(
  html.includes('const isDocumentModifying = ref(false);'),
  'index should track document mutation separately from general agent busy state'
);

assert.ok(
  html.includes('<div v-if="isDocumentModifying"'),
  'editor loading overlay should only render while the agent is applying document mutations'
);

assert.ok(
  !html.includes('<div v-if="isModifying"\n                 class="editor-loading-overlay"'),
  'editor loading overlay should not be tied to the general waiting state'
);

assert.ok(
  html.includes("if (event === 'dom_mutation') {\n                            isDocumentModifying.value = true;\n                            await applyDomMutation(data);\n                        }"),
  'document overlay should open only when a dom_mutation event is applied'
);

assert.ok(
  html.includes('isDocumentModifying.value = false;\n                    isThinking.value = false;'),
  'document overlay should close in the stream cleanup path'
);

assert.ok(
  html.includes('isDocumentModifying,'),
  'document mutation state should be returned to the Vue template'
);
