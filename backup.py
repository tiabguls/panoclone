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
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Connection error for {url}: {e.reason}")
        sys.exit(1)


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

    print("\nConnecting to Panorama API...")
    device_groups_data = make_request(f"{base_url}/Panorama/DeviceGroups", api_key)
    save_json(device_groups_data, output_dir, timestamp, "device-groups")

    input("\nPlease complete multi-factor authentication, then press Enter to continue...")
    print()

    device_groups = [e["@name"] for e in get_entries(device_groups_data)]
    print(f"Found {len(device_groups)} device group(s): {', '.join(device_groups)}")

    # Templates
    print("\nRetrieving Templates...")
    templates_data = make_request(f"{base_url}/Panorama/Templates", api_key)
    save_json(templates_data, output_dir, timestamp, "templates")
    templates = [e["@name"] for e in get_entries(templates_data)]
    print(f"Found {len(templates)} template(s): {', '.join(templates)}")

    # Template Stacks
    print("\nRetrieving Template Stacks...")
    stacks_data = make_request(f"{base_url}/Panorama/TemplateStacks", api_key)
    save_json(stacks_data, output_dir, timestamp, "template-stacks")

    # VirtualSystems and Zones per Template
    for template in templates:
        tname = safe_name(template)
        tparam = urllib.parse.quote(template)

        print(f"\nRetrieving VirtualSystems for template: {template}...")
        vsys_data = make_request(
            f"{base_url}/Device/VirtualSystems?location=template&template={tparam}",
            api_key,
        )
        save_json(vsys_data, output_dir, timestamp, f"template-{tname}-virtualsystems")
        vsys_list = [e["@name"] for e in get_entries(vsys_data)]
        print(f"  Found {len(vsys_list)} virtual system(s): {', '.join(vsys_list)}")

        for vsys in vsys_list:
            print(f"  Retrieving Network Zones for vsys: {vsys}...")
            zones_data = make_request(
                f"{base_url}/Network/Zones"
                f"?location=template&template={tparam}&vsys={urllib.parse.quote(vsys)}",
                api_key,
            )
            save_json(zones_data, output_dir, timestamp, f"template-{tname}-vsys-{safe_name(vsys)}-zones")

    # Objects — shared
    print("\nRetrieving shared objects...")
    for _, endpoint in OBJECT_TYPES:
        print(f"  Retrieving shared {endpoint}...")
        data = make_request(f"{base_url}/Objects/{endpoint}?location=shared", api_key)
        save_json(data, output_dir, timestamp, f"shared-{endpoint.lower()}")

    # Objects — per device group
    for dg in device_groups:
        dgname = safe_name(dg)
        dgparam = urllib.parse.quote(dg)
        print(f"\nRetrieving objects for device group: {dg}...")
        for _, endpoint in OBJECT_TYPES:
            print(f"  Retrieving {endpoint}...")
            data = make_request(
                f"{base_url}/Objects/{endpoint}?location=device-group&device-group={dgparam}",
                api_key,
            )
            save_json(data, output_dir, timestamp, f"dg-{dgname}-{endpoint.lower()}")

    # SecurityPreRules
    print("\nRetrieving shared SecurityPreRules...")
    shared_rules = make_request(f"{base_url}/Policies/SecurityPreRules?location=shared", api_key)
    save_json(shared_rules, output_dir, timestamp, "shared-security-pre-rules")

    for dg in device_groups:
        dgname = safe_name(dg)
        print(f"\nRetrieving SecurityPreRules for device group: {dg}...")
        rules_data = make_request(
            f"{base_url}/Policies/SecurityPreRules"
            f"?location=device-group&device-group={urllib.parse.quote(dg)}",
            api_key,
        )
        save_json(rules_data, output_dir, timestamp, f"dg-{dgname}-security-pre-rules")

    print(f"\nBackup complete. Files saved to: {output_dir}")


if __name__ == "__main__":
    main()
