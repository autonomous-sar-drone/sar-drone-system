import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import App from './App';

vi.mock('./features/telemetry/useTelemetry', () => ({
  useTelemetry: () => ({ reading: null, status: 'connecting', nowMs: 0 }),
}));

describe('dashboard layout', () => {
  it('renders every panel from the sketch', () => {
    render(<App />);
    for (const name of ['Search map', 'Mission status', 'Drone camera', 'Possible detections']) {
      expect(screen.getByRole('heading', { name })).toBeInTheDocument();
    }
    expect(screen.getByRole('status')).toHaveTextContent('Connecting to backend');
  });

  it('keeps mission controls disabled until a mission can exist', () => {
    render(<App />);
    const toolbar = screen.getByRole('toolbar', { name: 'Mission controls' });
    for (const button of toolbar.querySelectorAll('button')) {
      expect(button).toBeDisabled();
    }
  });
});
