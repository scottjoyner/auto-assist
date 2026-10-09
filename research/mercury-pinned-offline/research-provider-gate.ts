/**
 * OFFLINE RESEARCH SHIM ONLY, pinned to Mercury upstream 1be98293.
 * There is deliberately no provider import, network client or SDK fallback.
 * No permit+renewal+witness+explicit fixture factory -> no stream.
 */
export interface ResearchPermit {
  leaseId: string;
  taskId: string;
  nodeId: string;
  role: string;
  attemptId: string;
  modelId: string;
  upstreamGroupId: string;
  authorityEpoch: number;
  reservedInput: number;
  reservedOutput: number;
  expiresAt: number; // milliseconds since epoch; fixture clock
}

export interface ResearchAuthority {
  renew(permit: ResearchPermit): Promise<ResearchPermit | null>;
  acknowledge(event: {
    kind: 'research_fixture_start';
    taskId: string;
    leaseId: string;
    nodeId: string;
    upstreamGroupId: string;
  }): Promise<boolean>;
  release(permit: ResearchPermit): Promise<void>;
}

export interface ResearchStream<T = { type: string; text?: string }> {
  fullStream: AsyncIterable<T>;
  text: Promise<string>;
  finishReason: Promise<string>;
}

export type ResearchExpected = Pick<ResearchPermit,
  'taskId' | 'nodeId' | 'role' | 'attemptId' | 'modelId' |
  'upstreamGroupId' | 'authorityEpoch' | 'reservedInput' | 'reservedOutput'>;

export class ResearchGateDenied extends Error {
  readonly reasonCode = 'research_admission_denied';
  readonly reason: string;
  constructor(reason: string) {
    super(reason);
    this.reason = reason;
    this.name = 'ResearchGateDenied';
  }
}

type StartOptions<T> = {
  fixtureEnabled: boolean;
  permit?: ResearchPermit;
  authority?: ResearchAuthority;
  expected: ResearchExpected;
  fixtureFactory?: (signal: AbortSignal) => ResearchStream<T>;
  parentSignal: AbortSignal;
  clock?: () => number;
  renewEveryMs?: number;
};

function positiveInt(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value > 0;
}

function matches(permit: ResearchPermit | null | undefined,
                 expected: ResearchExpected, initialLease?: string,
                 clock: () => number = Date.now): permit is ResearchPermit {
  if (!permit || !permit.leaseId || typeof permit.leaseId !== 'string' ||
      (initialLease !== undefined && permit.leaseId !== initialLease) ||
      !positiveInt(permit.authorityEpoch) ||
      !positiveInt(permit.reservedInput) || !positiveInt(permit.reservedOutput) ||
      !Number.isFinite(permit.expiresAt) || permit.expiresAt <= clock()) {
    return false;
  }
  for (const key of Object.keys(expected) as (keyof ResearchExpected)[]) {
    if (permit[key] !== expected[key]) return false;
  }
  return Boolean(expected.taskId && expected.nodeId && expected.role &&
    expected.attemptId && expected.modelId && expected.upstreamGroupId);
}

/**
 * Wrap one *fake* stream. Token authorization is not claimed here:
 * permit/authority are injected mocks, and the fixture is a caller-provided
 * no-network async iterator. The future real transport must be independently
 * reviewed, authenticated, and enforce this check at its true call site.
 */
export async function startOfflineResearchRound<T>(opts: StartOptions<T>): Promise<{
  stream: ResearchStream<T>;
  close: () => Promise<void>;
}> {
  const now = opts.clock ?? Date.now;
  const { permit, authority } = opts;
  if (!opts.fixtureEnabled || !permit || !authority || !opts.fixtureFactory) {
    throw new ResearchGateDenied('fixture_unavailable');
  }
  if (opts.renewEveryMs !== undefined && !positiveInt(opts.renewEveryMs)) {
    throw new ResearchGateDenied('invalid_renew_interval');
  }
  if (opts.parentSignal.aborted) throw new ResearchGateDenied('parent_aborted');
  if (!matches(permit, opts.expected, undefined, now)) {
    throw new ResearchGateDenied('permit_mismatch');
  }

  // The lease, witness, and post-witness lease checks must ALL complete
  // within a bounded window. The earlier mock awaited witness forever and
  // could launch a stream using a lease already revoked during that wait.
  // A late promise completion never grants authority after a timeout/abort.
  const bounded = async <T>(fn: () => Promise<T>, ignoreAbort = false): Promise<T | null> => {
    if (!ignoreAbort && opts.parentSignal.aborted) return null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let onAbort: () => void = () => {};
    const abort = new Promise<null>(resolve => {
      onAbort = () => resolve(null);
      if (!ignoreAbort) {
        opts.parentSignal.addEventListener('abort', onAbort, { once: true });
        if (opts.parentSignal.aborted) resolve(null);
      }
    });
    const timeout = new Promise<null>(resolve => {
      timer = setTimeout(() => resolve(null), 250);
    });
    try {
      return await Promise.race([
        Promise.resolve().then(fn).catch(() => null),
        timeout, abort,
      ]);
    } finally {
      if (timer) clearTimeout(timer);
      opts.parentSignal.removeEventListener('abort', onAbort);
    }
  };
  const first = await bounded(() => authority.renew(permit));
  if (opts.parentSignal.aborted) throw new ResearchGateDenied('parent_aborted');
  if (!matches(first, opts.expected, permit.leaseId, now)) {
    throw new ResearchGateDenied('initial_renewal_denied');
  }
  const acknowledged = await bounded(() => authority.acknowledge({
    kind: 'research_fixture_start',
    taskId: permit.taskId, leaseId: permit.leaseId,
    nodeId: permit.nodeId, upstreamGroupId: permit.upstreamGroupId,
  }));
  if (!acknowledged || opts.parentSignal.aborted ||
      !matches(first, opts.expected, permit.leaseId, now)) {
    await bounded(() => authority.release(first), true);
    throw new ResearchGateDenied(opts.parentSignal.aborted ?
      'parent_aborted' : 'fixture_custody_denied');
  }
  // Witness acceptance is not a reservation extension. Revalidate with the
  // coordinator after witnessing, to reject revocation/epoch or expiry
  // occurring while the witness call was pending.
  const afterWitness = await bounded(() => authority.renew(first));
  if (opts.parentSignal.aborted ||
      !matches(afterWitness, opts.expected, permit.leaseId, now)) {
    await bounded(() => authority.release(first), true);
    throw new ResearchGateDenied(opts.parentSignal.aborted ?
      'parent_aborted' : 'prelaunch_renewal_denied');
  }

  let current = afterWitness;
  let stopped = false;
  let reason = 'lease_lost';
  const controller = new AbortController();
  let rejectCancellation!: (reason: unknown) => void;
  const cancellation = new Promise<never>((_, reject) => {
    rejectCancellation = reject;
  });
  // Prevent an unhandled rejection if the parent aborts before first next().
  void cancellation.catch(() => {});
  const cancel = (cause: string): void => {
    if (stopped) return;
    stopped = true;
    reason = cause;
    controller.abort(new ResearchGateDenied(cause));
    rejectCancellation(new ResearchGateDenied(cause));
  };
  const parentAbort = (): void => cancel('parent_aborted');
  opts.parentSignal.addEventListener('abort', parentAbort, { once: true });
  if (opts.parentSignal.aborted) cancel('parent_aborted');

  let updating: Promise<void> = Promise.resolve();
  const renew = async (): Promise<void> => {
    if (stopped) throw new ResearchGateDenied(reason);
    // Serializes timer renewals with pre-event renewals.
    const previous = updating;
    let complete!: () => void;
    updating = new Promise<void>(resolve => { complete = resolve; });
    try {
      await previous;
      if (stopped) throw new ResearchGateDenied(reason);
      const candidate = await authority.renew(current).catch(() => null);
      if (!matches(candidate, opts.expected, permit.leaseId, now)) {
        cancel('renewal_denied');
        throw new ResearchGateDenied('renewal_denied');
      }
      if (stopped) throw new ResearchGateDenied(reason);
      current = candidate;
    } finally {
      complete();
    }
  };

  const interval = setInterval(() => {
    // Local expiry check remains active even if a coordinator call hangs.
    if (current.expiresAt <= now()) {
      cancel('lease_expired');
      return;
    }
    void renew().catch(() => { cancel('renewal_denied'); });
  }, opts.renewEveryMs ?? 20);
  interval.unref?.();

  let source: ResearchStream<T>;
  try {
    if (stopped) throw new ResearchGateDenied(reason);
    // This is the only launch site. No 'ai' provider transport is referenced.
    source = opts.fixtureFactory(controller.signal);
    if (!source?.fullStream ||
        typeof source.fullStream[Symbol.asyncIterator] !== 'function' ||
        !source.text || !source.finishReason) {
      throw new ResearchGateDenied('invalid_fixture_stream');
    }
  } catch {
    clearInterval(interval);
    opts.parentSignal.removeEventListener('abort', parentAbort);
    cancel('fixture_factory_failed');
    await authority.release(current).catch(() => {});
    throw new ResearchGateDenied('fixture_factory_failed');
  }

  const wrap = async function* (): AsyncGenerator<T> {
    const iterator = source.fullStream[Symbol.asyncIterator]();
    try {
      while (true) {
        await Promise.race([renew(), cancellation]);
        const next = await Promise.race([iterator.next(), cancellation]);
        if (stopped) throw new ResearchGateDenied(reason);
        if (next.done) return;
        yield next.value;
      }
    } finally {
      if (stopped) {
        // Signal the fake SDK but do not await an uncooperative iterator.
        void iterator.return?.().catch(() => {});
      }
    }
  };
  let closed = false;
  return {
    stream: {
      fullStream: wrap(),
      get text() { return Promise.race([source.text, cancellation]); },
      get finishReason() { return Promise.race([source.finishReason, cancellation]); },
    },
    async close() {
      if (closed) return;
      closed = true;
      clearInterval(interval);
      opts.parentSignal.removeEventListener('abort', parentAbort);
      // Close the renewal path before releasing the reserved mock lease.
      cancel('round_closed');
      await authority.release(current).catch(() => {});
    },
  };
}