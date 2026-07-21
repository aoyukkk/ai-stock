from __future__ import annotations

import argparse
import base64
import os
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.cloudflare.common import (
    CloudflareApiError,
    CloudflareClient,
    DeploymentInput,
    emit,
    safe_result,
    write_local_state,
)


APP_NAME = "AI Trader Internal"
POLICY_NAME = "Allow four internal users"
REQUIRED_PERMISSIONS = [
    "Cloudflare Tunnel Edit",
    "DNS Edit",
    "Access: Apps and Policies Write",
    "Access: Organizations, Identity Providers, and Groups Write",
]
SHARED_PASSWORD_PERMISSIONS = ["Cloudflare Tunnel Edit", "DNS Edit", "Zone Read"]


def _required_permissions(values: DeploymentInput) -> list[str]:
    return REQUIRED_PERMISSIONS if values.uses_access else SHARED_PASSWORD_PERMISSIONS


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap Cloudflare Tunnel and exact-user Access policy.")
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
        emit(_manual_configuration(values))
        return 2
    try:
        values.validate(require_token=True, require_emails=values.uses_access and (args.apply or args.verify))
        client = CloudflareClient(values)
        try:
            result = apply(client, values) if args.apply else inspect(client, values)
            if args.verify:
                result["status"] = "VERIFIED" if result["ready"] else "VERIFICATION_FAILED"
            elif args.dry_run:
                result["status"] = "DRY_RUN"
            emit(result)
            return 0 if (args.apply or not args.verify or result.get("ready")) else 1
        finally:
            client.close()
    except CloudflareApiError as exc:
        emit(safe_result(
            status="BLOCKED",
            error=exc.code,
            http_status=exc.status_code,
            required_permissions=_required_permissions(values),
            allowed_user_count=len(values.emails),
        ))
        return 3


def inspect(client: CloudflareClient, values: DeploymentInput) -> dict[str, Any]:
    if not values.uses_access:
        return _inspect_shared_password(client, values)
    zone = client.zone()
    organization = client.organization()
    zone_name = str(zone.get("name", "")).lower()
    if values.hostname == zone_name or not values.hostname.endswith("." + zone_name):
        raise CloudflareApiError("HOSTNAME_MUST_BE_NON_ROOT_ZONE_SUBDOMAIN")

    tunnels = client.tunnels()
    tunnel = next((item for item in tunnels if item.get("name") == values.tunnel_name), None)
    apps = client.apps()
    app = next((item for item in apps if str(item.get("domain", "")).lower() == values.hostname), None)
    providers = client.identity_providers()
    otp = _otp_provider(providers)
    records = client.dns_records()
    policies = _policies(client, values, app)
    tunnel_config = _tunnel_config(client, values, tunnel)

    expected_emails = set(values.emails)
    exact_policy = next((item for item in policies if _policy_is_exact(item, expected_emails)), None)
    unsafe_policies = [item for item in policies if not _policy_is_exact(item, expected_emails)]
    expected_target = f"{tunnel.get('id')}.cfargotunnel.com" if tunnel else None
    dns_exact = len(records) == 1 and _dns_is_exact(records[0], expected_target)
    app_exact = _app_is_exact(app, values, otp)
    ingress_exact = _ingress_is_exact(tunnel_config, values.hostname)
    tunnel_healthy = bool(tunnel and str(tunnel.get("status", "")).lower() == "healthy")
    policy_exact = bool(exact_policy and not unsafe_policies and len(expected_emails) == 4)
    configured = bool(tunnel and app_exact and otp and dns_exact and ingress_exact and policy_exact)

    return safe_result(
        ready=configured and tunnel_healthy,
        configured=configured,
        account_id_status="CONFIGURED",
        zone_id_status="CONFIGURED",
        zone_name=zone_name,
        team_domain=organization.get("auth_domain"),
        hostname=values.hostname,
        tunnel={
            "exists": bool(tunnel),
            "id": tunnel.get("id") if tunnel else None,
            "status": tunnel.get("status") if tunnel else None,
            "healthy": tunnel_healthy,
            "ingress_exact": ingress_exact,
        },
        dns={
            "exists": bool(records),
            "exact": dns_exact,
            "record_ids": [item.get("id") for item in records],
        },
        access_application={
            "exists": bool(app),
            "id": app.get("id") if app else None,
            "aud": app.get("aud") if app else None,
            "exact": app_exact,
        },
        otp_provider={"exists": bool(otp), "id": otp.get("id") if otp else None},
        policy={
            "exists": bool(exact_policy),
            "id": exact_policy.get("id") if exact_policy else None,
            "exact": policy_exact,
            "unsafe_policy_count": len(unsafe_policies),
            "bypass": any(item.get("decision") == "bypass" for item in policies),
        },
        allowed_user_count=len(values.emails),
        required_permissions=REQUIRED_PERMISSIONS,
        planned_changes=_planned_changes(tunnel, tunnel_healthy, ingress_exact, app, app_exact, otp, policy_exact, records, dns_exact),
    )


def apply(client: CloudflareClient, values: DeploymentInput) -> dict[str, Any]:
    if not values.uses_access:
        return _apply_shared_password(client, values)
    values.validate(require_token=True, require_emails=True)
    zone = client.zone()
    client.organization()
    zone_name = str(zone.get("name", "")).lower()
    if values.hostname == zone_name or not values.hostname.endswith("." + zone_name):
        raise CloudflareApiError("HOSTNAME_MUST_BE_NON_ROOT_ZONE_SUBDOMAIN")

    existing_records = client.dns_records()
    existing_providers = client.identity_providers()
    otp = _otp_provider(existing_providers)
    existing_apps = client.apps()
    existing_app = next((item for item in existing_apps if str(item.get("domain", "")).lower() == values.hostname), None)
    existing_policies = _policies(client, values, existing_app)
    exact_existing_policy = [item for item in existing_policies if _policy_is_exact(item, set(values.emails))]
    if existing_records and not (
        _app_is_exact(existing_app, values, otp)
        and len(exact_existing_policy) == 1
        and len(existing_policies) == 1
    ):
        raise CloudflareApiError("PREEXISTING_DNS_WITHOUT_EXACT_ACCESS")

    if otp is None:
        otp = client.request(
            "POST",
            f"/accounts/{values.account_id}/access/identity_providers",
            json_body={"name": "One-time PIN", "type": "onetimepin", "config": {}},
        )

    apps = existing_apps
    app = next((item for item in apps if str(item.get("domain", "")).lower() == values.hostname), None)
    if app is not None and app.get("name") != APP_NAME:
        raise CloudflareApiError("ACCESS_APP_HOSTNAME_CONFLICT")
    app_body = {
        "name": APP_NAME,
        "type": "self_hosted",
        "domain": values.hostname,
        "session_duration": f"{values.session_hours}h",
        "auto_redirect_to_identity": True,
        "allowed_idps": [otp["id"]],
        "app_launcher_visible": True,
        "allow_authenticate_via_warp": False,
    }
    if app is None:
        app = client.request("POST", f"/accounts/{values.account_id}/access/apps", json_body=app_body)
    else:
        app = client.request("PUT", f"/accounts/{values.account_id}/access/apps/{app['id']}", json_body=app_body)

    policy_body = {
        "name": POLICY_NAME,
        "decision": "allow",
        "precedence": 1,
        "include": [{"email": {"email": email}} for email in values.emails],
        "exclude": [],
        "require": [],
    }
    policies = _policies(client, values, app)
    policy = next((item for item in policies if item.get("name") == POLICY_NAME), None)
    if policy is None:
        policy = client.request(
            "POST", f"/accounts/{values.account_id}/access/apps/{app['id']}/policies", json_body=policy_body
        )
    else:
        policy = client.request(
            "PUT",
            f"/accounts/{values.account_id}/access/apps/{app['id']}/policies/{policy['id']}",
            json_body=policy_body,
        )
    for extra in policies:
        if extra.get("id") != policy.get("id"):
            client.request(
                "DELETE", f"/accounts/{values.account_id}/access/apps/{app['id']}/policies/{extra['id']}"
            )

    tunnels = client.tunnels()
    tunnel = next((item for item in tunnels if item.get("name") == values.tunnel_name), None)
    if tunnel is None:
        tunnel = client.request("POST", f"/accounts/{values.account_id}/cfd_tunnel", json_body={
            "name": values.tunnel_name,
            "config_src": "cloudflare",
            "tunnel_secret": base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
        })
    client.request(
        "PUT",
        f"/accounts/{values.account_id}/cfd_tunnel/{tunnel['id']}/configurations",
        json_body={"config": {"ingress": [
            {
                "hostname": values.hostname,
                "service": "http://127.0.0.1:8080",
                "originRequest": {"connectTimeout": 10},
            },
            {"service": "http_status:404"},
        ]}},
    )

    records = client.dns_records()
    expected_target = f"{tunnel['id']}.cfargotunnel.com"
    if len(records) > 1:
        raise CloudflareApiError("MULTIPLE_DNS_RECORDS_FOR_HOSTNAME")
    dns_body = {"type": "CNAME", "name": values.hostname, "content": expected_target, "proxied": True}
    if not records:
        dns = client.request("POST", f"/zones/{values.zone_id}/dns_records", json_body=dns_body)
    elif not _dns_is_exact(records[0], expected_target):
        dns = client.request("PUT", f"/zones/{values.zone_id}/dns_records/{records[0]['id']}", json_body=dns_body)
    else:
        dns = records[0]

    token_file = _write_tunnel_token(client, values, str(tunnel["id"]))
    result = inspect(client, values)
    result.update(safe_result(
        status="APPLIED" if result["ready"] else "APPLIED_PENDING_TUNNEL_SERVICE",
        access_application_id=app.get("id"),
        access_aud=app.get("aud"),
        policy_id=policy.get("id"),
        tunnel_id=tunnel.get("id"),
        dns_record_id=dns.get("id"),
        tunnel_token_file_status="CREATED",
        tunnel_token_file=str(token_file),
    ))
    write_local_state(result)
    return result


def _inspect_shared_password(client: CloudflareClient, values: DeploymentInput) -> dict[str, Any]:
    values.validate(require_token=True, require_emails=False)
    zone = client.zone()
    zone_name = str(zone.get("name", "")).lower()
    if values.hostname == zone_name or not values.hostname.endswith("." + zone_name):
        raise CloudflareApiError("HOSTNAME_MUST_BE_NON_ROOT_ZONE_SUBDOMAIN")
    tunnels = client.tunnels()
    tunnel = next((item for item in tunnels if item.get("name") == values.tunnel_name), None)
    records = client.dns_records()
    config = _tunnel_config(client, values, tunnel)
    expected_target = f"{tunnel.get('id')}.cfargotunnel.com" if tunnel else None
    dns_exact = len(records) == 1 and _dns_is_exact(records[0], expected_target)
    ingress_exact = _ingress_is_exact(config, values.hostname)
    tunnel_healthy = bool(tunnel and str(tunnel.get("status", "")).lower() == "healthy")
    configured = bool(tunnel and dns_exact and ingress_exact)
    return safe_result(
        ready=configured and tunnel_healthy,
        configured=configured,
        authentication_mode="LOCAL_SHARED_PASSWORD",
        account_id_status="CONFIGURED",
        zone_id_status="CONFIGURED",
        zone_name=zone_name,
        hostname=values.hostname,
        tunnel={
            "exists": bool(tunnel), "id": tunnel.get("id") if tunnel else None,
            "status": tunnel.get("status") if tunnel else None,
            "healthy": tunnel_healthy, "ingress_exact": ingress_exact,
        },
        dns={"exists": bool(records), "exact": dns_exact, "record_ids": [item.get("id") for item in records]},
        access_application={"required": False, "exists": False},
        allowed_user_count=0,
        required_permissions=SHARED_PASSWORD_PERMISSIONS,
        planned_changes=[name for name, ready in (
            ("tunnel", bool(tunnel)), ("tunnel_ingress", ingress_exact), ("dns", bool(records and dns_exact))
        ) if not ready],
    )


def _apply_shared_password(client: CloudflareClient, values: DeploymentInput) -> dict[str, Any]:
    values.validate(require_token=True, require_emails=False)
    zone = client.zone()
    zone_name = str(zone.get("name", "")).lower()
    if values.hostname == zone_name or not values.hostname.endswith("." + zone_name):
        raise CloudflareApiError("HOSTNAME_MUST_BE_NON_ROOT_ZONE_SUBDOMAIN")
    tunnels = client.tunnels()
    tunnel = next((item for item in tunnels if item.get("name") == values.tunnel_name), None)
    records = client.dns_records()
    if len(records) > 1:
        raise CloudflareApiError("MULTIPLE_DNS_RECORDS_FOR_HOSTNAME")
    if records and (tunnel is None or not _dns_is_exact(records[0], f"{tunnel['id']}.cfargotunnel.com")):
        raise CloudflareApiError("PREEXISTING_DNS_CONFLICT")
    if tunnel is None:
        tunnel = client.request("POST", f"/accounts/{values.account_id}/cfd_tunnel", json_body={
            "name": values.tunnel_name,
            "config_src": "cloudflare",
            "tunnel_secret": base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
        })
    client.request(
        "PUT", f"/accounts/{values.account_id}/cfd_tunnel/{tunnel['id']}/configurations",
        json_body={"config": {"ingress": [
            {"hostname": values.hostname, "service": "http://127.0.0.1:8080", "originRequest": {"connectTimeout": 10}},
            {"service": "http_status:404"},
        ]}},
    )
    expected_target = f"{tunnel['id']}.cfargotunnel.com"
    if not records:
        dns = client.request("POST", f"/zones/{values.zone_id}/dns_records", json_body={
            "type": "CNAME", "name": values.hostname, "content": expected_target, "proxied": True,
        })
    else:
        dns = records[0]
    token_file = _write_tunnel_token(client, values, str(tunnel["id"]))
    result = _inspect_shared_password(client, values)
    result.update(safe_result(
        status="APPLIED" if result["ready"] else "APPLIED_PENDING_TUNNEL_SERVICE",
        tunnel_id=tunnel.get("id"), dns_record_id=dns.get("id"),
        tunnel_token_file_status="CREATED", tunnel_token_file=str(token_file),
    ))
    write_local_state(result)
    return result


def _policies(client: CloudflareClient, values: DeploymentInput, app: dict | None) -> list[dict]:
    if not app:
        return []
    return list(client.request(
        "GET", f"/accounts/{values.account_id}/access/apps/{app['id']}/policies", params={"per_page": 100}
    ) or [])


def _tunnel_config(client: CloudflareClient, values: DeploymentInput, tunnel: dict | None) -> dict:
    if not tunnel:
        return {}
    return client.request("GET", f"/accounts/{values.account_id}/cfd_tunnel/{tunnel['id']}/configurations") or {}


def _otp_provider(providers: list[dict]) -> dict | None:
    return next((item for item in providers if str(item.get("type", "")).lower() in {"onetimepin", "onetime_pin"}), None)


def _policy_emails(policy: dict) -> set[str] | None:
    emails: set[str] = set()
    include = policy.get("include") or []
    for rule in include:
        if set(rule) != {"email"} or set(rule["email"]) != {"email"}:
            return None
        emails.add(str(rule["email"]["email"]).strip().lower())
    return emails


def _policy_is_exact(policy: dict, expected: set[str]) -> bool:
    return bool(
        len(expected) == 4
        and policy.get("name") == POLICY_NAME
        and policy.get("decision") == "allow"
        and not (policy.get("exclude") or [])
        and not (policy.get("require") or [])
        and _policy_emails(policy) == expected
    )


def _app_is_exact(app: dict | None, values: DeploymentInput, otp: dict | None) -> bool:
    return bool(
        app
        and otp
        and app.get("name") == APP_NAME
        and app.get("type") == "self_hosted"
        and str(app.get("domain", "")).lower() == values.hostname
        and app.get("session_duration") == f"{values.session_hours}h"
        and app.get("auto_redirect_to_identity") is True
        and app.get("allowed_idps") == [otp.get("id")]
        and app.get("allow_authenticate_via_warp") is False
        and bool(app.get("aud"))
    )


def _dns_is_exact(record: dict, expected_target: str | None) -> bool:
    return bool(
        expected_target
        and record.get("type") == "CNAME"
        and str(record.get("content", "")).lower().rstrip(".") == expected_target.lower().rstrip(".")
        and record.get("proxied") is True
    )


def _ingress_is_exact(payload: dict, hostname: str) -> bool:
    ingress = (payload.get("config") or {}).get("ingress") or []
    return ingress == [
        {
            "hostname": hostname,
            "service": "http://127.0.0.1:8080",
            "originRequest": {"connectTimeout": 10},
        },
        {"service": "http_status:404"},
    ]


def _planned_changes(tunnel, healthy, ingress, app, app_exact, otp, policy, records, dns) -> list[str]:
    changes = []
    for name, ready in (
        ("otp_provider", bool(otp)),
        ("access_application", bool(app and app_exact)),
        ("exact_four_user_policy", policy),
        ("tunnel", bool(tunnel)),
        ("tunnel_ingress", ingress),
        ("dns", bool(records and dns)),
        ("cloudflared_service_connection", healthy),
    ):
        if not ready:
            changes.append(name)
    return changes


def _write_tunnel_token(client: CloudflareClient, values: DeploymentInput, tunnel_id: str) -> Path:
    token = client.request("GET", f"/accounts/{values.account_id}/cfd_tunnel/{tunnel_id}/token")
    if not isinstance(token, str) or len(token) < 32:
        raise CloudflareApiError("TUNNEL_TOKEN_RESPONSE_INVALID")
    default_root = Path(os.getenv("PROGRAMDATA", r"C:\ProgramData")) / "AITraderAssistant"
    path = Path(os.getenv("CLOUDFLARE_TUNNEL_TOKEN_FILE") or default_root / "secrets" / "cloudflared-token.txt")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(token, encoding="ascii")
    os.replace(temporary, path)
    if os.name == "nt":
        completed = subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", "SYSTEM:F", "Administrators:F"],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            path.unlink(missing_ok=True)
            raise CloudflareApiError("TUNNEL_TOKEN_ACL_FAILED")
    return path


def _manual_configuration(values: DeploymentInput) -> dict[str, Any]:
    access_steps = [
        "Create One-time PIN identity provider",
        "Create self-hosted Access application with one exact four-email allow policy and no bypass",
    ] if values.uses_access else ["Use the application's local shared-password authentication"]
    return safe_result(
        status="MANUAL_CONFIGURATION_REQUIRED",
        hostname=values.hostname,
        allowed_user_count=len(values.emails),
        required_permissions=_required_permissions(values),
        steps=[
            "Create or select the named remotely-managed tunnel",
            "Configure exact hostname ingress to http://127.0.0.1:8080 and final 404 rule",
            *access_steps,
            "Create proxied CNAME to the tunnel UUID",
            "Install cloudflared as a Windows service using a protected token file",
        ],
    )


def _rollback_plan(values: DeploymentInput) -> dict[str, Any]:
    access_step = ["Delete the AI Trader Internal Access application and exact-user policy"] if values.uses_access else []
    return safe_result(status="ROLLBACK_PLAN", hostname=values.hostname, steps=[
        "Stop cloudflared Windows service",
        "Delete the exact-hostname DNS CNAME",
        *access_step,
        "Delete the named tunnel after confirming no other hostname uses it",
        "Stop and uninstall AI Trader Internal Web Server",
        "Delete the protected cloudflared token file",
        "Preserve ProgramData data, outputs, backups and application secrets",
    ])


if __name__ == "__main__":
    raise SystemExit(main())
