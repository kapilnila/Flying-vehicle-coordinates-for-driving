import {useEffect, useState} from 'react';
import {helpExamples, type ExampleLanguage} from './helpExamples';

export type HelpDestination = 'Generate' | 'History' | 'Paths';
type Mode = 'loading' | 'public' | 'protected' | 'unavailable';

export function Help({navigate}: {navigate: (destination: HelpDestination) => void}) {
  const [language, setLanguage] = useState<ExampleLanguage>('Python');
  const [mode, setMode] = useState<Mode>('loading');
  const [attempt, setAttempt] = useState(0);
  const [copyStatus, setCopyStatus] = useState('');
  const [copyError, setCopyError] = useState('');
  const origin = window.location.origin;
  const examples = helpExamples(origin, mode === 'public');

  useEffect(() => {
    const controller = new AbortController();
    setMode('loading');
    fetch('/v1/config', {cache: 'no-store', signal: AbortSignal.any([controller.signal, AbortSignal.timeout(12000)])})
      .then(async response => {
        if (!response.ok) throw new Error('Configuration unavailable');
        const value = await response.json();
        if (typeof value.public_api !== 'boolean') throw new Error('Invalid configuration');
        if (!controller.signal.aborted) setMode(value.public_api ? 'public' : 'protected');
      })
      .catch(() => {if (!controller.signal.aborted) setMode('unavailable');});
    return () => controller.abort();
  }, [attempt]);

  async function copy(value: string, label: string) {
    setCopyStatus('');
    setCopyError('');
    try {
      await navigator.clipboard.writeText(value);
      setCopyStatus(label + ' copied.');
    } catch {
      setCopyError('Copy is unavailable. Select the displayed text and copy it manually.');
    }
  }

  return <div className="help-page">
    <section className="panel">
      <h2>Save your first point</h2>
      <ol className="help-steps">
        <li><strong>Choose a vehicle ID.</strong> Use one stable name, such as VEH-1024, for all readings from
          the same vehicle. Each saved point receives its own unique ID.</li>
        <li><strong>Fill the coordinates.</strong> In Generate, enter latitude and longitude or choose
          Use my current location. Allow the browser’s location permission and use HTTPS or localhost.
          This reads the device you are using and fills coordinates and capture time. Check its reported accuracy.</li>
        <li><strong>Review the reading and save.</strong> Enter the actual altitude and speed, choose the
          altitude reference, and click Generate IDs. The form’s initial values are examples.
          For one observation, leave batch size at 1.</li>
        <li><strong>Retrieve the values.</strong> In History, search the same vehicle ID, open a point’s
          details, or export JSONL. Latest record selects by capture time.</li>
        <li><strong>Follow the vehicle.</strong> In Paths, select Live map and follow its vehicle ID.
          It refreshes the latest 100 saved points every five seconds. New readings must be submitted
          by your device or API client. Google Maps and terrain lookup are optional.</li>
      </ol>
      <div className="actions">
        <button onClick={() => navigate('Generate')}>Open Generate</button>
        <button onClick={() => navigate('History')}>Open History</button>
        <button onClick={() => navigate('Paths')}>Open Paths</button>
      </div>
    </section>

    <section className="panel">
      <h2>Understand the input values</h2>
      <p>Send a JSON object to <code>POST /v1/ids</code> with <code>Content-Type: application/json</code>.
        Required numeric fields must be numbers, such as <code>12.9716</code>, without quotes.</p>
      <div className="table-scroll"><table><thead><tr><th>Field</th><th>Meaning</th></tr></thead><tbody>
        <tr><td><code>latitude</code> · required</td><td>Degrees north or south, from −90 to 90.</td></tr>
        <tr><td><code>longitude</code> · required</td><td>Degrees east or west, from −180 up to but excluding 180.</td></tr>
        <tr><td><code>altitude_m</code> · required</td><td>Reported altitude in metres, from −1024 up to but excluding 3072.</td></tr>
        <tr><td><code>speed_mps</code> · required</td><td>Speed in metres per second, from 0 up to but excluding 256. Divide km/h by 3.6.</td></tr>
        <tr><td><code>vehicle_id</code></td><td>Recommended for history and tracking. Use 1–128 letters, digits, dots, underscores, colons, or hyphens.</td></tr>
        <tr><td><code>altitude_reference</code></td><td>Use <code>msl</code> only for metres above mean sea level.
          Choose <code>unknown</code> when unsure. Ground elevation is a separate reading.</td></tr>
        <tr><td><code>captured_at_ms</code></td><td>When the device measured the point, as Unix milliseconds.
          JavaScript uses <code>Date.now()</code>. If omitted, the server uses receipt time.</td></tr>
        <tr><td><code>count</code></td><td>Defaults to 1. A batch of 1–1000 creates that many IDs carrying the same reading.</td></tr>
        <tr><td><code>vehicle_class</code></td><td>An integer category from 0–15, default 0. Choose category meanings consistently in your own application.</td></tr>
      </tbody></table></div>
    </section>

    <section className="panel">
      <h2>Connect your API client</h2>
      <p>Use this website’s address as the base URL for your device or application.</p>
      <div className="help-base"><code>{origin}</code>
        <button onClick={() => void copy(origin, 'Base URL')}>Copy base URL</button></div>
      {mode === 'loading' && <p role="status">Checking API access mode…</p>}
      {mode === 'public' && <p>Public vehicle access is enabled. These examples work without an API token.</p>}
      {mode === 'protected' && <p>Public access is disabled. If the service requires authentication,
        obtain the API token from its operator and set the <code>API_TOKEN</code> environment variable
        in your terminal. These examples read that variable and send a Bearer authorization header.
        Local installations may have authentication unconfigured.</p>}
      {mode === 'unavailable' && <div><p>Access mode could not be checked. Examples support an
        <code> API_TOKEN</code> environment variable if your operator requires authentication.</p>
        <button onClick={() => setAttempt(value => value + 1)}>Retry access check</button></div>}
      {(mode === 'protected' || mode === 'unavailable') && <details>
        <summary>Set a token in your terminal</summary>
        <p>Replace the placeholder with the credential supplied by your operator, then run the example
          in the same terminal session.</p>
        <p>PowerShell</p><pre><code>{"$env:API_TOKEN = 'your-api-token'"}</code></pre>
        <p>Bash</p><pre><code>{"export API_TOKEN='your-api-token'"}</code></pre>
      </details>}
      <p>Running an example below creates one sample point under a new DEMO vehicle ID and prints
        the saved values. Replace the sample values with your device readings when connecting your application.</p>
      <div className="path-modes help-languages" role="group" aria-label="Example language">
        {(['Python', 'JavaScript', 'cURL'] as const).map(name =>
          <button key={name} aria-pressed={language === name} onClick={() => {
            setLanguage(name); setCopyStatus(''); setCopyError('');
          }}>{name}</button>)}
      </div>
      <p className="muted">{language === 'Python' ? 'Python 3 · standard library only' :
        language === 'JavaScript' ? 'Node.js 22+ · built-in fetch · save as .mjs' :
          'Bash-compatible terminal · curl 7.84+ · Python 3 available as python for JSON handling'}</p>
      <button disabled={mode === 'loading'} onClick={() => void copy(examples[language], language + ' example')}>
        Copy {language} example
      </button>
      {copyStatus && <p role="status">{copyStatus}</p>}
      {copyError && <p role="alert">{copyError}</p>}
      <pre tabIndex={0} aria-label={language + ' example'}><code>{examples[language]}</code></pre>
      <p>Each script generates one <code>Idempotency-Key</code> for its submission. If the response is
        lost or times out, repeat the submission with that same key and unchanged payload, including
        capture time. Re-running the entire script generates a new key and creates a new sample.
        Use a new key for each new observation. A changed payload with an existing key returns HTTP 409.</p>
    </section>

    <section className="panel">
      <h2>Read the response and saved data</h2>
      <dl className="help-definitions">
        <dt>ids</dt><dd>The saved point IDs. Use the first ID from a single-point submission with
          <code> GET /v1/records/&#123;id&#125;</code> to retrieve its original values.</dd>
        <dt>request_key</dt><dd>The logical submission key used for retry deduplication.
          A matching retry returns the same IDs with HTTP 200 and <code>Idempotency-Replayed: true</code>.
          A new submission returns HTTP 201.</dd>
        <dt>X-Request-ID</dt><dd>A response header identifying this individual API attempt.
          An operator can search it in the request log. Each retry has a different request ID.</dd>
        <dt>captured_at_ms and generated_at_ms</dt><dd>The device observation time and the server’s ID
          generation time, both Unix milliseconds. Late-arriving points keep their capture time.</dd>
        <dt>Decode and original records</dt><dd><code>GET /v1/ids/&#123;id&#125;</code> decodes the approximate
          cells and ranges embedded in an ID. The original precise submitted values come from
          <code> GET /v1/records/&#123;id&#125;</code>.</dd>
      </dl>
      <p><code>GET /v1/vehicles/&#123;vehicle&#125;/latest</code> returns the newest captured point.
        <code> GET /v1/vehicles/&#123;vehicle&#125;/records</code> returns <code>items</code> and
        <code> next_cursor</code>. Send that cursor on the next request until it is null.
        URL-encode vehicle IDs in request paths.</p>
      <p><strong>Operator request logs.</strong> Open History → Request log and enter the separate
        operator token to review 90 days of API activity. Expiring a request log leaves its vehicle
        points available through History.</p>
    </section>

    <section className="panel">
      <h2>Troubleshooting</h2>
      <details><summary>Location permission is denied or the reading times out</summary>
        <p>Allow location for this site in browser settings, enable device location services, and open
          the HTTPS website. Try again or enter coordinates manually. Review reported accuracy before saving.</p></details>
      <details><summary>Waiting for backend or a service error</summary>
        <p>Open Health for connection details. A sleeping hosted service may need time to start.
          Retry after it responds. If authentication is enabled, enter the API token. Persistent errors
          require the service operator to check the deployment and database.</p></details>
      <details><summary>HTTP 400, 413, or invalid values</summary>
        <p>Check required numeric fields, ranges, and the altitude reference. Use Unix milliseconds
          for capture time and a batch size from 1–1000. Send valid JSON under 64 KiB with supported fields.</p></details>
      <details><summary>HTTP 401 or 403</summary>
        <p>Use the vehicle API token when authentication is enabled. Request-log access requires the
          distinct operator token. HTTP 403 on the request log means operator access is disabled
          until its credential is configured correctly.</p></details>
      <details><summary>History is empty or latest returns HTTP 404</summary>
        <p>Confirm you saved a point successfully and are using exactly the same vehicle ID and server.
          Clear time filters and check the next page. A submission without a vehicle ID can be retrieved
          by its saved point ID.</p></details>
    </section>
  </div>;
}
