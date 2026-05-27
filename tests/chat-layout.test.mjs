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
