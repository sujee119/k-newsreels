#!/usr/bin/env python3
"""One-time: turn a Desktop OAuth client into a refresh token for ONE channel.

Run it once per channel, signed in as that channel. It prints a refresh
token; that goes into the repo's YT_REFRESH_TOKEN secret and the pipeline
uses it forever.

    python tools/youtube_auth.py

Before running, the consent screen's publishing status must be "In
production". While it is "Testing", Google expires refresh tokens after
seven days and uploads would stop every week with no obvious cause.

Uses a loopback redirect (http://127.0.0.1:PORT). The old out-of-band
flow, urn:ietf:wg:oauth:2.0:oob, was switched off by Google in 2022 and
now fails with "request is invalid".

Nothing is written to disk and nothing is sent anywhere except Google.
"""
import json
import socket
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from getpass import getpass
from http.server import BaseHTTPRequestHandler, HTTPServer

AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/youtube.upload"

_received = {}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):                      # noqa: N802
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _received.update({k: v[0] for k, v in q.items()})
        body = (b"<h2>Done. Close this tab and go back to the terminal.</h2>"
                if "code" in q else
                b"<h2>No code in the callback. Check the terminal.</h2>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):             # keep the console quiet
        pass


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def post(url: str, fields: dict) -> dict:
    body = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        sys.exit(f"\nGoogle refused it:\n{exc.read().decode()[:600]}\n")


def main() -> None:
    print(__doc__)
    client_id = input("Client ID: ").strip()
    client_secret = getpass("Client secret (hidden, paste and press Enter): ").strip()
    if not client_id or not client_secret:
        sys.exit("both are required")

    port = free_port()
    redirect = f"http://127.0.0.1:{port}"
    url = AUTH + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        # Without this, a second authorisation returns no refresh token at
        # all, which is the usual reason the second channel "doesn't work".
        "prompt": "consent",
    })

    server = HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    print("\n" + "=" * 70)
    print("1. Open this URL in a browser signed in as the CHANNEL you want")
    print("   to upload to (switch account first if needed):\n")
    print(url)
    print("\n2. Past the 'Google hasn't verified this app' screen: Advanced,")
    print("   then 'Go to reel uploader (unsafe)'. Expected for your own app.")
    print("3. Approve. The browser lands on a localhost page.")
    print("\nIf that page loads, come back here -- this is already done.")
    print("If instead the browser shows 'can't reach this site', copy the")
    print("WHOLE address bar URL and paste it below.")
    print("=" * 70 + "\n")

    pasted = input("Press Enter when approved, or paste the redirect URL: ").strip()
    server.shutdown()

    code = _received.get("code", "")
    if not code and pasted:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query)
        code = (q.get("code") or [""])[0]
    if not code:
        err = _received.get("error", "")
        sys.exit(f"No authorisation code arrived{' (' + err + ')' if err else ''}. "
                 f"Re-run and paste the full redirect URL from the address bar.")

    data = post(TOKEN, {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect,
    })
    refresh = data.get("refresh_token")
    if not refresh:
        sys.exit("No refresh_token came back. That happens when the account "
                 "has authorised this app before -- revoke it at "
                 "https://myaccount.google.com/permissions and run again.")

    print("\nRefresh token (store it, do not paste it into a chat):\n")
    print(refresh)
    print("\nSet it on the right repo with:\n")
    print("    gh secret set YT_REFRESH_TOKEN\n")


if __name__ == "__main__":
    main()
