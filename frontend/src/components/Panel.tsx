import type { ReactNode } from 'react';
import './panel.css';

interface Props {
  title: string;
  /** Grid area name from dashboard.css. */
  area: string;
  actions?: ReactNode;
  children: ReactNode;
}

export function Panel({ title, area, actions, children }: Props) {
  const id = `panel-${area}`;
  return (
    <section className="panel" style={{ gridArea: area }} aria-labelledby={id}>
      <header className="panel__header">
        <h2 id={id} className="panel__title">
          {title}
        </h2>
        {actions}
      </header>
      <div className="panel__body">{children}</div>
    </section>
  );
}

/** Honest empty state for panels whose feature hasn't been built yet. */
export function EmptyState({ children }: { children: ReactNode }) {
  return <p className="empty-state">{children}</p>;
}
