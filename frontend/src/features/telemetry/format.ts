export function formatLatitude(deg: number): string {
  return `${Math.abs(deg).toFixed(6)}° ${deg >= 0 ? 'N' : 'S'}`;
}

export function formatLongitude(deg: number): string {
  return `${Math.abs(deg).toFixed(6)}° ${deg >= 0 ? 'E' : 'W'}`;
}

const COMPASS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'] as const;

export function formatHeading(deg: number | null): { value: string; detail: string } {
  if (deg === null) return { value: 'Unknown', detail: 'Not reported by the aircraft' };
  const normalized = ((deg % 360) + 360) % 360;
  const point = COMPASS[Math.round(normalized / 45) % 8];
  return { value: `${Math.round(normalized)}°`, detail: point };
}

export function formatAge(ms: number): string {
  if (ms < 1000) return 'just now';
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  return `${m}m ${s % 60}s ago`;
}
