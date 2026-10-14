# Contributing

Thank you for helping improve Canvas Ed MCP. This project handles password-equivalent
tokens and education data, so small, reviewable, synthetic changes are preferred.

## Development setup

Use Python 3.10–3.15 in an isolated environment:

```text
python -m venv .venv
python -m pip install .
python -B -m unittest discover -s tests -v
```

Use `uv sync --locked` when reproducing the release lock. Do not replace the reviewed
lock or upgrade dependencies incidentally in an unrelated change.

## Change requirements

1. Preserve the safety contracts in `docs/CURRENT_BASELINE.md`.
2. Add or update a failing synthetic test before changing behavior.
3. Never use a real token, course ID, account ID, signed URL, course document, or
   institution-private response as a test fixture.
4. Keep Canvas, Ed, and Gradescope optional and isolated. A disabled provider must not
   resolve credentials or make a request.
5. Do not add remote write tools to the portable server without a separate design,
   permission model, threat review, and explicit user opt-in.
6. Update user-facing documentation and `CHANGELOG.md` for visible changes.

## Pull requests

Keep commits focused and use Conventional Commits. Describe the behavior and safety
impact, list the exact verification commands, and identify anything that remains
`NOT_RUN`. Pull requests must pass the cross-platform CI and distribution allowlist.

Do not attach local config, logs, state, screenshots, course output, or generated
release artifacts. For a vulnerability, follow `SECURITY.md` instead of opening a
public issue.
