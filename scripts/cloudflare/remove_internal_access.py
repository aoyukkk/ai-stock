from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.cloudflare.bootstrap_internal_access import APP_NAME, _ingress_is_exact, _rollback_plan
from scripts.cloudflare.common import CloudflareApiError, CloudflareClient, DeploymentInput, emit, safe_result


def main() -> int:
    parser = argparse.ArgumentParser(description="Remove only AI Trader internal Cloudflare resources.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--verify", action="store_true")
    mode.add_argument("--rollback-plan", action="store_true")
    args = parser.parse_args()
    values = DeploymentInput.from_env()
    if args.rollback_plan:
        emit(_rollback_plan(values))
        return 0
    if not values.token:
        emit(safe_result(status="MANUAL_CONFIGURATION_REQUIRED", hostname=values.hostname))
        return 2
    try:
        values.validate(require_token=True, require_emails=False)
        client = CloudflareClient(values)
        try:
            targets = _targets(client, values)
            if args.dry_run:
                emit(safe_result(status="DRY_RUN", hostname=values.hostname, targets=targets, plan=_rollback_plan(values)["steps"]))
                return 0
            if args.apply:
                _remove(client, values, targets)
                _delete_local_tunnel_token()
            remaining = _targets(client, values)
            empty = not any(remaining[key]["exists"] for key in ("dns", "access_application", "tunnel"))
            emit(safe_result(status="REMOVED" if args.apply and empty else "VERIFIED", hostname=values.hostname, empty=empty, targets=remaining))
            return 0 if empty else 1
        finally:
            client.close()
    except CloudflareApiError as exc:
        emit(safe_result(status="BLOCKED", error=exc.code, http_status=exc.status_code))
        return 3


def _targets(client: CloudflareClient, values: DeploymentInput) -> dict:
    zone = client.zone()
    zone_name = str(zone.get("name", "")).lower()
    if values.hostname == zone_name or not values.hostname.endswith("." + zone_name):
        raise CloudflareApiError("HOSTNAME_MUST_BE_NON_ROOT_ZONE_SUBDOMAIN")
    records = client.dns_records()
    apps = (
        [item for item in client.apps() if str(item.get("domain", "")).lower() == values.hostname]
        if values.uses_access else []
    )
    tunnels = [item for item in client.tunnels() if item.get("name") == values.tunnel_name]
    if len(records) > 1 or len(apps) > 1 or len(tunnels) > 1:
        raise CloudflareApiError("AMBIGUOUS_CLOUDFLARE_RESOURCES")
    app = apps[0] if apps else None
    tunnel = tunnels[0] if tunnels else None
    if app and app.get("name") != APP_NAME:
        raise CloudflareApiError("ACCESS_APP_HOSTNAME_CONFLICT")
    ingress_exact = False
    if tunnel:
        config = client.request("GET", f"/accounts/{values.account_id}/cfd_tunnel/{tunnel['id']}/configurations") or {}
        ingress_exact = _ingress_is_exact(config, values.hostname)
        if not ingress_exact:
            raise CloudflareApiError("TUNNEL_HAS_UNMANAGED_INGRESS")
    expected_target = f"{tunnel['id']}.cfargotunnel.com" if tunnel else None
    if records and expected_target and str(records[0].get("content", "")).lower().rstrip(".") != expected_target.lower():
        raise CloudflareApiError("DNS_TARGET_CONFLICT")
    return {
        "dns": {"exists": bool(records), "id": records[0].get("id") if records else None},
        "access_application": {"exists": bool(app), "id": app.get("id") if app else None},
        "tunnel": {"exists": bool(tunnel), "id": tunnel.get("id") if tunnel else None, "ingress_exact": ingress_exact},
    }


def _remove(client: CloudflareClient, values: DeploymentInput, targets: dict) -> None:
    if targets["dns"]["exists"]:
        client.request("DELETE", f"/zones/{values.zone_id}/dns_records/{targets['dns']['id']}")
    if targets["access_application"]["exists"]:
        client.request("DELETE", f"/accounts/{values.account_id}/access/apps/{targets['access_application']['id']}")
    if targets["tunnel"]["exists"]:
        client.request("DELETE", f"/accounts/{values.account_id}/cfd_tunnel/{targets['tunnel']['id']}")


def _delete_local_tunnel_token() -> None:
    default = Path(os.getenv("PROGRAMDATA", r"C:\ProgramData")) / "AITraderAssistant" / "secrets" / "cloudflared-token.txt"
    Path(os.getenv("CLOUDFLARE_TUNNEL_TOKEN_FILE") or default).unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
