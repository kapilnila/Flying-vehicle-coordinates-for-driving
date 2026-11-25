export type TrackPoint = {
  id: string;
  latitude: number;
  longitude: number;
  captured_at_ms: number;
  altitude_m: number;
  altitude_reference?: 'msl' | 'unknown';
  speed_mps: number;
};

export type TrackSegment = {from_id: string; to_id: string; connected: boolean};
export type Track = {points: TrackPoint[]; segments: TrackSegment[]};

export function PathPreview({data, live = false}: {data: Track; live?: boolean}) {
  const {points, segments} = data;
  if (!points.length) return <div className="empty compact">No points in this trajectory window.</div>;
  const lats = points.map(p => p.latitude);
  const lons = points.map(p => p.longitude);
  const minLat = Math.min(...lats), minLon = Math.min(...lons);
  const latRange = Math.max(...lats) - minLat, lonRange = Math.max(...lons) - minLon;
  const positions = new Map(points.map(p => [p.id, {
    x: lonRange ? 30 + (p.longitude - minLon) / lonRange * 540 : 300,
    y: latRange ? 250 - (p.latitude - minLat) / latRange * 220 : 140,
  }]));
  return <div className="path-preview">
    <svg viewBox="0 0 600 280" role="img" aria-label="Relative trajectory plot">
      {segments.filter(s => !live || s.connected).map(s => {
        const a = positions.get(s.from_id)!, b = positions.get(s.to_id)!;
        return <line key={s.from_id} x1={a.x} y1={a.y} x2={b.x} y2={b.y}
          className={s.connected ? 'path-connected' : 'path-broken'}/>;
      })}
      {points.map((p, i) => {
        const {x, y} = positions.get(p.id)!;
        const latest = live && i === points.length - 1;
        return <g key={p.id}>
          <circle cx={x} cy={y} r={latest ? 8 : 6}/>
          {(!live || latest) && <text x={x + 9} y={y - 9}>{latest ? 'Latest' : i + 1}</text>}
        </g>;
      })}
    </svg>
    <p className="muted">{live ? 'Relative coordinates · gaps mark rejected segments' :
      'Relative coordinate view · green = accepted segment · dashed red = discontinuity'}</p>
  </div>;
}
