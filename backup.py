#!/usr/bin/env python3
"""Panorama configuration backup tool."""

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from prefect import flow, task

_SSL_CONTEXT = ssl.create_default_context()

OBJECT_TYPES = [
    ("Tags",          "Tags"),
    ("Addresses",     "Addresses"),
    ("AddressGroups", "AddressGroups"),
    ("Services",      "Services"),
    ("ServiceGroups", "ServiceGroups"),
]


def make_request(url, api_key):
    """Make a GET request to the Panorama API and return parsed JSON."""
    req = urllib.request.Request(url, headers={"X-PAN-KEY": api_key})
    try:
        with urllib.request.urlopen(req, context=_SSL_CONTEXT) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            error_data = json.loads(body)
        except Exception:
            error_data = body
        print(f"API Error {e.code} for {url}:")
        print(json.dumps(error_data, indent=2) if isinstance(error_data, dict) else error_data)
        raise RuntimeError(f"API Error {e.code}") from e
    except urllib.error.URLError as e:
        print(f"Connection error for {url}: {e.reason}")
        raise RuntimeError(f"Connection error: {e.reason}") from e


def save_json(data, output_dir, timestamp, name):
    """Save data as a JSON file in the output directory."""
    filename = f"{timestamp}-{name}.json"
    filepath = os.path.join(output_dir, filename)
    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)
    print(f"  Saved: {filename}")


def get_entries(data):
    """Extract a list of entries from a Panorama API response."""
    entry = (data.get("result") or {}).get("entry")
    if entry is None:
        return []
    return [entry] if isinstance(entry, dict) else entry


def safe_name(name):
    """Sanitize a name for use in a filename."""
    return name.replace(" ", "_").replace("/", "-")


@task
def fetch_and_save(url, api_key, output_dir, timestamp, name):
    """Fetch data from the Panorama API, save to JSON, and return the response."""
    data = make_request(url, api_key)
    save_json(data, output_dir, timestamp, name)
    return data


@flow(name="panorama-backup", log_prints=True)
def backup_flow(base_url, api_key, output_dir, timestamp, device_groups_data):
    device_groups = [e["@name"] for e in get_entries(device_groups_data)]
    print(f"Found {len(device_groups)} device group(s): {', '.join(device_groups)}")

    # Templates and Template Stacks in parallel
    templates_future = fetch_and_save.with_options(name="fetch-templates").submit(
        f"{base_url}/Panorama/Templates", api_key, output_dir, timestamp, "templates"
    )
    stacks_future = fetch_and_save.with_options(name="fetch-template-stacks").submit(
        f"{base_url}/Panorama/TemplateStacks", api_key, output_dir, timestamp, "template-stacks"
    )

    templates_data = templates_future.result()
    stacks_future.result()

    templates = [e["@name"] for e in get_entries(templates_data)]
    print(f"Found {len(templates)} template(s): {', '.join(templates)}")

    # VirtualSystems per Template — submitted in parallel
    vsys_futures = {
        template: fetch_and_save.with_options(name=f"fetch-vsys-{safe_name(template)}").submit(
            f"{base_url}/Panorama/Templates/VirtualSystems?template={urllib.parse.quote(template)}",
            api_key, output_dir, timestamp,
            f"template-{safe_name(template)}-virtualsystems",
        )
        for template in templates
    }

    # Zones per VirtualSystem — submitted as vsys results arrive
    zone_futures = []
    for template, vsys_future in vsys_futures.items():
        vsys_data = vsys_future.result()
        vsys_list = [e["@name"] for e in get_entries(vsys_data)]
        tname = safe_name(template)
        tparam = urllib.parse.quote(template)
        print(f"  Template {template}: {len(vsys_list)} virtual system(s)")
        for vsys in vsys_list:
            zone_futures.append(
                fetch_and_save.with_options(name=f"fetch-zones-{tname}-{safe_name(vsys)}").submit(
                    f"{base_url}/Network/Zones"
                    f"?location=template&template={tparam}&vsys={urllib.parse.quote(vsys)}",
                    api_key, output_dir, timestamp,
                    f"template-{tname}-vsys-{safe_name(vsys)}-zones",
                )
            )

    # Objects — shared + per device group, all submitted in parallel
    object_futures = []
    for _label, endpoint in OBJECT_TYPES:
        object_futures.append(
            fetch_and_save.with_options(name=f"fetch-shared-{endpoint.lower()}").submit(
                f"{base_url}/Objects/{endpoint}?location=shared",
                api_key, output_dir, timestamp, f"shared-{endpoint.lower()}",
            )
        )
        for dg in device_groups:
            dgname = safe_name(dg)
            object_futures.append(
                fetch_and_save.with_options(name=f"fetch-dg-{dgname}-{endpoint.lower()}").submit(
                    f"{base_url}/Objects/{endpoint}"
                    f"?location=device-group&device-group={urllib.parse.quote(dg)}",
                    api_key, output_dir, timestamp, f"dg-{dgname}-{endpoint.lower()}",
                )
            )

    # SecurityPreRules — shared + per device group, all submitted in parallel
    rules_futures = [
        fetch_and_save.with_options(name="fetch-shared-security-pre-rules").submit(
            f"{base_url}/Policies/SecurityPreRules?location=shared",
            api_key, output_dir, timestamp, "shared-security-pre-rules",
        )
    ]
    for dg in device_groups:
        dgname = safe_name(dg)
        rules_futures.append(
            fetch_and_save.with_options(name=f"fetch-dg-{dgname}-security-pre-rules").submit(
                f"{base_url}/Policies/SecurityPreRules"
                f"?location=device-group&device-group={urllib.parse.quote(dg)}",
                api_key, output_dir, timestamp, f"dg-{dgname}-security-pre-rules",
            )
        )

    for f in zone_futures + object_futures + rules_futures:
        f.result()

    print(f"\nBackup complete. Files saved to: {output_dir}")


def main():
    parser = argparse.ArgumentParser(description="Backup Palo Alto Panorama configurations.")
    parser.add_argument("hostname", help="Panorama API hostname")
    parser.add_argument("--key", help="Panorama API key (defaults to PANORAMA_KEY env var)")
    args = parser.parse_args()

    api_key = args.key or os.environ.get("PANORAMA_KEY")
    if not api_key:
        print("Error: No API key provided. Use --key or set the PANORAMA_KEY environment variable.")
        sys.exit(1)

    base_url = f"https://{args.hostname}/restapi/v11.1"
    timestamp = datetime.now().strftime("%Y%m%d-%H%M")

    output_dir = os.path.join("output", timestamp)
    os.makedirs(output_dir, exist_ok=True)
    print(f"Output directory: {output_dir}")

    # First API call triggers the MFA session — done outside the flow so we can
    # pause for the user to complete MFA before any further requests are made.
    print("\nConnecting to Panorama API...")
    try:
        device_groups_data = make_request(f"{base_url}/Panorama/DeviceGroups", api_key)
    except RuntimeError:
        sys.exit(1)

    save_json(device_groups_data, output_dir, timestamp, "device-groups")

    input("\nPlease complete multi-factor authentication, then press Enter to continue...")
    print()

    try:
        backup_flow(base_url, api_key, output_dir, timestamp, device_groups_data)
    except Exception:
        sys.exit(1)


if __name__ == "__main__":
    main()
