---
source: GitHub CLI manual + GitHub Docs
library: GitHub CLI
package: github-cli
topic: device-code login in non-interactive and container environments
fetched: 2026-03-12T00:00:00Z
official_docs:
  - https://cli.github.com/manual/gh_auth_login
  - https://cli.github.com/manual/gh_help_environment
  - https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps#device-flow
  - https://docs.github.com/en/actions/security-guides/automatic-token-authentication
---

# GitHub CLI auth guidance for device-code and headless/container use

## `gh auth login --web`

- `gh auth login` uses a web-based browser flow by default.
- `gh auth login --web` explicitly opens a browser to authenticate.
- `--clipboard` copies the one-time OAuth device code to the clipboard.
- For headless use, the `gh auth login` docs say environment-token auth is more suitable than interactive login.

Source:
- https://cli.github.com/manual/gh_auth_login

## Device code polling and waiting behavior

- GitHub's device flow is intended for headless apps such as CLI tools.
- The app requests a `device_code`, `user_code`, and `verification_uri`, then prompts the user to enter the code at `https://github.com/login/device`.
- Polling must respect the returned minimum `interval`; the documented default example is `interval=5` seconds.
- The device and user codes default to `expires_in=900` seconds (15 minutes).
- If polling too quickly, GitHub returns `slow_down`, which adds 5 seconds to the minimum interval.
- While authorization is still pending, GitHub returns `authorization_pending` and the client should keep polling without exceeding the interval.
- The `gh` manual documents the browser/device-code flow, but not a gh-specific poll timeout beyond the underlying GitHub OAuth device-flow rules.

Source:
- https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps#device-flow
- https://cli.github.com/manual/gh_auth_login

## Credential storage defaults

- After interactive login completes, `gh` stores the token in the system credential store when available.
- If no credential store is found, or if there is an issue using it, `gh` falls back to writing the token to a plain-text file.
- `--insecure-storage` forces plain-text storage.
- The docs say `gh auth status` can be used to see the stored location.

Source:
- https://cli.github.com/manual/gh_auth_login
- https://cli.github.com/manual/gh_auth_status

## Config directory and env overrides

- `GH_CONFIG_DIR` overrides where `gh` stores configuration files.
- If `GH_CONFIG_DIR` is unset, the default config dir is:
  - `$XDG_CONFIG_HOME/gh` if `XDG_CONFIG_HOME` is set
  - `$AppData/GitHub CLI` on Windows if `AppData` is set
  - `$HOME/.config/gh` otherwise
- `GH_TOKEN` / `GITHUB_TOKEN` take precedence over previously stored credentials for `github.com` or `*.ghe.com` subdomains.
- `GH_ENTERPRISE_TOKEN` / `GITHUB_ENTERPRISE_TOKEN` are for GitHub Enterprise Server hosts.
- `GH_PROMPT_DISABLED` disables interactive prompting.

Source:
- https://cli.github.com/manual/gh_help_environment

## CI and container-oriented guidance

- The `gh auth login` docs say environment-variable auth is most suitable for headless use such as automation.
- For GitHub Actions, GitHub's docs and the `gh auth login` manual recommend setting `GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}`.
- GitHub Actions docs recommend least-privilege `permissions` for `GITHUB_TOKEN`.
- Practical container implication from the docs: prefer `GH_TOKEN`/`GH_ENTERPRISE_TOKEN` over interactive `gh auth login --web`; if you must persist config, set `GH_CONFIG_DIR` to a writable mounted directory.
- Caveat: in minimal containers without a usable system credential store, interactive login may fall back to plain-text token storage unless you avoid stored creds and use env tokens instead.

Source:
- https://cli.github.com/manual/gh_auth_login
- https://cli.github.com/manual/gh_help_environment
- https://docs.github.com/en/actions/security-guides/automatic-token-authentication
