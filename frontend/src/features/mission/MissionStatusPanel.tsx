import { Panel } from '../../components/Panel';

const ROWS: Array<[string, string]> = [
  ['Mission', 'Not started'],
  ['Search area', 'Not defined'],
  ['Route', 'Not generated'],
  ['Coverage', 'No data'],
];

export function MissionStatusPanel() {
  return (
    <Panel title="Mission status" area="mission">
      <dl className="status-list">
        {ROWS.map(([label, value]) => (
          <div key={label} className="status-list__row">
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </Panel>
  );
}
