import {useState} from 'react';

type Attempt = {
  request_id: string; started_at_ms: number; method: string; route: string;
  status: number; duration_ms: number; vehicle_id: string | null;
  point_count: number; replayed: boolean;
};
type Page = {items: Attempt[]; next_cursor: string | null};
type Point = {id: string; captured_at_ms: number; latitude: number; longitude: number; altitude_m: number};
const endpoint = '/v1/operator/request-logs';
const initialFilters = {vehicle_id: '', status: '', request_id: '', from_ms: '', to_ms: ''};
const dateLabel = (ms: number) => new Date(ms).toISOString();

export function RequestLog() {
  const [token, setToken] = useState('');
  const [filters, setFilters] = useState(initialFilters);
  const [page, setPage] = useState<Page | null>(null);
  const [detail, setDetail] = useState<{id: string; points: Point[]} | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  function clear() {
    setPage(null);
    setDetail(null);
    setError('');
  }
  async function read<T>(url: string): Promise<T> {
    const response = await fetch(url, {
      headers: {Authorization: `Bearer ${token}`}, redirect: 'error',
      cache: 'no-store', signal: AbortSignal.timeout(12000),
    });
    if (response.status === 401) throw new Error('Enter a valid operator token.');
    if (response.status === 403) throw new Error('Configure a distinct OPERATOR_TOKEN on the server to enable access.');
    if (!response.ok) throw new Error(`Request failed (HTTP ${response.status}).`);
    return response.json();
  }
  async function load(action: () => Promise<void>) {
    setBusy(true);
    setDetail(null);
    setError('');
    try { await action(); }
    catch (err) { setError(err instanceof Error ? err.message : 'Unable to load the request log.'); }
    finally { setBusy(false); }
  }
  function search(cursor?: string) {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) {
      if (value) query.set(key, key.endsWith('_ms') ? String(new Date(value).getTime()) : value);
    }
    if (cursor) query.set('cursor', cursor);
    void load(async () => setPage(await read<Page>(endpoint + '?' + query)));
  }
  function showPoints(id: string) {
    void load(async () => {
      const result = await read<{items: Point[]}>(`${endpoint}/${id}/points`);
      setDetail({id, points: result.items});
    });
  }
  return <section className="panel">
    <h2>Request log</h2>
    <p>Search the last 90 days of vehicle API activity. Times in the results are UTC.</p>
    <form onSubmit={event => {event.preventDefault(); search();}}>
      <div className="fields">
        <label>Operator token<input required type="password" autoComplete="off" value={token} disabled={busy}
          onChange={event => {clear(); setToken(event.target.value);}} /></label>
        {([
          ['vehicle_id', 'Vehicle ID', 'text'], ['status', 'HTTP status', 'number'],
          ['request_id', 'Request ID', 'text'], ['from_ms', 'From (local time)', 'datetime-local'],
          ['to_ms', 'To (local time)', 'datetime-local'],
        ] as const).map(([key, label, type]) => <label key={key}>{label}
          <input type={type} value={filters[key]} disabled={busy} min={key === 'status' ? 100 : undefined}
            max={key === 'status' ? 599 : undefined} onChange={event => {
              clear(); setFilters({...filters, [key]: event.target.value});
            }}/>
        </label>)}
      </div>
      <div className="actions"><button className="primary" disabled={busy || !token}>Search requests</button></div>
    </form>
    {error && <p className="message error" role="alert">{error}</p>}
    {page && (page.items.length === 0 ? <p>No matching requests.</p> : <>
      <div className="table-scroll"><table><thead><tr>
        <th>Received (UTC)</th><th>Request ID</th><th>Endpoint</th><th>Status</th>
        <th>Duration</th><th>Vehicle</th><th>Saved points</th>
      </tr></thead><tbody>{page.items.map(row => <tr key={row.request_id}>
        <td>{dateLabel(row.started_at_ms)}</td><td><code>{row.request_id}</code></td>
        <td>{row.method} {row.route}</td><td>{row.status}</td><td>{row.duration_ms} ms</td>
        <td>{row.vehicle_id ?? '—'}</td><td>{row.point_count > 0
          ? <button className="text-button" disabled={busy} onClick={() => showPoints(row.request_id)}>
            {row.replayed && 'Replayed · '}{row.point_count} {row.point_count === 1 ? 'point' : 'points'}
          </button> : '—'}</td>
      </tr>)}</tbody></table></div>
      {page.next_cursor && <button disabled={busy} onClick={() => search(page.next_cursor!)}>Next page</button>}
    </>)}
    {detail && <><h3>Saved points</h3><p>Request <code>{detail.id}</code></p>
      <div className="table-scroll"><table><thead><tr><th>Point ID</th><th>Captured (UTC)</th>
        <th>Coordinates</th><th>Reported altitude</th></tr></thead><tbody>
        {detail.points.map(point => <tr key={point.id}><td>{point.id}</td><td>{dateLabel(point.captured_at_ms)}</td>
          <td>{point.latitude}, {point.longitude}</td><td>{point.altitude_m} m</td></tr>)}
      </tbody></table></div></>}
  </section>;
}
