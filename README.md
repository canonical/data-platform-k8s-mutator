# Basic k8s mutating webhook handler

This mutating admission webhook injects a pod sysctl into Kubernetes objects at admission time by
returning a JSONPatch that adds/updates `spec.securityContext.sysctls` in the embedded Pod spec.

It targets controllers with a pod template (/spec/template/spec) such as StatefulSet, Deployment, DaemonSet, Job, etc.

By default it ensures:

- `net.ipv4.tcp_retries2=5`

This is useful for workloads that require `tcp_retries2` to be lowered in the pod network namespace
without patching pods after creation. It can be used for any workload easily.

## Prerequisites

To run this webhook successfully you must enable/configure these 3 items in the cluster.

### 1. TLS and CA bundle

The API server calls the webhook over HTTPS and verifies the webhook's server certificate using the CA bundle in `MutatingWebhookConfiguration.clientConfig.caBundle`.

Use the built-in scripts so no cert-manager or manual steps are needed:

- **`scripts/gen_webhook_certs.py`** – generates a CA and server cert (OpenSSL), TLS Secret, MutatingWebhookConfiguration (caBundle) 
    and Deployment + Service and writes under `deploy/` directory by default.
- **`scripts/bootstrap_webhook.py`** – runs the generator then `kubectl apply -f deploy/` for a one-command deploy.

See **Quick start** below. No Python TLS libraries required (script uses OpenSSL).

### 2. Admission webhooks on the API server

- The `MutatingAdmissionWebhook` admission plugin must be enabled on the API server (usually enabled by default).
- The API server must be able to reach the webhook service. The network policies/firewalls must allow it.
- The webhook must serve HTTPS and the `MutatingWebhookConfiguration` must contain a trusted CA bundle.

Check that admission registration is available (works on any cluster where `kubectl` is configured):

```bash
kubectl api-versions | grep admissionregistration
```

You should see `admissionregistration.k8s.io/v1` (and possibly `v1beta1`). If admission webhooks are disabled for your distribution, 
enable the `MutatingAdmissionWebhook` admission plugin via your cluster's API server configuration and restart the API server. 
The exact steps depend on your Kubernetes distribution.

### 3. Kubelet: allow the unsafe sysctl

This webhook only adds the sysctl to the Pod spec. Nodes will still reject pods using unsafe sysctls unless kubelet is configured to allow them.

a. Configure kubelet with `--allowed-unsafe-sysctls=net.ipv4.tcp_retries2` (e.g. in a kubelet config file). 
b. Restart the kubelet so the change takes effect.

## Dependencies

Dependencies are managed with [uv](https://docs.astral.sh/uv/) and `pyproject.toml`. See [CONTRIBUTING.md](CONTRIBUTING.md) for install steps, running tests, and format/lint.

## Quick start

One command generates everything: TLS certs, namespace, TLS Secret, Deployment, Service, and MutatingWebhookConfiguration (with caBundle). 
The user needs to run it once when installing the webhook. Requires openssl and kubectl in PATH. Run all commands from the project root.

1. Build the rock and prepare a Docker image (so the cluster can use it):

```bash
# Install rockcraft from latest/edge for fastapi-framework extension
sudo snap install rockcraft --classic --channel latest/edge
sudo snap install lxd && sudo lxd init --auto

# Pack the OCI image using rockcraft (env var required with fastapi-framework)
ROCKCRAFT_ENABLE_EXPERIMENTAL_EXTENSIONS=true rockcraft pack

# Push the OCI image to your local registry (e.g. MicroK8s registry on port 32000).
# Replace the rock filename with the one produced by rockcraft pack.
sudo skopeo copy \
  oci-archive:./data-platform-k8s-mutator_1.0_amd64.rock \
  docker://localhost:32000/data-platform-k8s-mutator:1.0 \
  --dest-tls-verify=false
```

2. Generate manifests and apply:

The app requires `TARGET_CONTAINER_NAMES` to be non-empty. use `--target-container-names` (comma-separated) so the mutator knows which containers to target.
All other parameters are optional. To only generate manifests but not deploy, --dry-run option is available.

```bash
# Use the image tag you pushed (e.g. localhost:32000/data-platform-k8s-mutator:1.3). Run from project root:
# pass the image and which containers to match
uv run python -m scripts.bootstrap_webhook --image localhost:32000/data-platform-k8s-mutator:1.0 \
  --target-container-names targetapp
```

This will:

1. Generate a CA and a server certificate (SANs for `sysctl-webhook.webhooks.svc`)
2. Write `deploy/` with namespace, TLS Secret, webhook config and Deployment + Service (workload)
3. Run `kubectl apply -f deploy/` for all of them

After generating manifests, deploy Admission Webhook Mutator:

```bash
kubectl apply -f deploy/
```

## High availability (HA)

By default the webhook runs with a single replica. For HA, run multiple replicas so the API server can still reach the webhook if a pod fails or is rescheduled. 
The Service load-balances across pods.

1. Scale the webhook Deployment (after deploy):

```bash
kubectl scale deployment sysctl-webhook -n webhooks --replicas=3
```

Or edit the generated `deploy/workload.yaml` before applying: set `spec.replicas` to `3` (or more) for the webhook Deployment.

2. (Optional) PodDisruptionBudget: avoid all replicas being evicted at once during node drains:

```bash
kubectl apply -f - <<EOF
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: sysctl-webhook-pdb
  namespace: webhooks
spec:
  minAvailable: 1
  selector:
    matchLabels:
      app: sysctl-webhook
EOF
```

3. (Optional) Spread replicas across nodes: add to the webhook Deployment’s `spec.template.spec`:

```yaml
affinity:
  podAntiAffinity:
    preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 100
        podAffinityTerm:
          labelSelector:
            matchLabels:
              app: sysctl-webhook
          topologyKey: kubernetes.io/hostname
```

This prefers placing pods on different nodes.

## Configuration (environment variables)

- `SYSCTL_NAME` (default: `net.ipv4.tcp_retries2`)
- `SYSCTL_VALUE` (default: `5`)
- `TARGET_NAMESPACES` (optional): comma-separated list of namespace names; only mutate objects in one of these. Leave unset or empty to match all namespaces.
- `TARGET_LABELS` (optional): comma-separated key=value pairs; only mutate objects that have these labels (e.g. `app.kubernetes.io/name=myapp,app.kubernetes.io/managed-by=juju`)
- `TARGET_CONTAINER_NAMES` (**required**): comma-separated container names to match (e.g. `myapp`)
- `TARGET_IMAGE_SUBSTR` (optional): only match containers whose image contains this substring; leave unset or empty to match any image
- `REQUIRE_JUJU_MANAGED` (default: `true`): if true, only mutate objects with
  `app.kubernetes.io/managed-by=juju`

If `TARGET_NAMESPACES` and `TARGET_LABELS` are unset, the webhook will mutate all matching admission requests it receives subject to `TARGET_CONTAINER_NAMES` / `TARGET_IMAGE_SUBSTR` / `REQUIRE_JUJU_MANAGED`.


- It only adds `securityContext` when it is missing.
- If `sysctls` already exists, it will append the sysctl if missing, or replace the value if present but different.

## Important

Sysctls are applied by the kubelet at pod creation time. So it will not change sysctls for existing objects. It only applies to newly-created objects.

## Manifests

- **`scripts/bootstrap_webhook`** (single entry point): parses all CLI options, calls gen_webhook_certs.generate() to produce CA, 
    server cert, and manifests in deploy/, then runs kubectl apply -f deploy/ unless --dry-run. Run from project root: `uv run python -m scripts.bootstrap_webhook`.
- **`scripts/gen_webhook_certs`**: library only: provides GenWebhookCertsConfig and generate(config) used by bootstrap.

## Multiple webhooks in the same cluster
Several mutator webhooks (different sysctls or scopes) can be deployed in a single K8s cluster. But, it is needed to give each instance a unique name so their resources do not collide:

- `--webhook-config-name`: name of the MutatingWebhookConfiguration (must be unique cluster-wide).
- `--service`: Deployment and Service name (unique per namespace).
- `--secret-name`: TLS secret name (unique per namespace).
- Optionally use a dedicated `--namespace` per webhook.

Bootstrap only deletes the MutatingWebhookConfiguration with the given `--webhook-config-name`, so other mutators are unaffected.