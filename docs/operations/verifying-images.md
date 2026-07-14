# Verifying published images

Every eeper container image published to GHCR is **signed** and carries an **SBOM** and
**build provenance**. Signing is keyless [cosign](https://docs.sigstore.dev/) via the
build workflow's GitHub OIDC identity (Sigstore Fulcio for the certificate, the public
Rekor transparency log for the record) — there are no long-lived signing keys to leak.

Images live under `ghcr.io/calhargis/eeper/<service>` (e.g. `server`, `web`, `caddy`).
Each `main` build publishes `:latest` and `:<commit-sha>`; a release tag additionally
publishes an immutable `:vX.Y.Z`. All of them are signed.

## Verify the signature

Requires [cosign](https://docs.sigstore.dev/system_config/installation/). Verify against
the workflow identity that is allowed to sign — not just "is it signed", but "was it signed
by **our** release pipeline":

```bash
cosign verify \
  --certificate-identity-regexp '^https://github.com/calhargis/eeper/\.github/workflows/images\.yml@refs/' \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/calhargis/eeper/server:latest
```

A successful verification prints the signed payload(s) and confirms the certificate was
issued to that workflow and logged in Rekor. Pin to a digest (`...server@sha256:…`) or an
immutable `:vX.Y.Z` tag for a reproducible check; `:latest` moves.

## Inspect the SBOM and provenance

The SBOM (SPDX) and max-mode provenance are attached to the image as attestations:

```bash
# Software bill of materials (what's inside the image):
docker buildx imagetools inspect ghcr.io/calhargis/eeper/server:latest \
  --format '{{ json .SBOM }}'

# Build provenance (how/where it was built):
docker buildx imagetools inspect ghcr.io/calhargis/eeper/server:latest \
  --format '{{ json .Provenance }}'
```

## What this gives you

- **Authenticity** — the image came from this repo's build workflow, not a look-alike.
- **Integrity** — verification is by digest, so a tampered layer fails.
- **Transparency** — the signature is in the public Rekor log; it cannot be quietly issued.
- **Traceability** — the SBOM + provenance tie the image back to its source and dependencies
  (and complement the CRITICAL-CVE Trivy scan every image passes before it is pushed).

Signing does not replace trusting the registry account; it lets you detect tampering and
confirm origin. For the overall security posture see
[security-review.md](./security-review.md).
