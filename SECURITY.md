# Security

## Reporting a vulnerability

Please report it privately: on GitHub, **Security › Report a vulnerability** in this repository. Don't open a public
issue. Include what you found, how to reproduce it, and what it lets someone do. You'll get an answer as soon as it
has been looked at.

## Running it safely

The Sovereign is self-hosted; how it is exposed is up to you.

- By default it listens on `127.0.0.1` only. To reach it from other devices, put HTTPS in front, set
  `SOVEREIGN_BIND=0.0.0.0`, `COOKIE_SECURE=true`, and list the address in `SOVEREIGN_ORIGINS`.
- Sign-up is invite-only (`SIGNUP=invite`): the first account is the admin and invites everyone else.
- `SOVEREIGN_TOKEN` is the services' own key for calling each other. It never reaches a browser; keep `.env` private
  (`./sovereign init` writes it with mode 600).
- `./sovereign selfcheck` checks the running system: the API answers nothing without credentials, a connection
  limited to one domain can't reach another, and research refuses what robots.txt keeps bots out of. It also runs
  every night and leaves any failure in the Head's inbox.
