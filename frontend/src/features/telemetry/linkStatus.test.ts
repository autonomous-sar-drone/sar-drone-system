import { describe, expect, it } from 'vitest';
import { deriveLinkStatus } from './linkStatus';

const STALE = 3000;

describe('deriveLinkStatus', () => {
  it('is connecting before any socket or data', () => {
    expect(deriveLinkStatus('connecting', null, 0, STALE)).toBe('connecting');
  });

  it('is waiting when connected but no telemetry has arrived', () => {
    expect(deriveLinkStatus('open', null, 0, STALE)).toBe('waiting');
  });

  it('is live while telemetry is fresh', () => {
    expect(deriveLinkStatus('open', 10_000, 12_000, STALE)).toBe('live');
  });

  it('is stale when the socket is open but telemetry went quiet', () => {
    expect(deriveLinkStatus('open', 10_000, 13_001, STALE)).toBe('stale');
  });

  it('is offline when the socket closes, even with recent data', () => {
    expect(deriveLinkStatus('closed', 10_000, 10_100, STALE)).toBe('offline');
  });

  it('stays offline (not connecting) while reconnecting after having had data', () => {
    expect(deriveLinkStatus('connecting', 10_000, 20_000, STALE)).toBe('offline');
  });
});
