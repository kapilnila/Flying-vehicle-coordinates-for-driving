import {useEffect, useRef, useState} from 'react';

type Payload = Record<string, string | number>;
type Run = {
  stopped: boolean; pending: boolean; reason: string; deadline: number;
  next?: number; end?: number; clock?: number; controller: AbortController;
};
type Props = {
  disabled: boolean;
  getPayload: () => Payload | null;
  submit: (payload: Payload, key: string, signal: AbortSignal) => Promise<void>;
  onActive: (active: boolean) => void;
};

function deviceLocation(signal: AbortSignal): Promise<GeolocationPosition> {
  return new Promise((resolve, reject) => {
    if (!window.isSecureContext || !navigator.geolocation) {
      reject(new Error('Device location requires HTTPS or localhost and browser location support.'));
      return;
    }
    const abort = () => reject(new Error('Location cancelled.'));
    signal.addEventListener('abort', abort, {once: true});
    navigator.geolocation.getCurrentPosition(position => {
      signal.removeEventListener('abort', abort);
      resolve(position);
    }, failure => {
      signal.removeEventListener('abort', abort);
      reject(new Error(failure.code === 1 ? 'Location permission was denied. Allow location before restarting.'
        : 'A fresh location could not be obtained. Check location services before restarting.'));
    }, {enableHighAccuracy: true, maximumAge: 0, timeout: 10000});
  });
}

function clearTimers(run: Run) {
  window.clearTimeout(run.next);
  window.clearTimeout(run.end);
  window.clearInterval(run.clock);
}

export function TimedGeneration({disabled, getPayload, submit, onActive}: Props) {
  const [enabled, setEnabled] = useState(false);
  const [duration, setDuration] = useState('60');
  const [interval, setIntervalSeconds] = useState('5');
  const [source, setSource] = useState('form');
  const [active, setActive] = useState(false);
  const [remaining, setRemaining] = useState(0);
  const [saved, setSaved] = useState(0);
  const [status, setStatus] = useState('');
  const [error, setError] = useState('');
  const current = useRef<Run | null>(null);

  useEffect(() => () => {
    const run = current.current;
    current.current = null;
    if (run) {run.stopped = true; clearTimers(run); run.controller.abort();}
    onActive(false);
  }, [onActive]);

  function finish(run: Run) {
    if (current.current !== run) return;
    clearTimers(run);
    current.current = null;
    setActive(false);
    onActive(false);
    setRemaining(0);
    setStatus(run.reason);
  }

  function stop(reason = 'Stopped') {
    const run = current.current;
    if (!run || run.stopped) return;
    run.stopped = true;
    run.reason = reason;
    clearTimers(run);
    setRemaining(0);
    if (run.pending) setStatus(`${reason} — waiting for the current reading to finish`);
    else finish(run);
  }

  function start() {
    if (current.current || disabled) return;
    setError('');
    const seconds = Number(duration), spacing = Number(interval);
    if (!Number.isInteger(seconds) || seconds < 1 || seconds > 3600
        || !Number.isInteger(spacing) || spacing < 1 || spacing > 60) {
      setError('Choose a duration of 1–3600 seconds and an interval of 1–60 seconds.');
      return;
    }
    const values = getPayload();
    if (!values) return;
    if (!String(values.vehicle_id ?? '').trim()) {
      setError('Enter a vehicle ID so these readings can be found in History.');
      return;
    }
    const snapshot = {...values, count: 1};
    const run: Run = {stopped: false, pending: false, reason: 'Completed',
      deadline: performance.now() + seconds * 1000, controller: new AbortController()};
    current.current = run;
    setSaved(0);
    setRemaining(seconds);
    setStatus('Running');
    setActive(true);
    onActive(true);
    run.end = window.setTimeout(() => stop('Completed'), seconds * 1000);
    run.clock = window.setInterval(() => setRemaining(Math.max(0, Math.ceil((run.deadline - performance.now()) / 1000))), 250);

    const tick = async () => {
      if (run.stopped || current.current !== run) return;
      if (performance.now() >= run.deadline) {stop('Completed'); return;}
      run.pending = true;
      const started = performance.now();
      try {
        const payload: Payload = {...snapshot};
        if (source === 'device') {
          const position = await deviceLocation(run.controller.signal);
          if (run.stopped || current.current !== run) return;
          const {latitude, longitude} = position.coords;
          if (![latitude, longitude, position.timestamp].every(Number.isFinite)
              || Math.abs(latitude) > 90 || longitude < -180 || longitude >= 180) {
            throw new Error('The device returned an invalid location.');
          }
          payload.latitude = latitude;
          payload.longitude = longitude;
          payload.captured_at_ms = position.timestamp;
        }
        if (performance.now() >= run.deadline) {stop('Completed'); return;}
        // Every observation is a new submission. Never reuse the one-off form's retry key.
        await submit(payload, crypto.randomUUID(), run.controller.signal);
        if (current.current === run) setSaved(value => value + 1);
      } catch (cause) {
        if (current.current === run) {
          setError(`${cause instanceof Error ? cause.message : 'Request failed'} No automatic retry was sent. Check History before restarting; an unanswered request may have been saved.`);
          run.reason = 'Stopped after an error';
          stop('Stopped after an error');
        }
      } finally {
        run.pending = false;
        if (current.current === run) {
          if (run.stopped) finish(run);
          else {
            // Skip missed intervals; never overlap requests or send a catch-up burst.
            const elapsed = performance.now() - started;
            const delay = spacing * 1000 - elapsed % (spacing * 1000);
            run.next = window.setTimeout(() => void tick(), delay);
          }
        }
      }
    };
    void tick();
  }

  return <div className="timed-generation">
    <label className="timed-toggle"><input type="checkbox" checked={enabled} disabled={active || disabled}
      onChange={event => setEnabled(event.target.checked)}/> Generate continuously for a time period (optional)</label>
    {enabled && <>
      <div className="fields">
        <label>Duration (seconds)<input type="number" min="1" max="3600" step="1" value={duration}
          disabled={active} onChange={event => setDuration(event.target.value)}/></label>
        <label>Interval (seconds)<input type="number" min="1" max="60" step="1" value={interval}
          disabled={active} onChange={event => setIntervalSeconds(event.target.value)}/></label>
        <label>Coordinate source<select value={source} disabled={active} onChange={event => setSource(event.target.value)}>
          <option value="form">Repeat form values</option><option value="device">Fresh device location</option>
        </select></label>
      </div>
      <p className="muted">One ID per reading, starting immediately; batch size is ignored. Keep this page open.
        Slow requests or background browser throttling can reduce the number of readings. Leaving Generate stops the run.</p>
      <p className="muted">{source === 'device'
        ? 'Requests a fresh device location and capture time for each reading. Altitude, altitude reference, and speed stay at the reviewed form values; they are not measured again.'
        : 'Repeats the same coordinates, altitude, and speed. This does not track movement. A supplied capture time stays unchanged; when blank, the server uses each request’s receipt time.'}</p>
      <div className="actions">
        <button type="button" disabled={active} onClick={() => setDuration('60')}>1 minute</button>
        <button type="button" className="primary" disabled={disabled || active} onClick={start}>Start timed generation</button>
        {active && <button type="button" disabled={current.current?.stopped} onClick={() => stop()}>Stop generation</button>}
      </div>
      {status && <p role="status">{status} · {saved} IDs saved{active ? ` · ${remaining}s remaining` : ''}.
        {' '}The output shows the latest result; all saved readings are available in History.</p>}
      {error && <p role="alert" className="message error">{error}</p>}
    </>}
  </div>;
}
