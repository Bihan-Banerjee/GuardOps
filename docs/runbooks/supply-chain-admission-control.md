# Runbook: Supply Chain Security & Admission Control

**Phase 11 — GuardOps v0.11.0**  
**Path:** `docs/runbooks/supply-chain-admission-control.md`

---

## Overview

Phase 11 adds three layers that together guarantee only the exact, CI-built image
runs in the cluster:

1. **SBOM** — Syft generates a CycloneDX + SPDX bill of materials for every image
   (CI artifact + S3 + signed attestation).
2. **Cosign keyless signing** — CI signs the image **by digest** using the GitHub
   Actions OIDC identity (Sigstore: Fulcio cert + Rekor transparency log).
3. **Kyverno** — admission policies verify the signature (and SBOM attestation)
   against the CI identity before a pod is allowed to run, plus a best-practice
   policy pack.

Nothing here needs a private key or a new CI secret — keyless signing reuses the
`container-scan` job's existing `id-token: write`.

---

## Prerequisites

- A CI run on `main` has completed (the `container-scan` job signs + attests).
- EKS cluster running and `kubectl` pointed at it:
  ```
  aws eks update-kubeconfig --region ap-south-1 --name guardops-prod-cluster
  ```
- `cosign` installed locally for verification (optional but recommended).

---

## Step 1 — Confirm CI signed the image

In the GitHub Actions run, the `container-scan` job should show:

- **Generate SBOM** — wrote `sbom.cdx.json` + `sbom.spdx.json`
- **Cosign keyless sign (by digest)** — prints `tlog entry created with index: N`
- **Cosign attest SBOMs** — CycloneDX + SPDX attestations
- **Upload SBOM to S3** — `s3://<bucket>/sbom/guardops-app/<digest>/`

Verify locally:

```bash
guardops verify-image <account>.dkr.ecr.ap-south-1.amazonaws.com/guardops-app@sha256:<digest>
guardops verify-image <ref> --attestation     # also checks the SBOM attestation
cosign tree <ref>                             # lists the .sig + .att artifacts
```

If `guardops verify-image` prints `Signature verified` the identity regexp and the
CI signature agree — this is the prerequisite for Enforce mode to pass.

---

## Step 2 — Install Kyverno (Terraform)

In `infra/terraform/terraform.tfvars`:

```hcl
enable_kyverno        = true
kyverno_policy_action = "Audit"   # start in Audit, never Enforce on day one
```

```
terraform -chdir=infra/terraform apply -target=module.kyverno
```

This installs Kyverno and creates the IRSA role (`guardops-kyverno-ecr-read`) the
admission/background controllers use to read cosign signatures from private ECR.

---

## Step 3 — Apply the policies (Audit)

```powershell
.\scripts\setup-admission-control.ps1
```

`morning-start.ps1` does this automatically when `enable_kyverno = true`. The script
substitutes the CI identity (issuer + subject) and the policy action into the
`k8s/kyverno/*.yaml` manifests, then `kubectl apply`s them. `networkpolicy*` files
are reference-only and are skipped.

Inspect:

```bash
kubectl get clusterpolicy
kubectl get polr -A        # PolicyReports — pass/fail per pod
kubectl get cpolr          # cluster-scoped reports
```

Expected in Audit: running prod/staging pods **pass** the verify-image and
best-practice rules; only `guardops-require-readonly-rootfs` warns (advisory).

Negative test (pod is **created** in Audit, but the report shows `fail`):

```bash
kubectl run rogue --image=nginx:latest -n staging
kubectl get polr -n staging
kubectl delete pod rogue -n staging
```

---

## Step 4 — Flip to Enforce

Only after the Audit reports are clean.

```powershell
.\scripts\setup-admission-control.ps1 -Enforce
```

This re-applies the policies with `validationFailureAction: Enforce` and flips the
signature/SBOM webhooks to `failurePolicy: Fail`. From now on, unsigned or
non-compliant pods in `default` / `staging` are **rejected at admission**:

```bash
kubectl run rogue --image=nginx:latest -n staging
# Error from server: admission webhook "...kyverno..." denied the request: ...
```

System namespaces (kube-system, kyverno, argocd, monitoring, cert-manager) are
excluded, so a Sigstore/ECR outage can never block cluster-critical workloads.

---

## Rollback

**Back to Audit (non-destructive):**

```powershell
.\scripts\setup-admission-control.ps1
```

**Remove all GuardOps policies (fastest un-block):**

```bash
kubectl delete clusterpolicy -l app.kubernetes.io/managed-by=guardops
```

**Remove Kyverno entirely:**

```
terraform -chdir=infra/terraform apply   # with enable_kyverno = false
# or, in an emergency:
helm uninstall kyverno -n kyverno
```

`night-shutdown.ps1` already deletes the policies and uninstalls Kyverno first so
teardown is never blocked.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| `polr` shows verify-image `fail` for a CI-signed image | Identity regexp does not match the Fulcio SAN | Confirm the subject is `…/ci.yaml@refs/heads/*`; run `guardops verify-image <ref>` to compare. The morning-start/setup substitute this value. |
| Kyverno report: `401 Unauthorized` / `no credentials` pulling `.sig` | IRSA not applied or SA annotation missing | Check `kubectl get sa -n kyverno kyverno-admission-controller -o yaml` has `eks.amazonaws.com/role-arn`; re-run `terraform apply -target=module.kyverno`. Node role ECR ReadOnly is the fallback. |
| Admission errors: `failed to verify … context deadline exceeded` | Kyverno cannot reach Fulcio/Rekor | Check egress to `*.sigstore.dev` (NAT). Raise `webhookTimeoutSeconds` in `k8s/kyverno/verify-images.yaml`. In Audit, `failurePolicy: Ignore` means this never blocks. |
| The app pod is rejected after Enforce | A real policy violation, or signed image lacks the SBOM attestation | Read the denial message; `cosign tree <ref>` to confirm `.att` exists; if needed keep `guardops-require-sbom-attestation` in Audit. |
| Signatures disappear after a few days | ECR lifecycle expired them | The cosign-aware lifecycle (expire untagged 7d, keep 25 tagged) is applied by `modules/ecr` and the CI repo-create step — confirm it is present on the `guardops-app` repo. |
| Local `guardops verify-image` says `cosign not installed` | cosign missing on PATH | Install cosign (`https://docs.sigstore.dev/cosign/installation`). |

---

## Related

- Policies: `k8s/kyverno/` · Terraform: `infra/terraform/modules/kyverno`
- CLI: `guardops verify-image`, `guardops sbom`
- Scripts: `scripts/setup-admission-control.ps1`, Phase 11 steps in
  `scripts/morning-start.ps1` / `scripts/night-shutdown.ps1`
- CI: `.github/workflows/ci.yaml` — `container-scan` job
