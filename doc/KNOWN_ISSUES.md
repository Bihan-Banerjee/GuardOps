# GuardOps Known Issues & Technical Debt

**Last Updated:** 2026-06-06  
**Current Release:** v1.0.1  
**Status:** Healthy — all critical issues resolved, platform-specific workarounds documented

This document tracks known limitations, bugs, workarounds, and design trade-offs in GuardOps. Issues are organized by category and severity. All entries include context, workarounds (if applicable), and planned resolution timelines where known.

---

## 🔴 Critical Issues

### None Currently Active
All critical issues have been resolved as of v1.0.1. The codebase is in a stable, production-ready state.

---

## 🟠 Major Issues with Workarounds

### 1. Falco eBPF Sensor Memory Allocation Failure on Small Instances
**Component:** `k8s/falco/`  
**Affected Versions:** All  
**Severity:** High (affects real-time runtime security monitoring)  
**Status:** Mitigated via simulator

**Description:**
The Falco eBPF sensor requires kernel-level contiguous memory for perf ring buffer allocation. On single-node clusters (e.g., t3.large with full monitoring stack), this allocation fails:
```
unable to mmap the perf-buffer
```

**Impact:** 
- Real eBPF sensor cannot start on demo/dev clusters
- Live runtime security events not captured

**Workaround:**
Use the Falco simulator CronJob (`k8s/falco/`) which provides identical JSON output for portfolio and development use. The simulator:
- Generates realistic scan findings compatible with the dashboard
- Requires no kernel privileges or special memory allocation
- Suitable for demonstrations, testing, and offline validation

**When to Deploy Real eBPF:**
- Production clusters with adequate memory (c5.xlarge or larger recommended)
- Clusters where live sensor data is mission-critical
- When kernel module loading is permitted and CPU quotas support the overhead

**Related:** README.md:1634

---

### 2. EKS OIDC Issuer URL Changes After `terraform destroy` + Recreate
**Component:** `scripts/morning-start.ps1`, AWS IAM/OIDC  
**Affected Versions:** All  
**Severity:** High (automation blocker)  
**Status:** Automatic mitigation in place

**Description:**
When an EKS cluster is destroyed and immediately recreated:
1. The new cluster gets a new OIDC issuer URL (AWS generates this per cluster)
2. The IAM role trust policy for `guardops-alb-controller` becomes stale
3. The ALB controller gets `AccessDenied` from STS, pods fail to reach AWS APIs

**Example Failing Scenario:**
```powershell
# terraform destroy + terraform apply (same day)
# → EKS OIDC issuer URL changes
# → guardops-alb-controller pods error: "AccessDenied" on AWS API calls
```

**Automatic Mitigation:**
The `Ensure-AlbControllerRole` function in `morning-start.ps1` detects stale OIDC trust policies and automatically:
1. Fetches the new EKS OIDC issuer URL
2. Updates the IAM role's assume-role-policy document via `aws iam update-assume-role-policy`
3. Validates the update before proceeding with Terraform apply

**No Manual Intervention Needed:**
Run `morning-start.ps1` after any cluster recreation and the fix applies automatically. The script is idempotent.

**Related:** README.md:1672

---

### 3. cert-manager ClusterIssuer Stays Ready=False (DNS/Propagation Delays)
**Component:** `k8s/cert-manager/`, `k8s/networking/`  
**Affected Versions:** All  
**Severity:** Medium (non-blocking for self-hosted setup)  
**Status:** Configuration issue, not a code bug

**Description:**
The cert-manager ClusterIssuer for ACME (Let's Encrypt) occasionally fails to become Ready with status `Ready=False`. Root cause is usually DNS propagation delays or public domain reachability.

**Symptoms:**
```bash
kubectl get clusterissuer
# NAME                STATUS   AGE
# acme-letsencrypt    False    5m
```

**Diagnosis:**
ACME HTTP-01 challenge requires the domain to be publicly reachable and DNS-resolvable:
```bash
# Verify DNS resolution from the cluster
nslookup -type=NS guardops.live 8.8.8.8

# Check cert-manager logs for the actual ACME error
kubectl logs -n cert-manager deployment/cert-manager | grep -i acme
```

**Workarounds:**
- **Wait for DNS to propagate:** DNS changes can take 5-30 minutes to propagate globally. Run the cert-manager retry after propagation completes.
- **Use self-signed certs:** For dev/demo, skip Let's Encrypt and use self-signed certificates (`k8s/networking/ingress-self-signed.yaml`)
- **Verify domain accessibility:** Ensure `guardops.live` (or your configured domain) resolves to the ALB's IP from outside the cluster
- **Check firewall rules:** AWS security groups must allow inbound HTTP:80 traffic for ACME challenges

**Resolution Timeline:**
Usually resolves automatically within 30 minutes of DNS propagation. No code changes required.

**Related:** README.md:1674-1675

---

### 4. AWS Subnet CIDR Conflict After Incomplete `terraform destroy`
**Component:** `terraform/`, AWS infrastructure  
**Affected Versions:** All  
**Severity:** High (cluster recreation blocker)  
**Status:** Terraform/AWS behavior, mitigation documented

**Description:**
An incomplete `terraform destroy` (e.g., interrupted mid-run) can leave subnets with ENIs (Elastic Network Interfaces) still attached. Subsequent `terraform apply` fails because the CIDR blocks are already in use:
```
Error: InvalidSubnet.Conflict: The CIDR '10.0.0.0/20' conflicts with reserved addresses
```

**Impact:**
- Cannot recreate VPC/subnets without manual cleanup
- Automation (morning-start.ps1) cannot proceed

**Mitigation:**
The terraform destroy is idempotent when run to completion. If interrupted:
1. Manually detach or terminate the remaining ENIs:
   ```bash
   aws ec2 describe-network-interfaces --filters "Name=vpc-id,Values=vpc-XXXXX"
   aws ec2 delete-network-interface --network-interface-id eni-XXXXX
   ```
2. Re-run terraform destroy to completion
3. Then terraform apply to recreate

**Prevention:**
- Use `timeout` in CI/CD to ensure destroy completes or rolls back
- Monitor terraform apply/destroy operations for completion

**Related:** README.md:1612

---

## 🟡 Platform-Specific Workarounds

### 1. Windows kubeconfig `host.docker.internal` to `127.0.0.1` Mapping
**Platform:** Windows (Docker Desktop with k3d)  
**Affected Versions:** All  
**Severity:** Medium (Docker Desktop specific)  
**Status:** Workaround documented, automatic in scripts

**Description:**
On Windows, after recreating a k3d cluster, the kubeconfig contains `host.docker.internal` which is Docker Desktop's internal hostname. K3d sometimes fails to correctly map this to `127.0.0.1` for local cluster access.

**Symptom:**
```bash
kubectl get nodes
# Unable to connect to the server: dial tcp: lookup host.docker.internal: no such host
```

**Workaround:**
After recreating a k3d cluster on Windows, patch the kubeconfig:
```bash
# Option 1: Manual patch
sed -i 's/host.docker.internal/127.0.0.1/g' ~/.kube/config

# Option 2: k3d native flag (preferred)
k3d cluster create --api-port 127.0.0.1:6443
```

**Prevention:**
The `morning-start.ps1` script automatically handles this during cluster creation. Manual intervention is only needed if using `k3d` outside the automation script.

**Related:** README.md:1431

---

### 2. Windows Legacy Console Encoding (cp1252 → UTF-8)
**Platform:** Windows (non-UTF-8 legacy console)  
**Affected Versions:** All  
**Severity:** Low (cosmetic, affects terminal output only)  
**Status:** Automatic mitigation in place

**Description:**
Older Windows consoles default to cp1252 encoding, which cannot display UTF-8 characters (emojis, unicode symbols). GuardOps CLI uses unicode in output (✓, ✗, ◉, etc.).

**Impact:**
- CLI output shows mojibake (garbled characters) on legacy consoles
- Build status indicators unreadable

**Automatic Mitigation:**
The `cli/utils/output.py:26` reconfigures legacy console encoding to UTF-8 automatically on first import. No manual configuration needed.

**If Issues Persist:**
Set environment variable before running:
```powershell
$env:PYTHONIOENCODING = "utf-8"
guardops ...
```

**Related:** INSTALL.md:56

---

### 3. PowerShell Tool Output Capture Issue with Long-Running Commands
**Platform:** Windows (PowerShell 5.1)  
**Affected Versions:** All  
**Severity:** Medium (affects automation reliability)  
**Status:** Documented workaround, affects CI only

**Description:**
The PowerShell tool intermittently returns exit code 9 with truncated or empty output when long-running native commands' stdout is piped through `Select-Object -Last/-First` or `Out-String`. This is a PowerShell 5.1 + broken-pipe behavior where the downstream cmdlet closes the pipe early and the native process sees SIGPIPE.

**Affected Commands:**
- Long `terraform apply` operations piped to `Out-String`
- Native commands that generate large output piped to `Select-Object`

**Workaround (Used in codebase):**
Instead of piping to cmdlets, redirect output to a temporary file:
```powershell
# ❌ Problematic
$output = terraform apply | Out-String

# ✅ Recommended
terraform apply | Out-File -FilePath (Join-Path $env:TEMP 'tf-apply.txt') -Encoding utf8
$output = Get-Content (Join-Path $env:TEMP 'tf-apply.txt') -Raw
```

**Current Codebase:**
`scripts/morning-start.ps1:859` uses this workaround for terraform output, piping to `Out-Host` directly instead of capturing.

**Bash Alternative:**
For reliability, prefer Bash tool over PowerShell for native command output capture (the Bash tool does not have this limitation).

**Related:** Memory file: `powershell-tool-exit9-quirk.md`

---

## 🔵 Test Coverage Gaps (Non-Blocking, Improvement Opportunities)

### 1. CLI Permutation Test Coverage
**Component:** `cli/tests/`  
**Current Status:** Individual commands tested, but not parametrized matrix  
**Priority:** Low (functional tests pass, coverage at 100% line)  
**Effort:** Medium

**Description:**
The CLI has many command combinations (run scan, scan modes, output formats, filters). Currently tested individually, but no parametrized matrix validates all combinations.

**Example:**
```python
# Current: individual tests
def test_scan_verbose():
def test_scan_output_json():

# Desired: parametrized matrix
@pytest.mark.parametrize("mode,output,filter", [
    ("container", "json", "critical"),
    ("image", "table", "high"),
    ...  # all combinations
])
def test_scan_all_combinations(mode, output, filter):
```

**Impact:** Rare edge-case CLI flag combinations might not be exercised  
**Planned For:** v1.1.0 or later

---

### 2. Backend Pipeline Module Coverage
**Component:** `backend/pipeline/*.py` (runner.py, pusher.py, etc.)  
**Current Status:** 100% line coverage, but thin parametrization  
**Priority:** Low (all code paths tested, but not all permutations)  
**Effort:** Medium

**Description:**
Pipeline modules handle image building, pushing, and GitOps writes. Comprehensive coverage exists, but parametrized tests for different registry types, error scenarios, and retry paths could be more thorough.

**Example:**
```python
# Current: separate tests for ECR, registry
def test_push_to_ecr():
def test_push_to_docker_hub():

# Desired: parametrized
@pytest.mark.parametrize("registry_type", ["ecr", "docker", "ghcr"])
def test_push_to_registry(registry_type):
```

**Impact:** Rare registry configuration combinations might have unexercised code  
**Planned For:** v1.1.0 or later

---

### 3. Optional k3d Integration Test Suite
**Component:** `tests/integration/k3d/`  
**Current Status:** Not implemented  
**Priority:** Low (functional e2e tests exist, k3d optional for fast iteration)  
**Effort:** High

**Description:**
An optional, skippable k3d integration suite for local iteration would allow developers to validate the full stack (CLI → backend → Kubernetes) without AWS.

**Current Situation:**
- Full e2e tests require AWS EKS
- Fast local iteration uses mocked Kubernetes
- No middle ground for lightweight k3d validation

**Desired:**
```bash
# Skip by default (slow, optional)
pytest -m "not k3d_integration"

# Run for full validation
pytest -m "k3d_integration"
```

**Impact:** Developers must choose between fast mocked tests or slow AWS tests  
**Planned For:** v1.2.0 or later

---

## 🟢 Resolved Issues (Historical Reference)

### Terraform Output Not Displaying (v1.0.0 Fix)
**Fixed By:** `scripts/morning-start.ps1:859` (PowerShell pipe handling)  
**Commit:** `aea4d12` (release: v1.0.1)

**Original Bug:**
The `Invoke-TerraformApply` function's output was being captured into a variable, which swallowed stdout. This caused two problems:
1. Terraform's plan/apply output never displayed on screen
2. The exit code check became unreliable (array of text lines + int = always truthy)

**Result:** Manual `terraform apply` always worked, but scripted apply appeared to fail silently.

**Fix:**
Pipe terraform output to `Out-Host` directly instead of capturing, then check exit code separately:
```powershell
terraform apply @args | Out-Host
$ec = $LASTEXITCODE
```

---

### Trivy CVE Issues (v1.0.0 Fix)
**Fixed By:** Commits `129cc6c`, `382146b` (2026-06-05)  
**Versions Affected:** v0.13.x, v0.14.0-rc

**Original Issues:**
- 10 HIGH/CRITICAL CVEs in demo Docker image (`test-project`)
- 3+ perl-base CVEs in system tools (unfixable by Debian)

**Fixes Applied:**
1. Upgraded FastAPI 0.115 → 0.136, Starlette 1.2.x (urllib3, idna updates)
2. Added `apt-get upgrade` to demo Dockerfile for base-image CVEs
3. Removed pip, setuptools, ensurepip from final demo image layer

**Resolution:** All fixable CVEs resolved in v1.0.0. Unfixed CVEs (perl-base) are OS-level; CI gate uses `--ignore-unfixed`.

---

### SAST Gate False Positives (v1.0.0 Fix)
**Fixed By:** Commit `4af2840` (2026-06-05)  
**Component:** `.github/workflows/ci.yaml` (Bandit SAST gate)

**Original Issues:**
- Shell injection false positive on parameterized commands
- CVE-related findings on non-security code (SHA-1 fingerprints, logging configs)

**Fixes:**
1. Fixed legitimate shell injection in `iam_oidc` script (now parameterized)
2. Suppressed false positives with justifications:
   - Public AWS account ID (non-sensitive)
   - EKS control plane logging disabled (cost optimization, not security issue)
   - Non-crypto SHA-1 (dedup, not cryptographic)
   - Parameterized SQLite queries (false positive, queries are safe)

**Resolution:** CI SAST gate now passes without legitimate findings.

---

### EKS Cluster Flag Sync Issue (v1.0.0 Fix)
**Fixed By:** Commit `45396e2` (2026-06-05)  
**Component:** `scripts/morning-start.ps1`, `.github/workflows/ci.yaml`

**Original Issue:**
The `HAS_EKS_CLUSTER` flag used in CI to decide which tests to run could drift out of sync with actual cluster existence, causing tests to be skipped unexpectedly or run against non-existent clusters.

**Fix:**
Added automation to sync the flag with actual cluster state in `morning-start.ps1` and validate in CI before test runs.

---

## 📋 Intentionally Suppressed Findings

The following findings are intentionally suppressed in CI with documented justifications (not hidden bugs):

### SAST Gate Suppressions (CI/CD)
From `.github/workflows/ci.yaml:232-266`:

| Finding | Component | Reason |
|---------|-----------|--------|
| Shell injection | `iam_oidc.sh` | Public AWS account ID + TLS cert reading (non-sensitive, CI env only) |
| Dangerous system config | `iam_oidc.sh` | EKS control plane logging disabled (cost optimization, not security risk) |
| Parameterization false positive | SQL queries | All queries are parameterized; finding is a false positive |
| Non-crypto SHA-1 | Cache dedup | SHA-1 used for content-based deduplication, not cryptographic |
| Short-lived IRSA role | Kyverno policies | Temporary by design (lifecycle: 5m), expected behavior |

### Trivy CVE Gate Suppressions
From `changelog.md:94`:

**perl-base CVEs (Debian 13.5):**
- Status: `affected` with no Fixed Version
- These are OS-level issues Debian hasn't patched
- CI gate uses `--ignore-unfixed` to pass only on fixable CVEs
- Not a code issue; infrastructure/OS responsibility

---

## Design Trade-offs (Intentional)

### 1. Ephemeral Cluster + Static Snapshot
**Decision:** Run cluster during business hours, destroy each night, publish static snapshot

**Trade-off:** Live data is stale overnight (snapshot from morning-start)  
**Benefit:** 90%+ cost reduction, simpler infra, suitable for portfolio/demo

**Mitigation:**
- Snapshot updated at morning-start (fresh data)
- Redacted before public publish (remove sensitive findings)
- Dashboard can still query live API during cluster runtime

**When Not Suitable:**
- 24/7 monitoring required
- Real-time incident response needed
- Security findings must never be stale

---

### 2. Public Snapshot Exposure (Opt-In)
**Decision:** Allow `enable_public_snapshot` to publish scan findings summaries unauthenticated

**Trade-off:** Scan summaries become visible to the internet  
**Benefit:** Portfolio showcase, public security posture, no auth required

**Scope (By Design):**
- Enabled only when `enable_public_snapshot: true` in config
- Only summary counts (Critical: 5, High: 12, etc.)
- Details require authentication
- Redaction rules applied before publish

**Recommendation:**
- For production: Leave disabled (default: false)
- For portfolio/public demo: Enable with redaction rules in place

---

## Future Improvements

### Planned
- v1.1.0: Parametrized CLI test matrix
- v1.1.0: Expanded pipeline module parametrization
- v1.2.0: Optional k3d integration test suite
- v1.2.0: ESLint/Prettier integration for frontend
- Post-1.0: Real Falco eBPF on production-sized clusters

### Under Consideration
- Incremental snapshot updates (vs. full re-snapshot each morning)
- Multi-region deployment support
- Custom Falco rule templates
- Webhook notifications for findings

---

## How to Report New Issues

Found a bug or limitation not listed here?

1. **For security issues:** See [SECURITY.md](../SECURITY.md)
2. **For bugs:** Open an issue with:
   - GuardOps version (`guardops --version`)
   - Environment (OS, Kubernetes, AWS region)
   - Steps to reproduce
   - Expected vs. actual behavior
3. **For documentation gaps:** Comment on the relevant doc file

---

## Questions?

See also:
- [TROUBLESHOOTING.md](../TROUBLESHOOTING.md) for common runtime issues
- [ARCHITECTURE.md](ARCHITECTURE.md) for design decisions
- [RELEASE.md](../RELEASE.md) for version management
