import {useEffect, useRef, useState} from 'react';
import {importLibrary, setOptions} from '@googlemaps/js-api-loader';
import type {Track} from './PathPreview';

let loaderConfigured = false;
let authorizationFailed = false;

export function GoogleTrail({track, onReady, onFailure}: {
  track: Track;
  onReady: () => void;
  onFailure: (message: string) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const [map, setMap] = useState<google.maps.Map>();
  const latest = track.points[track.points.length - 1];
  const initialPosition = useRef({lat: latest.latitude, lng: latest.longitude});

  useEffect(() => {
    const key = import.meta.env.VITE_GOOGLE_MAPS_API_KEY;
    if (!key && !window.google?.maps?.importLibrary) {
      onFailure('Google Maps is optional. Add a browser key to enable the basemap.');
      return;
    }
    let active = true;
    let tileListener: google.maps.MapsEventListener | undefined;
    const authWindow = window as Window & {gm_authFailure?: () => void};
    const previousAuthHandler = authWindow.gm_authFailure;
    authWindow.gm_authFailure = () => {
      authorizationFailed = true;
      onFailure('Google Maps rejected the browser key. Check its website and API restrictions.');
      previousAuthHandler?.();
    };
    const timeout = window.setTimeout(() => onFailure('Google Maps took too long to load.'), 12000);

    async function initialize() {
      if (authorizationFailed) throw new Error('Google Maps authorization failed');
      if (!loaderConfigured) {
        setOptions({key, v: 'weekly'});
        loaderConfigured = true;
      }
      const library = await importLibrary('maps');
      if (!active || !host.current) return;
      const instance = new library.Map(host.current, {
        center: initialPosition.current, zoom: 15, streetViewControl: false,
        mapTypeControl: false, tilt: 0,
      });
      instance.data.setStyle(feature => {
        const role = feature.getProperty('role');
        if (role === 'trail') return {strokeColor: '#238460', strokeWeight: 4, clickable: false};
        const current = role === 'latest';
        return {clickable: false, zIndex: current ? 3 : 2, icon: {
          path: google.maps.SymbolPath.CIRCLE, scale: current ? 8 : 3,
          fillColor: current ? '#225ec6' : '#238460', fillOpacity: 1,
          strokeColor: 'white', strokeWeight: current ? 2 : 1,
        }};
      });
      tileListener = google.maps.event.addListenerOnce(instance, 'tilesloaded', () => {
        window.clearTimeout(timeout);
        if (active) onReady();
      });
      setMap(instance);
    }
    initialize().catch(() => { if (active) onFailure('Google Maps is unavailable.'); });
    return () => {
      active = false;
      window.clearTimeout(timeout);
      tileListener?.remove();
      authWindow.gm_authFailure = previousAuthHandler;
    };
  }, [onReady, onFailure]);

  useEffect(() => {
    if (!map) return;
    const coords = new Map(track.points.map(p => [p.id, [p.longitude, p.latitude]]));
    const features: object[] = track.points.map(p => ({
      type: 'Feature', properties: {role: p.id === latest.id ? 'latest' : 'point'},
      geometry: {type: 'Point', coordinates: coords.get(p.id)},
    }));
    for (const segment of track.segments) {
      if (segment.connected) features.push({
        type: 'Feature', properties: {role: 'trail'},
        geometry: {type: 'LineString', coordinates: [coords.get(segment.from_id), coords.get(segment.to_id)]},
      });
    }
    const overlays = map.data.addGeoJson({type: 'FeatureCollection', features});
    return () => overlays.forEach(feature => map.data.remove(feature));
  }, [map, track, latest.id]);

  useEffect(() => {
    map?.panTo({lat: latest.latitude, lng: latest.longitude});
  }, [map, latest.latitude, latest.longitude]);

  return <div ref={host} className="google-trail" aria-label="Live vehicle map"/>;
}
