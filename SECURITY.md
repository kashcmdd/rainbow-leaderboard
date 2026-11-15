# Security

Please report vulnerabilities privately through
[GitHub Security Advisories](https://github.com/kashcmdd/rainbow-leaderboard/security/advisories/new)
rather than opening a public issue.

Include the affected route or file, reproduction steps, and the impact you
believe it has. The latest `main` is supported.

## Deployment notes

- Set `COOKIE_SECURE=true` behind HTTPS.
- Unset `DEV_LOGIN_DISCORD_ID` and `DEV_LOGIN_ADMIN` in production.
- Keep `SECRET_KEY` and `POSTGRES_PASSWORD` out of version control.
- Restrict admin access with `ADMIN_DISCORD_IDS` or the in-app admin toggle.
