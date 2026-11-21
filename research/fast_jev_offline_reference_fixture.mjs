/** Isolated upstream fast-jev differential fixture: synthetic content only.
 *
 * Run with pinned fast-jev-compaction commit e3f262a7f4d42bd8dd32ced30d26176f7cb545b0,
 * TypeScript loader (tsx), and WITHOUT network or Jev credentials. This imports
 * upstream compact() but supplies a fake JevAsker that never performs IO.
 * JSON output is synthetic input/proposal/decisions for a separate Python audit.
 */
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const pin = {
  'src/compact.ts': '6dcfe27bc00c1ba9065135a61a128bde0da41b9d300dbfd286de3cd6ce8b745b',
  'src/state.ts': '585a9e571340a56d604ea7b90a395f286e575fe24b8394600c38064d972c03dc',
  'src/request.ts': 'aaa0922f89b1a38b304cf8cf922038641338cdae04e1a636c2a618c60c55b53a',
  'src/types.ts': 'dbb401b0f9b09af3a8ec4bb331f2c7141dfa70dc38c3dbfd501183d44930973f',
  'src/index.ts': '5f832b5688b80900fa4ef50a5f9d150137b19c8dceaa7cbe8a1fa2980cc1de4a',
};
const root = process.env.FAST_JEV_PINNED_ROOT;
if (!root?.startsWith('/')) throw new Error('absolute pinned upstream source root required');
for (const [relative, expected] of Object.entries(pin)) {
  const bytes = readFileSync(resolve(root, relative));
  const digest = createHash('sha256').update(bytes).digest('hex');
  if (digest !== expected) throw new Error('pinned source mismatch');
}
const { compact } = await import(pathToFileURL(resolve(root, 'src/compact.ts')).href);
const msg = (role, text, toolUses = [], toolResults = undefined) => ({
  role, text, toolUses, ...(toolResults === undefined ? {} : { toolResults }),
});
const use = (tool_use_id, tool, text) => ({
  tool_use_id, tool, input: { path: 'synthetic/fixture.txt' }, text,
});
const result = (tool_use_id, text, isError = false) => ({ tool_use_id, text, isError });
const original = [
  msg('user','Never edit src/generated. Keep any signed evidence.'),
  msg('assistant','',[use('read_a','Read','A'.repeat(1200))]),
  msg('user','',[],[result('read_a','A'.repeat(1200))]),
  msg('assistant','The first file was fine.'),
  msg('assistant','',[use('grep_b','Grep','B'.repeat(1300))]),
  msg('user','',[],[result('grep_b','B'.repeat(1300))]),
  msg('assistant','',[use('bash_c','Bash','failure: synthetic')]),
  msg('user','',[],[result('bash_c','failure: synthetic',true)]),
  msg('assistant','The failure is in the second file.'),
  msg('user','continue with unchanged constraints'),
];
const localFakeAsker = {
  async ask(_state, questions) {
    const answers = Object.fromEntries(Object.keys(questions).map(key => {
      const isFirst = /_t1$/.test(key);
      const isSecond = /_t2$/.test(key);
      const keepCall = key.startsWith('call_');
      const noul = isFirst ? 0 : isSecond ? (keepCall ? 1 : 0) : 1;
      return [key, { noul }];
    }));
    return { answers };
  }
};
const proposed = await compact(original,localFakeAsker,{
  preserveRecentMessages:2,
  truncateHeadChars:300,
  keepThreshold:0.5,
});
const idFor = new Map([['t1','read_a'],['t2','grep_b'],['t3','bash_c']]);
const decisions = Object.fromEntries(proposed.decisions.map(x => [
  idFor.get(x.id) ?? (() => { throw new Error('unexpected tool id'); })(),
  x.action
]));
if (Object.keys(decisions).length!==3) throw new Error('unexpected decision count');
console.log(JSON.stringify({ original, proposed:proposed.messages, decisions, upstream_stats:proposed.stats }));
