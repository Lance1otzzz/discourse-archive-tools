#!/usr/bin/env python3
"""
Request a Discourse User API key using the official RSA payload flow.

The key is printed once. The script does not persist it.
"""

from __future__ import annotations

import argparse
import base64
import json
import secrets
import sys
import uuid
import webbrowser
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


def configure_utf8_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


configure_utf8_stdio()


ALL_SCOPES = {
    "bookmarks_calendar",
    "message_bus",
    "notifications",
    "one_time_password",
    "push",
    "read",
    "session_info",
    "user_status",
    "write",
}


@dataclass(frozen=True)
class UserApiKeyPayload:
    key: str
    nonce: str
    push: bool
    api: int


def normalize_site_url(site_url: str) -> str:
    parsed = urlparse(site_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("site URL must look like https://forum.example.com")
    return f"{parsed.scheme}://{parsed.netloc}"


def build_authorization_url(site_url: str, application_name: str, client_id: str, scopes: list[str], public_key: str, nonce: str) -> str:
    params = {
        "application_name": application_name,
        "client_id": client_id,
        "scopes": ",".join(scopes),
        "public_key": public_key,
        "nonce": nonce,
    }
    return f"{site_url}/user-api-key/new?{urlencode(params, quote_via=quote)}"


def decrypt_payload(enc_payload: str, private_key: rsa.RSAPrivateKey, expected_nonce: str) -> UserApiKeyPayload:
    decoded = base64.b64decode(enc_payload.strip())
    decrypted = private_key.decrypt(decoded, padding.PKCS1v15())
    payload = UserApiKeyPayload(**json.loads(decrypted))
    if payload.nonce != expected_nonce:
        raise ValueError("nonce mismatch")
    return payload


def request_key(args: argparse.Namespace) -> tuple[str, UserApiKeyPayload]:
    scopes = args.scopes.split(",") if isinstance(args.scopes, str) else args.scopes
    scopes = [scope.strip() for scope in scopes if scope.strip()]
    invalid = sorted(set(scopes) - ALL_SCOPES)
    if invalid:
        raise ValueError(f"invalid scopes: {', '.join(invalid)}")

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=args.key_size)
    public_key_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")

    client_id = args.client_id or str(uuid.uuid4())
    nonce = secrets.token_urlsafe(32)
    site_url = normalize_site_url(args.site_url)
    authorization_url = build_authorization_url(site_url, args.application_name, client_id, scopes, public_key_pem, nonce)

    print("Open this URL while logged in:")
    print(authorization_url)
    if args.open_browser:
        webbrowser.open(authorization_url)
    print()
    enc_payload = input("Paste the returned payload here: ")
    return client_id, decrypt_payload(enc_payload, private_key, nonce)


def test_key(site_url: str, key: str, query: str, timeout: float) -> dict[str, Any]:
    url = f"{normalize_site_url(site_url)}/search.json?q={quote(query)}"
    request = Request(url, headers={"User-Api-Key": key, "Accept": "application/json"}, method="GET")
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Request a Discourse User API key.")
    parser.add_argument("--site-url", default="https://shuiyuan.sjtu.edu.cn", help="Discourse site base URL.")
    parser.add_argument("--application-name", default="Discourse Archive Tool", help="Name shown on the authorization page.")
    parser.add_argument("--client-id", help="Stable client ID for this application. Reuse it to rotate the key.")
    parser.add_argument("--scopes", default="read", help="Comma-separated scopes. Default: read")
    parser.add_argument("--key-size", type=int, default=4096, help="RSA key size. Default: 4096")
    parser.add_argument("--open-browser", action="store_true", help="Open the authorization URL in the default browser.")
    parser.add_argument("--test-query", help="Optional search query used to test the returned key.")
    parser.add_argument("--timeout", type=float, default=10.0, help="Test request timeout. Default: 10")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    try:
        args = parse_args(argv)
        client_id, payload = request_key(args)
        print()
        print("User API key granted.")
        print(f"client_id={client_id}")
        print(f"key={payload.key}")
        if args.test_query:
            result = test_key(args.site_url, payload.key, args.test_query, args.timeout)
            print()
            print(json.dumps(result, ensure_ascii=False, indent=2)[:4000])
        return 0
    except Exception as exc:
        print(f"fatal: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
