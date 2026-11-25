import {useEffect, useRef, useState} from 'react';
import {importLibrary} from '@googlemaps/js-api-loader';
import type {TrackPoint} from './PathPreview';

type TerrainSample = {latitude: number; longitude: number; sampledAt: number; elevation: number};

async function readElevation(point: TrackPoint, signal: AbortSignal) {
  const {ElevationService} = await importLibrary('elevation');
  signal.throwIfAborted();
  const {results} = await new ElevationService().getElevationForLocations({
    locations: [{lat: point.latitude, lng: point.longitude}],
  });
  const elevation = results[0]?.elevation;
  if (!Number.isFinite(elevation)) throw new Error('No terrain elevation at this position');
  return elevation;
}

export function TerrainReading({point, nextLookup}: {
  point: TrackPoint;
  nextLookup: {current: number};
}) {
  const position = useRef(point);
  const [sample, setSample] = useState<TerrainSample>();
  const [failed, setFailed] = useState(false);
  useEffect(() => { position.current = point; }, [point]);

  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    let previous: TerrainSample | undefined;
    let pending = false;
    async function lookup() {
      const current = position.current;
      if (pending || performance.now() < nextLookup.current) return;
      if (previous?.latitude === current.latitude && previous.longitude === current.longitude) return;
      nextLookup.current = performance.now() + 30000;
      pending = true;
      let timeout: number | undefined;
      try {
        const elevation = await Promise.race([
          readElevation(current, AbortSignal.any([controller.signal, AbortSignal.timeout(12000)])),
          new Promise<never>((_, reject) => { timeout = window.setTimeout(reject, 12000); }),
        ]);
        if (!active) return;
        previous = {latitude: current.latitude, longitude: current.longitude, elevation, sampledAt: Date.now()};
        setSample(previous);
        setFailed(false);
      } catch {
        if (!active) return;
        previous = undefined;
        setSample(undefined);
        setFailed(true);
      } finally {
        window.clearTimeout(timeout);
        pending = false;
      }
    }
    void lookup();
    const interval = window.setInterval(lookup, 1000);
    return () => { active = false; controller.abort(); window.clearInterval(interval); };
  }, [nextLookup]);

  const matched = sample?.latitude === point.latitude && sample.longitude === point.longitude;
  const height = point.altitude_reference === 'msl' && matched
    ? `≈ ${(point.altitude_m - sample.elevation).toFixed(1)} m` : 'Unavailable';
  return <div className="terrain-reading">
    <dl className="live-metrics">
      <div><dt>Terrain elevation above sea level</dt><dd>{sample ? `${sample.elevation.toFixed(1)} m` : failed ? 'Unavailable' : 'Waiting for terrain'}</dd></div>
      <div><dt>Estimated height above terrain</dt><dd>{height}</dd></div>
    </dl>
    {sample && <p>Google Maps terrain sampled at {sample.latitude}, {sample.longitude} · {new Date(sample.sampledAt).toLocaleString()}</p>}
    {sample && !matched && <p>Waiting for terrain at the latest position.</p>}
    {failed && <p>Terrain lookup failed. Tracking continues and the lookup will retry.</p>}
    {point.altitude_reference !== 'msl' && <p>Estimating height requires an altitude reported above mean sea level.</p>}
  </div>;
}
