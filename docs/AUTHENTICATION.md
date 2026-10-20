# Canvas and Ed authentication safety

> Reviewed 2026-10-14. This guide describes credential lifecycle; it does not
> authorize a real account request. Tokens are password-equivalent. Store values in
> the OS keyring, keep only references in config, and use HTTPS Authorization headers.
> Never put a token in a URL, command argument, repository file, log, or screenshot.

## 1. Manual Canvas token — one owner, local use only

In the intended Canvas instance, sign in as the same user who will run this local
tool. Open **Account → Settings** (normally
`https://<your-canvas-host>/profile/settings`), scroll to **Approved Integrations**,
select **New Access Token**, enter a clear purpose and the shortest practical expiry,
then generate the token. Canvas displays a newly generated value once; transfer it
directly into the OS keyring and clear the clipboard. The API base URL is normally
`https://<your-canvas-host>/api/v1`; for the University of Sydney it is
`https://canvas.sydney.edu.au/api/v1`.

```text
python -m keyring set canvas-ed-mcp/default canvas
```

The config stores only `keyring:canvas-ed-mcp/default/canvas`. A manual token carries
the permissions of its Canvas user and may be broadly scoped. Use a dedicated token,
the shortest practical lifetime, and a read-only application surface. If the
institution disables personal token generation, do not bypass that policy.

Instructure documents manual generation as a testing/local-development option and
states that asking other users to paste manually generated tokens into a multi-user
application violates Canvas API policy. Any distributed or hosted version must use
the institution-approved **OAuth 2.0** flow instead.

## 2. Canvas OAuth 2.0 — multi-user or distributed deployments

Obtain a Developer Key from the Canvas root-account administrator, use the
authorization-code flow, validate the returned `state`, and request only the endpoint
scopes needed by the read-only tools. Never ship the client secret in a desktop/public
client. Canvas Developer Key scopes are controlled by the administrator; removing a
scope can invalidate tokens derived from that key.

OAuth access tokens from modern Developer Keys are short-lived and use refresh
tokens. Refresh tokens and client secrets need the same protected-storage treatment
as access tokens. This repository has no OAuth callback/refresh implementation yet;
OAuth is therefore **documented but NOT_IMPLEMENTED**, not a selectable auth mode.

To revoke an OAuth token, the application can authenticate a request to
`DELETE /login/oauth2/token`; a user can also remove the integration under Approved
Integrations. Remove the local keyring entry only after remote revocation. Do not
confuse a local delete with server-side revocation.

Official references:

- https://developerdocs.instructure.com/services/canvas/oauth2/file.oauth
- https://developerdocs.instructure.com/services/canvas/oauth2/file.oauth_endpoints
- https://developerdocs.instructure.com/services/canvas/oauth2/file.developer_keys

## 3. Ed personal API token — beta/unofficial integration boundary

After signing in to the correct Ed account, open
https://edstem.org/au/settings/api-tokens. Create a dedicated personal token and
place it directly in the OS keyring:

```text
python -m keyring set canvas-ed-mcp/default ed
```

The config stores only `keyring:canvas-ed-mcp/default/ed`. Do not reuse a Canvas token,
session cookie, SSO password, or browser developer-tools payload.

Ed's public marketing/help pages do not expose a stable public API reference without
authentication. The token settings page is authenticated, and public institutional
guidance describes the API as beta with no stability guarantee. Evidence is therefore
**DEGRADED**: token-page availability and exact create/revoke labels require manual
account verification. The adapter intentionally fails closed on schema drift and
keeps Canvas operational when Ed is unavailable.

To retire an Ed credential, revoke/delete that exact token on the API Tokens settings
page, then delete its OS-keyring entry. If no revoke control is available, stop using
the token and contact Ed support or the institution; local deletion alone does not
prove revocation. Do not automate token creation or scrape SSO/browser sessions.

Public corroborating references (not a substitute for Ed's authenticated UI):

- https://edstem.org/quickstart/ed-discussion.pdf
- https://www.epfl.ch/education/educational-initiatives/wp-content/uploads/2025/04/QUICKSTART_EdDiscussion_exercises_lectures.pdf

## 4. Rotation and incident response

For suspected exposure, stop the MCP/sync process, revoke remotely first, remove the
keyring entry, create a replacement, and review repository status, shell history, MCP
host config, logs, screenshots, and shared artifacts without copying the suspect value.
Do not print a token to test it. A new token should be verified only with the minimum
read-only discovery request after explicit authorization.

Treat `401` with an authentication challenge as expired/invalid credentials and
rotate or re-authorize. Treat `403` as a permission/course-access failure; generating
more tokens is not the default remedy. Repeated authentication failures must not fall
back to browser cookies, passwords, query-string tokens, or a plaintext keyring.
