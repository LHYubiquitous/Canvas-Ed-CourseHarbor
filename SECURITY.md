# Security Policy

## Supported version

Security fixes currently target the newest release-candidate branch only. No public
stable release has been published.

## Reporting a vulnerability

Use GitHub private vulnerability reporting when it is enabled for the destination
repository. If it is unavailable, contact the maintainer through a private channel
shown on the repository owner’s GitHub profile. Do not open a public issue containing
an exploit, token, account/course identifier, signed URL, log, config, or screenshot.

Include a minimal synthetic reproduction, affected version/commit, impact, and whether
the issue requires a configured provider. Replace all real identifiers and secrets
with obvious canaries. Maintainers should acknowledge a report privately before any
public disclosure timeline is agreed.

## Security boundaries

- The portable MCP surface is read-only and limited to explicitly configured courses.
- Manual Canvas and Ed tokens are intended for one-owner local use. Multi-user Canvas
  deployment requires institution-approved OAuth, which is not implemented.
- Ed and Gradescope integrations depend on unofficial or scraping-based interfaces and
  may fail closed when their schemas change.
- Course material is private user data and is never an acceptable issue attachment or
  public test fixture.
- The private development repository contains legacy compatibility history. Only the
  audited clean-history source candidate is eligible for a future public repository.

See `docs/AUTHENTICATION.md` for credential rotation and incident response.
