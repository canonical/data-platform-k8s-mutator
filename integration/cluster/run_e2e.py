#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
"""Apply test Deployment, assert webhook injected sysctl."""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from tenacity import retry, stop_after_attempt, wait_fixed

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger(__name__)

MANIFEST = Path(__file__).resolve().parent / "test_deployment.yaml"


class SysctlNotReadyError(Exception):
    """Raised when the webhook-injected sysctl is not yet present."""


def kubectl(*args: str) -> subprocess.CompletedProcess:
    k = os.environ.get("KUBECTL") or ("kubectl" if shutil.which("kubectl") else "microk8s kubectl")
    cmd = k.split() + list(args)
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def main() -> int:
    r = kubectl("apply", "-f", str(MANIFEST))
    if r.returncode != 0:
        log.error(r.stderr or r.stdout)
        return 1

    @retry(
        stop=stop_after_attempt(30),
        wait=wait_fixed(2),
        reraise=True,
    )
    def _wait_for_sysctl() -> None:
        r = kubectl("get", "deployment", "e2e-test-app", "-n", "default", "-o", "json")
        if r.returncode != 0:
            raise SysctlNotReadyError(r.stderr or r.stdout or "kubectl get failed")
        spec = json.loads(r.stdout).get("spec", {}).get("template", {}).get("spec") or {}
        sysctls = (spec.get("securityContext") or {}).get("sysctls") or []
        if not any(
            s.get("name") == "net.ipv4.tcp_retries2" and str(s.get("value")) == "5"
            for s in sysctls
        ):
            raise SysctlNotReadyError("sysctl not yet present")

    try:
        _wait_for_sysctl()
    except SysctlNotReadyError:
        log.error("Timeout: deployment spec did not get sysctl net.ipv4.tcp_retries2=5")
        r = kubectl("get", "deployment", "e2e-test-app", "-n", "default", "-o", "json")
        if r.returncode == 0:
            spec = json.loads(r.stdout).get("spec", {}).get("template", {}).get("spec") or {}
            log.error(json.dumps(spec, indent=2))
        return 1

    log.info("OK: deployment has sysctl net.ipv4.tcp_retries2=5")
    kubectl("delete", "-f", str(MANIFEST), "--ignore-not-found=true", "--wait=false")
    return 0


if __name__ == "__main__":
    sys.exit(main())
