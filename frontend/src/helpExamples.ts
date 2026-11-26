export type ExampleLanguage = 'Python' | 'JavaScript' | 'cURL';

export function helpExamples(origin: string, publicApi: boolean) {
  const base = JSON.stringify(origin);
  const pythonAuth = publicApi ? '' : `
token = os.environ.get("API_TOKEN", "")
if token:
    headers["Authorization"] = "Bearer " + token
`;
  const javascriptAuth = publicApi ? '' : `
if (process.env.API_TOKEN) {
  headers.Authorization = "Bearer " + process.env.API_TOKEN;
}
`;
  const curlAuth = publicApi ? '' : `
if [ -n "\${API_TOKEN:-}" ]; then
  auth=(-H "Authorization: Bearer $API_TOKEN")
fi
`;
  return {
    Python: `# Python 3. Save as example.py and run: python example.py
# Running this script creates one sample point.
import json
import os
import time
import uuid
from urllib.parse import quote
from urllib.request import Request, urlopen

base_url = ${base}
request_key = str(uuid.uuid4())
vehicle_id = "DEMO-" + request_key
headers = {"Content-Type": "application/json"}
${pythonAuth}
# Sample values. Replace these with readings from your device.
payload = {
    "vehicle_id": vehicle_id,
    "latitude": 12.9716,
    "longitude": 77.5946,
    "altitude_m": 120.4,
    "altitude_reference": "unknown",
    "speed_mps": 0,
    "count": 1,
    "captured_at_ms": int(time.time() * 1000),
}

def request(path, body=None):
    request_headers = dict(headers)
    if body is not None:
        request_headers["Idempotency-Key"] = request_key
    req = Request(base_url + path,
                  data=json.dumps(body).encode() if body is not None else None,
                  headers=request_headers)
    with urlopen(req, timeout=30) as response:
        print("X-Request-ID:", response.headers.get("X-Request-ID"))
        return json.load(response)

# On a timeout, retry this call with the same payload and request_key.
created = request("/v1/ids", payload)
point_id = created["ids"][0]
vehicle_path = "/v1/vehicles/" + quote(vehicle_id, safe="")
record = request("/v1/records/" + point_id)
latest = request(vehicle_path + "/latest")
history = request(vehicle_path + "/records?limit=25")
print(json.dumps({"created": created, "record": record,
                  "latest": latest, "history": history}, indent=2))
`,
    JavaScript: `// Node.js 22+. Save as example.mjs and run: node example.mjs
// Running this script creates one sample point.
import {randomUUID} from 'node:crypto';

const baseUrl = ${base};
const requestKey = randomUUID();
const vehicleId = "DEMO-" + requestKey;
const headers = {"Content-Type": "application/json"};
${javascriptAuth}
// Sample values. Replace these with readings from your device.
const payload = {
  vehicle_id: vehicleId,
  latitude: 12.9716,
  longitude: 77.5946,
  altitude_m: 120.4,
  altitude_reference: "unknown",
  speed_mps: 0,
  count: 1,
  captured_at_ms: Date.now(),
};

async function request(path, body) {
  const response = await fetch(baseUrl + path, {
    method: body === undefined ? "GET" : "POST",
    headers: {...headers, ...(body === undefined ? {} : {"Idempotency-Key": requestKey})},
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(30000),
  });
  console.log("X-Request-ID:", response.headers.get("X-Request-ID"));
  if (!response.ok) throw new Error("HTTP " + response.status + ": " + await response.text());
  return response.json();
}

// On a timeout, retry this call with the same payload and requestKey.
const created = await request("/v1/ids", payload);
const pointId = created.ids[0];
const vehiclePath = "/v1/vehicles/" + encodeURIComponent(vehicleId);
const record = await request("/v1/records/" + pointId);
const latest = await request(vehiclePath + "/latest");
const history = await request(vehiclePath + "/records?limit=25");
console.log(JSON.stringify({created, record, latest, history}, null, 2));
`,
    cURL: `#!/usr/bin/env bash
# Requires Bash, curl, and Python 3 available as "python" for JSON handling.
# Save as example.sh and run: bash example.sh
# Running this script creates one sample point.
set -euo pipefail
BASE_URL=${base}
REQUEST_KEY="$(python -c 'import uuid; print(uuid.uuid4())')"
VEHICLE_ID="DEMO-$REQUEST_KEY"
auth=()
${curlAuth}
# Sample values. Replace these with readings from your device.
PAYLOAD="$(python - "$VEHICLE_ID" <<'PY'
import json, sys, time
print(json.dumps({
    "vehicle_id": sys.argv[1],
    "latitude": 12.9716,
    "longitude": 77.5946,
    "altitude_m": 120.4,
    "altitude_reference": "unknown",
    "speed_mps": 0,
    "count": 1,
    "captured_at_ms": int(time.time() * 1000),
}))
PY
)"

# The request ID is printed to stderr.
# On a timeout, retry this command with the same PAYLOAD and REQUEST_KEY.
CREATED="$(curl --fail-with-body --silent --show-error --max-time 30 \\
  --write-out '%{stderr}X-Request-ID: %header{x-request-id}\\n' "\${auth[@]}" -H 'Content-Type: application/json' \\
  -H "Idempotency-Key: $REQUEST_KEY" --data "$PAYLOAD" "$BASE_URL/v1/ids")"
POINT_ID="$(printf '%s' "$CREATED" | python -c 'import json, sys; print(json.load(sys.stdin)["ids"][0])')"
# This generated vehicle ID contains only URL-safe letters, digits, and hyphens.
VEHICLE_URL="$BASE_URL/v1/vehicles/$VEHICLE_ID"
RECORD="$(curl --fail-with-body --silent --show-error --max-time 30 "\${auth[@]}" "$BASE_URL/v1/records/$POINT_ID")"
LATEST="$(curl --fail-with-body --silent --show-error --max-time 30 "\${auth[@]}" "$VEHICLE_URL/latest")"
HISTORY="$(curl --fail-with-body --silent --show-error --max-time 30 "\${auth[@]}" "$VEHICLE_URL/records?limit=25")"
python - "$CREATED" "$RECORD" "$LATEST" "$HISTORY" <<'PY'
import json, sys
print(json.dumps(dict(zip(
    ["created", "record", "latest", "history"],
    [json.loads(value) for value in sys.argv[1:]]
)), indent=2))
PY
`,
  };
}
