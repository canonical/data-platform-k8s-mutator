#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
"""
Generate CA and webhook server certificates and all Kubernetes manifests (namespace,
TLS Secret, MutatingWebhookConfiguration, Deployment, Service)

Library only: call generate(config) with a GenWebhookCertsConfig. CLI is in bootstrap_webhook.
"""

import base64
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


@dataclass
class GenWebhookCertsConfig:
    """Parameters for generating webhook certs and manifests."""

    output_dir: Path = Path("deploy")
    namespace: str = "webhooks"
    service: str = "sysctl-webhook"
    secret_name: str = "sysctl-webhook-tls"
    webhook_config_name: str = "sysctl-webhook"
    validity_days: int = 3650
    image: str = "sysctl-webhook:latest"
    target_namespaces: str = ""
    target_container_names: str = ""
    target_image_substr: str = ""
    target_labels: str = ""
    sysctl_name: str = "net.ipv4.tcp_retries2"
    sysctl_value: str = "5"


def generate(config: GenWebhookCertsConfig) -> int:
    """Generate CA, server cert, and K8s manifests under config.output_dir. Returns 0 on success."""
    out = config.output_dir
    out.mkdir(parents=True, exist_ok=True)

    # SANs for the webhook Service
    san_dns = [
        f"{config.service}.{config.namespace}.svc",
        f"{config.service}.{config.namespace}.svc.cluster.local",
    ]
    san_line = ",".join(f"DNS:{s}" for s in san_dns)

    ca_key = out / "ca.key"
    ca_crt = out / "ca.crt"
    tls_key = out / "tls.key"
    tls_crt = out / "tls.crt"

    # 1. CA key and cert
    if not ca_crt.exists() or not ca_key.exists():
        _run(
            [
                "openssl",
                "genrsa",
                "-out",
                str(ca_key),
                "4096",
            ]
        )
        _run(
            [
                "openssl",
                "req",
                "-x509",
                "-new",
                "-nodes",
                "-key",
                str(ca_key),
                "-sha256",
                "-days",
                str(config.validity_days),
                "-out",
                str(ca_crt),
                "-subj",
                "/CN=webhook-ca",
            ]
        )
        print(f"Generated CA: {ca_crt}", file=sys.stderr)
    else:
        print(f"Using existing CA: {ca_crt}", file=sys.stderr)

    # 2. Server key, CSR, and signed cert
    _run(["openssl", "genrsa", "-out", str(tls_key), "4096"])
    csr_file = out / "tls.csr"
    _run(
        [
            "openssl",
            "req",
            "-new",
            "-key",
            str(tls_key),
            "-subj",
            f"/CN={config.service}.{config.namespace}.svc",
            "-addext",
            f"subjectAltName={san_line}",
            "-out",
            str(csr_file),
        ]
    )
    extfile = out / "tls.ext"
    extfile.write_text(f"subjectAltName={san_line}\nextendedKeyUsage=serverAuth\n")
    _run(
        [
            "openssl",
            "x509",
            "-req",
            "-in",
            str(csr_file),
            "-CA",
            str(ca_crt),
            "-CAkey",
            str(ca_key),
            "-CAcreateserial",
            "-out",
            str(tls_crt),
            "-days",
            str(config.validity_days),
            "-sha256",
            "-extfile",
            str(extfile),
        ]
    )
    csr_file.unlink(missing_ok=True)
    extfile.unlink(missing_ok=True)
    print(f"Generated server cert: {tls_crt}", file=sys.stderr)

    # 3. CA bundle (PEM) for MutatingWebhookConfiguration.clientConfig.caBundle
    ca_pem = ca_crt.read_bytes()
    ca_b64 = base64.b64encode(ca_pem).decode("ascii")

    # 4. Render K8s manifests from Jinja templates
    env = Environment(
        loader=FileSystemLoader(_TEMPLATES_DIR),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )

    tls_crt_b64 = base64.b64encode(tls_crt.read_bytes()).decode("ascii")
    tls_key_b64 = base64.b64encode(tls_key.read_bytes()).decode("ascii")

    # JSON strings for Deployment env (MutatorConfig reads list/dict via pydantic-settings)
    # Convert CLI comma-separated strings into JSON strings for the Deployment env.
    target_container_names_json = json.dumps(
        [n.strip() for n in config.target_container_names.split(",") if n.strip()]
    )
    target_namespaces_json = (
        json.dumps(
            [n.strip() for n in config.target_namespaces.split(",") if n.strip()]
        )
        if config.target_namespaces.strip()
        else "[]"
    )
    # Parse "k1=v1,k2=v2" into a dict, then JSON for TARGET_LABELS.
    target_labels_dict: dict[str, str] = {}
    for part in (config.target_labels or "").strip().split(","):
        part = part.strip()
        if part and "=" in part:
            k, _, v = part.partition("=")
            target_labels_dict[k.strip()] = v.strip()
    target_labels_json = json.dumps(target_labels_dict)

    template_ctx = {
        "namespace": config.namespace,
        "service": config.service,
        "secret_name": config.secret_name,
        "webhook_config_name": config.webhook_config_name,
        "image": config.image,
        "ca_b64": ca_b64,
        "tls_crt_b64": tls_crt_b64,
        "tls_key_b64": tls_key_b64,
        "target_namespaces_json": target_namespaces_json,
        "target_labels_json": target_labels_json,
        "target_container_names_json": target_container_names_json,
        "target_image_substr": config.target_image_substr,
        "sysctl_name": config.sysctl_name,
        "sysctl_value": config.sysctl_value,
    }

    (out / "namespace.yaml").write_text(
        env.get_template("namespace.yaml.j2").render(**template_ctx)
    )
    (out / "secret.yaml").write_text(
        env.get_template("secret.yaml.j2").render(**template_ctx)
    )
    (out / "webhook-config.yaml").write_text(
        env.get_template("webhook-config.yaml.j2").render(**template_ctx)
    )
    (out / "workload.yaml").write_text(
        env.get_template("workload.yaml.j2").render(**template_ctx)
    )

    print(
        f"Wrote {out}/namespace.yaml, secret.yaml, webhook-config.yaml, workload.yaml",
        file=sys.stderr,
    )
    print(f"Apply with: kubectl apply -f {out}/", file=sys.stderr)
    return 0
