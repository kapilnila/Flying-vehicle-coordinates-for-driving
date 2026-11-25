"""Request the deployed application's liveness endpoint from an external scheduler."""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def health_url(base_url):
    parsed = urllib.parse.urlsplit(base_url.strip())
    if (parsed.scheme not in ("http", "https") or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ("", "/")):
        raise ValueError("RENDER_APP_URL must be an HTTP(S) origin without credentials, query, or path.")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "/livez", "", ""))


def check(base_url):
    url = health_url(base_url)
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "spatial-id-keepalive"})
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = json.loads(response.read(4097))
                if response.status != 200 or data != {"status": "alive"}:
                    raise ValueError("unexpected health response")
            print(f"Liveness check succeeded on attempt {attempt}.")
            return True
        except (OSError, ValueError, urllib.error.URLError) as error:
            print(f"Liveness check attempt {attempt}/3 failed ({type(error).__name__}).", file=sys.stderr)
            if attempt < 3:
                time.sleep(5)
    return False


def main():
    url = os.getenv("RENDER_APP_URL", "")
    if not url:
        print("Set RENDER_APP_URL to the deployed Render origin.", file=sys.stderr)
        return 1
    try:
        return 0 if check(url) else 1
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
