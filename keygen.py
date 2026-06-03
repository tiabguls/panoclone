#!/usr/bin/env python3
"""Generate a Panorama API key."""

import argparse
import getpass
import json
import os
import ssl
import sys
import urllib.error
import urllib.request


_SSL_CONTEXT = ssl.create_default_context()
_SSL_CONTEXT.check_hostname = False
_SSL_CONTEXT.verify_mode = ssl.CERT_NONE


def api_error(url, code, body):
    try:
        error_data = json.loads(body)
    except Exception:
        error_data = body
    print(f"API Error {code} for {url}:")
    print(json.dumps(error_data, indent=2) if isinstance(error_data, dict) else error_data)
    sys.exit(1)


def post_json(url, body):
    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, context=_SSL_CONTEXT, timeout=30) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        api_error(url, e.code, e.read().decode())
    except urllib.error.URLError as e:
        print(f"Connection error for {url}: {e.reason}")
        sys.exit(1)


def get_json(url, api_key):
    req = urllib.request.Request(url, headers={"X-PAN-KEY": api_key})
    try:
        with urllib.request.urlopen(req, context=_SSL_CONTEXT, timeout=30) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        api_error(url, e.code, e.read().decode())
    except urllib.error.URLError as e:
        print(f"Connection error for {url}: {e.reason}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Generate a Palo Alto Panorama API key.")
    parser.add_argument("hostname", help="Panorama API hostname")
    args = parser.parse_args()

    username = input("Username: ")
    password = getpass.getpass("Password: ")

    base_url = f"https://{args.hostname}/restapi/v11.1"

    print("\nRequesting API key...")
    data = post_json(f"{base_url}/ApiKeys", {"username": username, "password": password})
    api_key = (data.get("result") or {}).get("key")
    if not api_key:
        print("Error: No API key in response.")
        print(json.dumps(data, indent=2))
        sys.exit(1)

    print("Validating API key...")
    get_json(f"{base_url}/Panorama/DeviceGroups", api_key)
    print("API key validated successfully.")

    os.environ["PANORAMA_KEY"] = api_key
    print(f"\nAPI Key: {api_key}")
    print(f"\nTo use in your shell:\n  export PANORAMA_KEY={api_key}")


if __name__ == "__main__":
    main()
