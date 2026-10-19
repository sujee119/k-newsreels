#!/usr/bin/env python3
"""Bind one YouTube channel to one repo, and SAY WHICH CHANNEL.

    python3 ytbind.py sujee119/news-reels

A refresh token is tied to the channel chosen at the consent screen. Pick
the wrong one there and uploads land on the wrong channel forever, with no
error -- which is exactly what happened. So this asks Google which channel
the new token actually controls, prints the name, and only writes the repo
secret after you confirm it.

Adds the read-only scope purely so that question can be asked; the upload
path still only ever uses youtube.upload.
"""
import json
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from getpass import getpass
from http.server import BaseHTTPRequestHandler, HTTPServer

AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN = "https://oauth2.googleapis.com/token"
MINE = ("https://www.googleapis.com/youtube/v3/channels"
        "?part=snippet&mine=true")
SCOPES = " ".join([
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
])

_got = {}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):                                        # noqa: N802
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        _got.update({k: v[0] for k, v in q.items()})
        body = (b"<h2>Done. Close this tab, go back to the terminal.</h2>"
                if "code" in q else
                b"<h2>No code in the callback. Check the terminal.</h2>")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def post(url: str, fields: dict) -> dict:
    req = urllib.request.Request(
        url, data=urllib.parse.urlencode(fields).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        sys.exit(f"\nGoogle refused it:\n{exc.read().decode()[:600]}\n")


def whose_channel(access: str) -> tuple:
    req = urllib.request.Request(
        MINE, headers={"Authorization": f"Bearer {access}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return ("", f"could not read the channel: {exc.read().decode()[:200]}")
    items = data.get("items") or []
    if not items:
        return ("", "this account has no YouTube channel")
    sn = items[0].get("snippet", {})
    return (sn.get("title", "(untitled)"), "")


def main() -> None:
    if len(sys.argv) != 2 or "/" not in sys.argv[1]:
        sys.exit("usage: python3 ytbind.py owner/repo")
    repo = sys.argv[1]

    print(__doc__)
    print(f"Binding a channel to: {repo}\n")
    client_id = input("Client ID: ").strip()
    if not client_id.endswith(".apps.googleusercontent.com"):
        sys.exit("Client ID must end in .apps.googleusercontent.com -- a "
                 "truncated paste here is what caused the invalid_client error")
    client_secret = getpass("Client secret (hidden): ").strip()
    if not client_secret:
        sys.exit("client secret is required")

    port = free_port()
    redirect = f"http://127.0.0.1:{port}"
    url = AUTH + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        # Forces a fresh refresh token even though this app was authorised
        # before, so nothing has to be revoked at myaccount.google.com --
        # revoking there would kill the other channel's token too.
        "prompt": "consent",
    })

    server = HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    print("\n" + "=" * 70)
    print("Open this in a NEW INCOGNITO WINDOW so it cannot silently reuse")
    print("the account you are already signed in as:\n")
    print(url)
    print("\nThen, in order:")
    print("  1. Sign in to the Google account that owns the channel you want.")
    print("  2. If a list of channels appears, PICK THE RIGHT ONE. This is")
    print("     the step that sent the science reels to the Korea channel.")
    print("  3. 'Google hasn't verified this app' -> Advanced -> Go to ...")
    print("  4. Leave both permission checkboxes ticked and continue.")
    print("=" * 70 + "\n")

    # Keep asking rather than exiting: under WSL the browser runs on Windows
    # and often cannot reach a listener inside WSL, so the paste is the normal
    # path, not the exception -- and a fumbled paste should not cost a whole
    # re-authorisation.
    code = ""
    for _ in range(6):
        pasted = input("\nPress Enter once approved, or paste the redirect URL "
                       "here (q to quit): ").strip()
        if pasted.lower() == "q":
            break
        code = _got.get("code", "")
        if code:
            break
        if pasted:
            q = urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query)
            code = (q.get("code") or [""])[0]
            if not code and pasted.startswith("4/") and " " not in pasted:
                code = pasted          # just the code, without the URL round it
            if code:
                break
            print("  !! no ?code= in that URL. Copy the WHOLE address bar.")
        else:
            print("  !! nothing reached the loopback port. If the browser says")
            print("     'can't reach this site', that is expected on WSL --")
            print("     copy its whole address bar URL and paste it HERE, at")
            print("     this prompt. Pasting it at the shell prompt will not")
            print("     work; bash splits the URL on its & characters.")
    server.shutdown()

    if not code:
        err = _got.get("error", "")
        sys.exit(f"No authorisation code{' (' + err + ')' if err else ''}. "
                 f"Nothing was changed; re-run when ready.")

    data = post(TOKEN, {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect,
    })
    refresh = data.get("refresh_token")
    access = data.get("access_token", "")
    if not refresh:
        sys.exit("No refresh_token came back. Re-run; the consent prompt must "
                 "be shown, not skipped.")

    title, problem = whose_channel(access)
    print("\n" + "=" * 70)
    if problem:
        print(f"!! {problem}")
        print("Continuing is a guess -- it may be the wrong channel again.")
    else:
        print(f"This token uploads to the channel:  {title}")
    print("=" * 70)

    if input("\nIs that the right channel for this repo? [y/N] ").strip().lower() != "y":
        print("\nNothing was written. Re-run and pick the other channel at the")
        print("account/channel step.")
        return

    for name, value in (("YT_CLIENT_ID", client_id),
                        ("YT_CLIENT_SECRET", client_secret),
                        ("YT_REFRESH_TOKEN", refresh)):
        done = subprocess.run(["gh", "secret", "set", name, "-R", repo],
                              input=value.encode())
        if done.returncode != 0:
            sys.exit(f"\n!! {name} did NOT get set, so the repo is now in a "
                     f"half-changed state. Fix whatever gh complained about "
                     f"and re-run this whole script.")
        print(f"  set {name}")
    print(f"\nDone. {repo} now uploads to: {title or 'that channel'}")


if __name__ == "__main__":
    main()
