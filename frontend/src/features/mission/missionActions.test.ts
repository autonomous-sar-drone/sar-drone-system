import { describe, expect, it } from 'vitest';
import { activityLabel, missionActions } from './missionActions';
import type {
  FlightActivity,
  HoldReason,
  MissionState,
  MissionUpdateData,
} from '../../types/mission';

function mission(
  missionState: MissionState,
  flightActivity: FlightActivity | null = null,
  extra: Partial<MissionUpdateData> = {},
): MissionUpdateData {
  return {
    executionId: 'exec-1',
    planId: 'plan-1',
    missionState,
    activeTarget: null,
    interruptionReason: null,
    runtimeError: null,
    timestamp: '2026-11-17T15:00:00.000Z',
    flightActivity,
    holdReason: null,
    activeRevisitCandidateId: null,
    manualControlReady: false,
    ...extra,
  };
}

const hold = (reason: HoldReason) => mission('PAUSED', 'HOLDING', { holdReason: reason });

describe('activityLabel', () => {
  it('names each activity the way the operator sees it', () => {
    expect(activityLabel('RUNNING', 'SEARCHING', null, null)).toBe('Searching');
    expect(activityLabel('PAUSED', 'MANUAL_CONTROL', null, null)).toBe('Manual control active');
    expect(activityLabel('PAUSED', 'HOLDING', null, null)).toBe('Holding position');
    expect(activityLabel('PAUSED', 'HOLDING', 'OPERATOR_PAUSE', null)).toBe('Holding position');
    expect(activityLabel('PAUSED', 'PREPARING_REJOIN', null, null)).toBe('Preparing to rejoin search');
    expect(activityLabel('PAUSED', 'REJOINING', null, null)).toBe('Rejoining search route');
  });

  it('distinguishes a viewpoint hold from an ordinary one', () => {
    // Same activity, different situations: one can rejoin the route, the other is
    // where a revisit ended.
    expect(activityLabel('PAUSED', 'HOLDING', 'REVISIT_ARRIVAL', null)).toBe('Holding at viewpoint');
  });

  it('shows the candidate number while revisiting', () => {
    expect(activityLabel('PAUSED', 'REVISITING', null, 7)).toBe('Revisiting detection #7');
  });

  it('stays generic rather than printing an opaque id when the number is unknown', () => {
    expect(activityLabel('PAUSED', 'REVISITING', null, null)).toBe('Revisiting a detection');
  });

  it('defers to the existing lifecycle copy where the table says to', () => {
    expect(activityLabel('RETURNING_HOME', null, null, null)).toBeNull();
    expect(activityLabel('PREFLIGHT_CHECK', null, null, null)).toBeNull();
    expect(activityLabel('INTERRUPTED', null, null, null)).toBeNull();
    expect(activityLabel('COMPLETED', null, null, null)).toBeNull();
  });
});

describe('availability table', () => {
  it('RUNNING + SEARCHING offers take, pause, stop and revisit', () => {
    const a = missionActions(mission('RUNNING', 'SEARCHING'));
    expect(a.label).toBe('Searching');
    expect(a.takeControls.available).toBe(true);
    expect(a.pause.available).toBe(true);
    expect(a.stop.available).toBe(true);
    expect(a.revisit.available).toBe(true);
    expect(a.giveControls.available).toBe(false);
    expect(a.cancelRevisit.available).toBe(false);
    expect(a.manualActive).toBe(false);
  });

  it('PAUSED + MANUAL_CONTROL offers give and stop only', () => {
    const a = missionActions(mission('PAUSED', 'MANUAL_CONTROL'));
    expect(a.label).toBe('Manual control active');
    expect(a.giveControls.available).toBe(true);
    expect(a.stop.available).toBe(true);
    expect(a.manualActive).toBe(true);
    expect(a.takeControls.available).toBe(false);
    expect(a.resumeSearch.available).toBe(false);
    expect(a.revisit.available).toBe(false);
  });

  it('will not resume the search straight out of manual control', () => {
    // Rejoining from a hand-flown position is a backend manoeuvre that starts from a
    // confirmed hold, so control comes back first.
    const a = missionActions(mission('PAUSED', 'MANUAL_CONTROL'));
    expect(a.resumeSearch.reason).toBe('Give control back before resuming the search.');
  });

  it('PAUSED + HOLDING offers resume, take, stop and revisit', () => {
    const a = missionActions(hold('OPERATOR_PAUSE'));
    expect(a.label).toBe('Holding position');
    expect(a.resumeSearch.available).toBe(true);
    expect(a.takeControls.available).toBe(true);
    expect(a.stop.available).toBe(true);
    expect(a.revisit.available).toBe(true);
  });

  it('PAUSED + HOLDING at a viewpoint offers resume, take and stop but not another revisit', () => {
    const a = missionActions(hold('REVISIT_ARRIVAL'));
    expect(a.label).toBe('Holding at viewpoint');
    expect(a.resumeSearch.available).toBe(true);
    expect(a.takeControls.available).toBe(true);
    expect(a.stop.available).toBe(true);
    expect(a.revisit.available).toBe(false);
    expect(a.revisit.reason).toBe('Already at a viewpoint.');
  });

  it('PAUSED + PREPARING_REJOIN allows a takeover only when the backend says so', () => {
    const blocked = missionActions(mission('PAUSED', 'PREPARING_REJOIN'));
    expect(blocked.takeControls.available).toBe(false);
    expect(blocked.takeControls.reason).toBe('The aircraft is not ready to hand over control.');
    expect(blocked.stop.available).toBe(true);

    const allowed = missionActions(
      mission('PAUSED', 'PREPARING_REJOIN', { manualControlReady: true }),
    );
    expect(allowed.takeControls.available).toBe(true);
  });

  it('PAUSED + REJOINING offers stop and take', () => {
    const a = missionActions(mission('PAUSED', 'REJOINING'));
    expect(a.label).toBe('Rejoining search route');
    expect(a.stop.available).toBe(true);
    expect(a.takeControls.available).toBe(true);
    expect(a.resumeSearch.available).toBe(false);
  });

  it('PAUSED + REVISITING offers cancel, stop and take', () => {
    const a = missionActions(
      mission('PAUSED', 'REVISITING', { activeRevisitCandidateId: 'cand-4' }),
      4,
    );
    expect(a.label).toBe('Revisiting detection #4');
    expect(a.cancelRevisit.available).toBe(true);
    expect(a.stop.available).toBe(true);
    expect(a.takeControls.available).toBe(true);
    expect(a.resumeSearch.available).toBe(false);
  });

  it('offers no manual keyboard control while taking off or returning home', () => {
    for (const state of ['PREFLIGHT_CHECK', 'RETURNING_HOME'] as const) {
      const a = missionActions(mission(state, null));
      expect(a.takeControls.available).toBe(false);
      expect(a.giveControls.available).toBe(false);
      expect(a.manualActive).toBe(false);
      expect(a.revisit.available).toBe(false);
    }
  });

  it('offers no takeover while returning home even when an activity is reported', () => {
    // The Phase 1 case above short-circuits before the activity rules, so this is
    // the one that actually pins the lifecycle guard.
    const a = missionActions(mission('RETURNING_HOME', 'SEARCHING'));
    expect(a.takeControls.available).toBe(false);
    expect(a.revisit.available).toBe(false);
    expect(a.pause.available).toBe(false);

    const b = missionActions(mission('PREFLIGHT_CHECK', 'SEARCHING'));
    expect(b.takeControls.available).toBe(false);
    expect(b.revisit.available).toBe(false);
  });

  it('keeps stop to the states the backend accepts it in', () => {
    expect(missionActions(mission('RUNNING', 'SEARCHING')).stop.available).toBe(true);
    expect(missionActions(hold('OPERATOR_PAUSE')).stop.available).toBe(true);
    // The aircraft has already landed in INTERRUPTED, and Stop would 409.
    expect(missionActions(mission('INTERRUPTED')).stop.available).toBe(false);
    expect(missionActions(mission('COMPLETED')).stop.available).toBe(false);
    expect(missionActions(mission('RETURNING_HOME')).stop.available).toBe(false);
  });

  it('gives a reason on every action it disables', () => {
    const a = missionActions(mission('RETURNING_HOME'));
    for (const action of [a.pause, a.resumeSearch, a.stop, a.takeControls, a.giveControls, a.cancelRevisit, a.revisit]) {
      expect(action.available).toBe(false);
      expect(action.reason).toBeTruthy();
    }
  });
});

describe('a backend without the Phase 2 fields', () => {
  it('keeps the existing pause, resume and stop behaviour', () => {
    expect(missionActions(mission('RUNNING')).pause.available).toBe(true);
    expect(missionActions(mission('PAUSED')).resumeSearch.available).toBe(true);
    expect(missionActions(mission('RUNNING')).stop.available).toBe(true);
    expect(missionActions(mission('PAUSED')).stop.available).toBe(true);
  });

  it('offers none of the Phase 2 actions, and says why', () => {
    const a = missionActions(mission('RUNNING'));
    expect(a.takeControls.available).toBe(false);
    expect(a.takeControls.reason).toBe('Needs the updated backend.');
    expect(a.revisit.available).toBe(false);
    expect(a.revisit.reason).toBe('Needs the updated backend.');
    expect(a.manualActive).toBe(false);
  });

  it('falls back to the lifecycle label', () => {
    expect(missionActions(mission('RUNNING')).label).toBe('Searching');
    expect(missionActions(mission('PAUSED')).label).toBe('Paused');
  });

  it('handles no mission at all', () => {
    const a = missionActions(null);
    expect(a.label).toBe('Not started');
    expect(a.stop.available).toBe(false);
    expect(a.takeControls.available).toBe(false);
  });
});
