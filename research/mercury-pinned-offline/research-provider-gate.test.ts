import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { startOfflineResearchRound, ResearchGateDenied,
  type ResearchPermit, type ResearchAuthority, type ResearchStream
} from '../src/bots/research-provider-gate.ts';

const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));
const basic: ResearchPermit = {
  leaseId: 'fixture-lease-001', taskId: 'task-0001',
  nodeId: 'synthetic-node-1', role: 'repository-reviewer',
  attemptId: 'attempt-0001', modelId: 'mercury-fixture/no-generation',
  upstreamGroupId: 'synthetic-upstream-only', authorityEpoch: 1,
  reservedInput: 6000, reservedOutput: 800,
  expiresAt: Date.now() + 120_000,
};
const expected = (({
  taskId, nodeId, role, attemptId, modelId, upstreamGroupId,
  authorityEpoch, reservedInput, reservedOutput,
}: ResearchPermit) => ({ taskId, nodeId, role, attemptId, modelId,
  upstreamGroupId, authorityEpoch, reservedInput, reservedOutput }))(basic);

function fixtureAuthority() {
  const state = { renewals: 0, released: 0, witnessed: 0,
    denied: false, witnessDenied: false, hang: false, override: null as ResearchPermit | null };
  const authority: ResearchAuthority = {
    async renew(lease) {
      state.renewals++;
      if (state.hang) return new Promise<ResearchPermit | null>(() => {});
      if (state.denied) return null;
      return state.override ?? lease;
    },
    async acknowledge(event) {
      state.witnessed++;
      assert.equal(event.taskId, basic.taskId);
      assert.equal(event.leaseId, basic.leaseId);
      return !state.witnessDenied;
    },
    async release() { state.released++; },
  };
  return { state, authority };
}

function finite(signal: AbortSignal): ResearchStream<any> {
  return {
    fullStream: (async function* () {
      if (signal.aborted) throw Error('aborted');
      yield { type: 'text-delta', text: 'safe' };
      if (signal.aborted) throw Error('aborted');
      yield { type: 'text-delta', text: 'fixture' };
    })(),
    text: Promise.resolve('safe fixture'),
    finishReason: Promise.resolve('stop'),
  };
}

function opts(overrides: Record<string, any> = {}) {
  const { state, authority } = fixtureAuthority();
  let launched = 0;
  const parent = new AbortController();
  const args = {
    fixtureEnabled: true,
    permit: { ...basic },
    authority,
    expected,
    fixtureFactory: (signal: AbortSignal) => { launched++; return finite(signal); },
    parentSignal: parent.signal,
    renewEveryMs: 2000,
    ...overrides,
  };
  return { state, authority, args, parent, launched: () => launched };
}

async function denied(make: ReturnType<typeof opts>, reason: string) {
  await assert.rejects(startOfflineResearchRound(make.args), (err: any) =>
    err instanceof ResearchGateDenied && err.reason === reason);
  assert.equal(make.launched(), 0);
}

test('disabled or missing gate elements deny before factory', async () => {
  for (const override of [{ fixtureEnabled: false }, { permit: undefined },
    { authority: undefined }, { fixtureFactory: undefined }]) {
    await denied(opts(override), 'fixture_unavailable');
  }
});

test('wrong task/node/group/model/attempt/epoch/quota/expiration reject before launch', async () => {
  const fields: Array<keyof ResearchPermit> = [
    'taskId', 'nodeId', 'role', 'attemptId', 'modelId',
    'upstreamGroupId', 'authorityEpoch', 'reservedInput', 'reservedOutput',
  ];
  for (const field of fields) {
    const permit = { ...basic, [field]:
      typeof basic[field] === 'number' ? -1 : 'wrong-scope' };
    await denied(opts({ permit }), 'permit_mismatch');
  }
  await denied(opts({ permit: { ...basic, expiresAt: Date.now() - 1 } }),
    'permit_mismatch');
});

test('revoked, superseded and custodian-denied attempts do not create streams', async () => {
  const revoked = opts();
  revoked.state.denied = true;
  await denied(revoked, 'initial_renewal_denied');
  const superseded = opts();
  superseded.state.override = { ...basic, leaseId: 'different-lease' };
  await denied(superseded, 'initial_renewal_denied');
  const noWitness = opts();
  noWitness.state.witnessDenied = true;
  await denied(noWitness, 'fixture_custody_denied');
  assert.equal(noWitness.state.released, 1);
});

test('initial coordinator hang expires before factory is created', async () => {
  const make = opts();
  make.state.hang = true;
  await denied(make, 'initial_renewal_denied');
});

test('valid fake stream yields events, typed witness, and releases once', async () => {
  const make = opts();
  const round = await startOfflineResearchRound(make.args);
  const events = [];
  try {
    for await (const event of round.stream.fullStream) events.push(event.text);
    assert.deepEqual(events, ['safe', 'fixture']);
    assert.equal(await round.stream.text, 'safe fixture');
    assert.equal(await round.stream.finishReason, 'stop');
  } finally { await round.close(); await round.close(); }
  assert.equal(make.launched(), 1);
  assert.equal(make.state.witnessed, 1);
  assert.equal(make.state.released, 1);
  assert.ok(make.state.renewals >= 3);
});

test('renewal loss before second event prevents additional output', async () => {
  const make = opts();
  let fakeAborted = false;
  make.args.fixtureFactory = (signal: AbortSignal) => {
    signal.addEventListener('abort', () => { fakeAborted = true; });
    return finite(signal);
  };
  const round = await startOfflineResearchRound(make.args);
  const iter = round.stream.fullStream[Symbol.asyncIterator]();
  try {
    const first = await iter.next();
    assert.equal(first.value.text, 'safe');
    make.state.denied = true;
    await assert.rejects(iter.next(), /renewal_denied/);
    assert.equal(fakeAborted, true);
  } finally { await round.close(); }
});

test('parent abort stops a blocked uncooperative iterator promptly', async () => {
  const make = opts({ renewEveryMs: 2000 });
  let fakeAborted = false;
  make.args.fixtureFactory = (signal: AbortSignal) => {
    signal.addEventListener('abort', () => { fakeAborted = true; });
    return {
      fullStream: (async function* () {
        yield { type: 'text-delta', text: 'first' };
        await new Promise(() => {});
      })(),
      text: new Promise<string>(() => {}),
      finishReason: new Promise<string>(() => {}),
    };
  };
  const round = await startOfflineResearchRound(make.args);
  const iter = round.stream.fullStream[Symbol.asyncIterator]();
  try {
    assert.equal((await iter.next()).value.text, 'first');
    const pending = iter.next();
    make.parent.abort();
    await assert.rejects(pending, /parent_aborted/);
    assert.equal(fakeAborted, true);
  } finally { await round.close(); }
});

test('coordinator denial while stream stalls cancels blocked next', async () => {
  const make = opts({ renewEveryMs: 5 });
  let aborted = false;
  make.args.fixtureFactory = (signal: AbortSignal) => {
    signal.addEventListener('abort', () => { aborted = true; });
    return {
      fullStream: (async function* () {
        yield { type: 'text-delta', text: 'first' };
        await new Promise(() => {});
      })(),
      text: new Promise<string>(() => {}),
      finishReason: new Promise<string>(() => {}),
    };
  };
  const round = await startOfflineResearchRound(make.args);
  const iter = round.stream.fullStream[Symbol.asyncIterator]();
  try {
    await iter.next();
    const pending = iter.next();
    make.state.denied = true;
    await assert.rejects(Promise.race([
      pending, delay(100).then(() => { throw Error('fixture_cancel_timeout'); })
    ]), /renewal_denied/);
    assert.equal(aborted, true);
  } finally { await round.close(); }
});

test('external expiry during stalled async generation cancels regardless of hung renewal', async () => {
  let fakeNow = Date.now();
  const make = opts({ renewEveryMs: 5, clock: () => fakeNow });
  let aborted = false;
  make.args.fixtureFactory = (signal: AbortSignal) => {
    signal.addEventListener('abort', () => { aborted = true; });
    return {
      fullStream: (async function* () {
        yield { type: 'text-delta', text: 'first' };
        await new Promise(() => {});
      })(),
      text: new Promise<string>(() => {}),
      finishReason: new Promise<string>(() => {}),
    };
  };
  const round = await startOfflineResearchRound(make.args);
  const iter = round.stream.fullStream[Symbol.asyncIterator]();
  try {
    await iter.next();
    const pending = iter.next();
    make.state.hang = true;
    fakeNow = basic.expiresAt + 1;
    await assert.rejects(Promise.race([
      pending, delay(100).then(() => { throw Error('fixture_cancel_timeout'); })
    ]), /lease_expired/);
    assert.equal(aborted, true);
  } finally { await round.close(); }
});

test('source forbids SDK fallback at actual pinned bot-turn callsite', () => {
  const source = readFileSync(new URL('../src/bots/bot-turn.ts', import.meta.url), 'utf8');
  assert.equal(source.includes('streamText('), false);
  assert.equal((source.match(/startOfflineResearchRound\(/g) ?? []).length, 1);
  assert.match(source, /fixtureFactory: input\.researchFixtureStream/);
  assert.match(source, /expected: input\.researchExpected/);
  const guard = readFileSync(new URL('../src/bots/research-provider-gate.ts',
    import.meta.url), 'utf8');
  assert.equal(guard.includes("from 'ai'"), false);
});


test('invalid fixture or factory exception fails closed and releases mock lease', async () => {
  const broken = opts({ fixtureFactory: () => { throw Error('fixture_error'); } });
  await denied(broken, 'fixture_factory_failed');
  assert.equal(broken.state.released, 1);
  const malformed = opts({ fixtureFactory: () => ({
    fullStream: {} as AsyncIterable<any>,
    text: Promise.resolve(''),
    finishReason: Promise.resolve('stop'),
  }) });
  await denied(malformed, 'fixture_factory_failed');
  assert.equal(malformed.state.released, 1);
});

test('abort before admission stops all mock transport startup', async () => {
  const make = opts();
  make.parent.abort();
  await denied(make, 'parent_aborted');
  assert.equal(make.state.renewals, 0);
});
