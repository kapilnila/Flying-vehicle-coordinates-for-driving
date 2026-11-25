import {useCallback, useEffect, useRef, useState} from 'react';
import {GoogleTrail} from './GoogleTrail';
import {PathPreview, type Track} from './PathPreview';
import {TerrainReading} from './TerrainReading';

type LiveProps = {
  vehicle: string;
  rules: {max_gap_ms: number; max_speed_mps: number; max_distance_m: number};
  request: (path: string, options?: RequestInit) => Promise<Track>;
};

function CaptureAge({captured}: {captured: number}) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const seconds = Math.floor((now - captured) / 1000);
  const age = new Intl.RelativeTimeFormat(undefined, {numeric: 'always'});
  let elapsed = age.format(-seconds, 'second');
  if (Math.abs(seconds) >= 3600) elapsed = age.format(-Math.trunc(seconds / 3600), 'hour');
  else if (Math.abs(seconds) >= 60) elapsed = age.format(-Math.trunc(seconds / 60), 'minute');
  return <>{new Date(captured).toLocaleString()} · {elapsed}</>;
}

function Following({vehicle, rules, request}: LiveProps) {
  const [track, setTrack] = useState<Track>();
  const [feedError, setFeedError] = useState('');
  const [mapError, setMapError] = useState('');
  const [mapReady, setMapReady] = useState(false);
  const [terrain, setTerrain] = useState(false);
  const nextLookup = useRef(0);
  const ready = useCallback(() => setMapReady(true), []);
  const query = new URLSearchParams({
    window: 'latest', limit: '100', max_gap_ms: String(rules.max_gap_ms),
    max_speed_mps: String(rules.max_speed_mps), max_distance_m: String(rules.max_distance_m),
  }).toString();

  useEffect(() => {
    const controller = new AbortController();
    let inFlight = false;
    async function refresh() {
      if (inFlight) return;
      inFlight = true;
      try {
        const result = await request(`/v1/vehicles/${encodeURIComponent(vehicle)}/trajectory?${query}`, {signal: controller.signal});
        if (controller.signal.aborted) return;
        setTrack(result);
        setFeedError('');
      } catch (error) {
        if (!controller.signal.aborted) setFeedError(error instanceof Error ? error.message : 'Vehicle feed unavailable');
      } finally {
        inFlight = false;
      }
    }
    void refresh();
    const timer = window.setInterval(refresh, 5000);
    return () => { window.clearInterval(timer); controller.abort(); };
  }, [vehicle, request, query]);

  const point = track?.points.at(-1);
  return <>
    {feedError && <p role="alert">Could not refresh the vehicle position. {feedError}</p>}
    {point && track ? <>
      {mapError ? <><p role="status">{mapError} Showing the coordinate plot.</p><PathPreview data={track} live/></> :
        <GoogleTrail track={track} onReady={ready} onFailure={setMapError}/>}
      <dl className="live-metrics">
        <div><dt>Latest position</dt><dd>{point.latitude}, {point.longitude}</dd></div>
        <div><dt>Speed</dt><dd>{point.speed_mps} m/s</dd></div>
        <div><dt>Captured</dt><dd><CaptureAge captured={point.captured_at_ms}/></dd></div>
        <div><dt>{point.altitude_reference === 'msl' ? 'Vehicle altitude above sea level' : 'Reported altitude'}</dt>
          <dd>{point.altitude_m} m{point.altitude_reference !== 'msl' && <small> · reference unknown</small>}</dd></div>
      </dl>
      {mapReady && !mapError && <>
        <label className="terrain-toggle"><input type="checkbox" checked={terrain} onChange={e => setTerrain(e.target.checked)}/> Show terrain elevation</label>
        {terrain && <TerrainReading point={point} nextLookup={nextLookup}/>}
      </>}
    </> : <div className="empty compact">{track ? 'Waiting for the first vehicle record.' : feedError ? 'Waiting for the vehicle feed.' : 'Checking for vehicle records…'}</div>}
  </>;
}

export function LiveMap({vehicle, rules, request}: LiveProps) {
  const [draft, setDraft] = useState(vehicle);
  const [followed, setFollowed] = useState(vehicle);
  return <section className="panel live-panel">
    <div className="panel-title"><h2>Live map</h2><span className="muted">Latest 100 records · refreshes every 5 s</span></div>
    <form className="follow-form" onSubmit={e => {e.preventDefault(); setFollowed(draft.trim());}}>
      <label>Vehicle ID<input aria-label="Live vehicle" value={draft} onChange={e => setDraft(e.target.value)} required/></label>
      <button disabled={!draft.trim()}>Follow vehicle</button>
    </form>
    {followed && <><p className="muted">Following {followed}</p>
      <Following key={followed} vehicle={followed} rules={rules} request={request}/></>}
  </section>;
}
