import {useEffect, useRef, useState} from 'react';

type Location = {latitude: number; longitude: number; accuracy: number; timestamp: number};
type Props = {
  latitude: string;
  longitude: string;
  disabled: boolean;
  onLocation: (location: Location) => void;
  onBusy: (busy: boolean) => void;
};

export function CurrentLocation({latitude, longitude, disabled, onLocation, onBusy}: Props) {
  const [pending, setPending] = useState(false);
  const [sample, setSample] = useState<Location | null>(null);
  const [error, setError] = useState('');
  const attempt = useRef(0);

  useEffect(() => {
    // Discard a late reading after manual edits or leaving the Generate view.
    attempt.current++;
    setPending(false);
    onBusy(false);
    return () => {attempt.current++; onBusy(false);};
  }, [latitude, longitude, onBusy]);

  function cancel() {
    attempt.current++;
    setPending(false);
    onBusy(false);
  }

  function locate() {
    setError('');
    setSample(null);
    if (!window.isSecureContext) {
      setError('Open this website over HTTPS to use your location.');
      return;
    }
    if (!navigator.geolocation) {
      setError('Location is unavailable in this browser. Enter coordinates manually.');
      return;
    }
    const id = ++attempt.current;
    setPending(true);
    onBusy(true);
    const fail = (message: string) => {
      if (id !== attempt.current) return;
      cancel();
      setError(message);
    };
    try {
      navigator.geolocation.getCurrentPosition(position => {
        if (id !== attempt.current) return;
        const {latitude, longitude, accuracy} = position.coords;
        if (![latitude, longitude, accuracy, position.timestamp].every(Number.isFinite)
            || Math.abs(latitude) > 90 || Math.abs(longitude) > 180 || accuracy < 0) {
          fail('The device returned an invalid location. Try again or enter coordinates manually.');
          return;
        }
        const value = {latitude, longitude, accuracy, timestamp: position.timestamp};
        cancel();
        setSample(value);
        onLocation(value);
      }, failure => {
        const messages: Record<number, string> = {
          1: 'Location permission was denied. Allow location in your browser settings or enter coordinates manually.',
          2: 'Your device could not determine its location. Check location services and try again.',
          3: 'Finding your location timed out. Try again or enter coordinates manually.',
        };
        fail(messages[failure.code] ?? 'Unable to read your location. Enter coordinates manually.');
      }, {enableHighAccuracy: true, timeout: 10000, maximumAge: 0});
    } catch {
      fail('Location access is unavailable. Check browser permissions or enter coordinates manually.');
    }
  }

  const matches = sample && String(sample.latitude) === latitude && String(sample.longitude) === longitude;
  return <div className="current-location">
    <div className="actions">
      <button type="button" disabled={disabled || pending} onClick={locate}>
        {pending ? 'Finding location…' : 'Use my current location'}
      </button>
      {pending && <button type="button" onClick={cancel}>Cancel location</button>}
    </div>
    <p className="muted">Use this device’s location to fill coordinates and capture time.
      Review altitude and speed before saving with Generate IDs.</p>
    {pending && <p role="status">Waiting for your device’s location permission or reading.</p>}
    {matches && <p role="status">Location filled · Reported accuracy ±{Math.ceil(sample.accuracy)} m
      · Captured {new Date(sample.timestamp).toLocaleString()}</p>}
    {error && <p className="message error" role="alert">{error}</p>}
  </div>;
}
