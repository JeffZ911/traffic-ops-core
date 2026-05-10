"""End-to-end Cloudflare Pages deployment for ntecodex-site.

Steps:
  1. Verify token + account
  2. Confirm the ntecodex.com zone exists in this account
  3. Create the `ntecodex` Pages project (direct upload mode) if missing
  4. Build the site (npm run build) — yields dist/
  5. Deploy dist/ via wrangler pages deploy
  6. Attach the custom domain ntecodex.com (apex) and www subdomain
  7. Add DNS records pointing to <project>.pages.dev (CNAME @ + www)
  8. Poll the latest deployment until success / failure

Token must have these permissions:
  Account: Cloudflare Pages:Edit
  Zone: DNS:Edit, SSL and Certificates:Edit
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parent.parent / ".env")

CF_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN") or ""
CF_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID") or ""

PROJECT_NAME = "ntecodex"
SITE_DOMAIN = "ntecodex.com"
SITE_REPO = (
    Path(__file__).resolve().parent.parent.parent / "ntecodex-site"
).resolve()

API = "https://api.cloudflare.com/client/v4"
HEADERS = {"Authorization": f"Bearer {CF_TOKEN}"}


def _api(method: str, path: str, **kwargs) -> httpx.Response:
    url = path if path.startswith("http") else f"{API}{path}"
    return httpx.request(method, url, headers=HEADERS, timeout=30, **kwargs)


def _ok(r: httpx.Response) -> dict:
    j = r.json()
    if not j.get("success"):
        raise RuntimeError(f"{r.status_code} {r.url} → {j}")
    return j


def step(label: str) -> None:
    print(f"\n=== {label} ===")


def main() -> int:
    if not CF_TOKEN or not CF_ACCOUNT_ID:
        print("❌ CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID missing.")
        return 2

    # ------------------------------------------------------ 1. Verify
    step("1. Verify token + account")
    # The cfat_-prefixed token cannot hit /user/tokens/verify, so probe a
    # permission-bearing endpoint instead.
    r = _api("GET", f"/accounts/{CF_ACCOUNT_ID}/pages/projects")
    if r.status_code == 403 or not r.json().get("success"):
        print("   ❌ Pages API not accessible. Token needs "
              "'Account > Cloudflare Pages > Edit'.")
        return 1
    print(f"   ✓ Pages API reachable; existing projects: "
          f"{len(r.json().get('result') or [])}")

    # ------------------------------------------------------ 2. Locate zone
    step(f"2. Locate {SITE_DOMAIN} zone")
    r = _api("GET", "/zones", params={"name": SITE_DOMAIN})
    j = _ok(r)
    if not j["result"]:
        print(f"   ❌ zone {SITE_DOMAIN} not found in this account.")
        print(f"   → Add ntecodex.com to Cloudflare first (Add Site flow).")
        return 3
    zone = j["result"][0]
    zone_id = zone["id"]
    print(f"   zone_id={zone_id[:8]}…  status={zone['status']}")
    if zone["status"] != "active":
        print(f"   ⚠️  zone not active yet (status={zone['status']}). "
              f"Continue but DNS propagation may lag.")

    # --------------------------------------- 3. Find or create Pages project
    step(f"3. Pages project '{PROJECT_NAME}'")
    r = _api("GET", f"/accounts/{CF_ACCOUNT_ID}/pages/projects/{PROJECT_NAME}")
    if r.status_code == 200 and r.json().get("success"):
        proj = r.json()["result"]
        print(f"   ✓ already exists  subdomain={proj.get('subdomain')}")
    else:
        r = _api(
            "POST",
            f"/accounts/{CF_ACCOUNT_ID}/pages/projects",
            json={
                "name": PROJECT_NAME,
                "production_branch": "main",
            },
        )
        j = _ok(r)
        proj = j["result"]
        print(f"   ✓ created  subdomain={proj.get('subdomain')}")

    pages_subdomain = proj.get("subdomain") or f"{PROJECT_NAME}.pages.dev"

    # --------------------------------------- 4. Build site
    step("4. Build (npm run build)")
    if not (SITE_REPO / "node_modules").exists():
        print("   installing deps…")
        subprocess.run(["npm", "install"], cwd=SITE_REPO, check=True)
    subprocess.run(["npm", "run", "build"], cwd=SITE_REPO, check=True)
    dist = SITE_REPO / "dist"
    if not dist.exists():
        print("   ❌ dist/ not produced")
        return 4
    file_count = sum(1 for _ in dist.rglob("*") if _.is_file())
    print(f"   dist/: {file_count} files")

    # --------------------------------------- 5. Deploy via wrangler
    step("5. Deploy to Pages")
    env = {
        **os.environ,
        "CLOUDFLARE_API_TOKEN": CF_TOKEN,
        "CLOUDFLARE_ACCOUNT_ID": CF_ACCOUNT_ID,
    }
    cmd = [
        "npx", "--yes", "wrangler@latest",
        "pages", "deploy", "dist",
        "--project-name", PROJECT_NAME,
        "--branch", "main",
        "--commit-dirty=true",
    ]
    print(f"   $ {shlex.join(cmd)}")
    res = subprocess.run(
        cmd, cwd=SITE_REPO, env=env, capture_output=True, text=True
    )
    print(res.stdout[-1500:])
    if res.returncode != 0:
        print(res.stderr[-1500:])
        print("   ❌ wrangler deploy failed")
        return 5

    # ------------------------- 6. Custom domain attach (apex + www)
    step(f"6. Attach custom domain {SITE_DOMAIN} + www.{SITE_DOMAIN}")
    for d in (SITE_DOMAIN, f"www.{SITE_DOMAIN}"):
        r = _api(
            "POST",
            f"/accounts/{CF_ACCOUNT_ID}/pages/projects/{PROJECT_NAME}/domains",
            json={"name": d},
        )
        if r.status_code in (200, 201) and r.json().get("success"):
            print(f"   ✓ added domain {d}")
        else:
            j = r.json()
            errs = j.get("errors", [])
            # 8000007 / "domain already exists" is fine
            if any("already" in str(e).lower() for e in errs):
                print(f"   ↪︎ {d} already attached")
            else:
                print(f"   ⚠️  {d}: {errs}")

    # ------------------------- 7. DNS records
    step("7. DNS records (CNAME → pages.dev)")
    target = pages_subdomain
    for name, kind in [(SITE_DOMAIN, "@"), (f"www.{SITE_DOMAIN}", "www")]:
        # Look up existing record
        r = _api(
            "GET",
            f"/zones/{zone_id}/dns_records",
            params={"name": name, "type": "CNAME"},
        )
        existing = (r.json().get("result") or [])
        if existing:
            rec = existing[0]
            if rec["content"] == target and rec.get("proxied", False):
                print(f"   ↪︎ {kind}: CNAME → {target} already correct")
                continue
            r = _api(
                "PUT",
                f"/zones/{zone_id}/dns_records/{rec['id']}",
                json={"type": "CNAME", "name": name, "content": target,
                      "proxied": True, "ttl": 1},
            )
            print(f"   ✓ updated {kind}: CNAME → {target}")
        else:
            r = _api(
                "POST",
                f"/zones/{zone_id}/dns_records",
                json={"type": "CNAME", "name": name, "content": target,
                      "proxied": True, "ttl": 1},
            )
            if r.json().get("success"):
                print(f"   ✓ created {kind}: CNAME → {target}")
            else:
                print(f"   ⚠️  {kind}: {r.json().get('errors')}")

    # ------------------------- 8. Poll latest deployment
    step("8. Poll latest deployment")
    for _ in range(40):  # ~4 min @ 6s
        r = _api(
            "GET",
            f"/accounts/{CF_ACCOUNT_ID}/pages/projects/{PROJECT_NAME}/deployments",
        )
        deploys = r.json().get("result") or []
        if not deploys:
            time.sleep(3)
            continue
        latest = deploys[0]
        stage = latest.get("latest_stage", {})
        stage_name = stage.get("name", "?")
        stage_status = stage.get("status", "?")
        env_url = latest.get("url")
        print(f"   stage={stage_name} status={stage_status} url={env_url}")
        if stage_name == "deploy" and stage_status == "success":
            print(f"\n✅ Deployed: {env_url}")
            print(f"   custom: https://{SITE_DOMAIN}")
            return 0
        if stage_status == "failure":
            print("\n❌ Deploy failed at stage:", stage_name)
            return 6
        time.sleep(6)

    print("\n⚠️  Deploy still running after 4 minutes — check the dashboard.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
