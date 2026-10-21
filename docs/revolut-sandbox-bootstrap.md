# Revolut Sandbox OAuth bootstrap

This is a standalone, read-only connectivity probe, not a TRUSTA Bank Adapter.
It does not change the core execution architecture or claim full banking integration.
The later 2026-10-08 read-only Core integration is documented separately in
[revolut-readonly-integration.md](revolut-readonly-integration.md); historical
bootstrap results below remain connectivity-only evidence.

1. Configure the registered client ID and exact callback `https://trusta.localhost/revolut/callback`
   in `config/revolut-sandbox/setup.local.json`.
2. Run `python scripts/revolut_sandbox_connect.py --prepare`, then run it without that flag.
3. Open `https://trusta.localhost/start` in the browser already logged into the sandbox.
   The local TLS certificate is self-signed, has SAN `trusta.localhost`, and is separate
   from the API signing certificate. It is not installed in the Windows trust store.
   Check that this is the local address before accepting the local certificate warning.
4. Follow the link from this local page. Requested scope is READ. In the consent UI,
   enable only "Read your account details". Confirm consent yourself.
5. The callback requires the prepared browser cookie; state, when returned, must match.
   State echo is not documented in the current Revolut guide and is not presumed verified.
   Regardless of state echo, no automatic exchange takes place: explicit same-origin POST
   confirmation with the local session CSRF token is required. Confirm promptly.
   The confirmation document uses `Referrer-Policy: same-origin`, so browser form
   submissions preserve the actual Origin header. Callback redirects still use
   `no-referrer` to protect the code URL. Null and foreign POST origins stay rejected.
6. The probe signs a five-minute RS256 assertion (issuer `trusta.localhost`), exchanges
   the code at the fixed sandbox `/auth/token` endpoint, and reads `/accounts`.

Authorization code, JWT, account identifiers/balances, and tokens are never logged or
rendered. Tokens are stored outside the project beside the existing private key in the
restricted per-user secrets folder. Reports contain counts/currencies only. The probe
rejects returned scopes other than READ when the bank includes a scope field; absence
of that field is recorded as unknown rather than proof of granted read-only scope.
HTTP redirects cannot forward credentials. The server binds only 127.0.0.1:443 and
stops after success or 20 minutes. No payment endpoint is permitted.

No global TLS verification is disabled. No hosts file, root trust store, firewall,
production account, model configuration, or agent capability is modified.

Local tests validate code and protocol behavior with synthetic responses. A bank-side
success requires an actual user consent, token exchange, and account read. Adapter
mapping, core audit integration, token refresh, and trusted sandbox payments remain
subsequent work. Revoke the sandbox certificate from Revolut when no longer needed.

References:
- https://developer.revolut.com/docs/guides/manage-accounts/get-started/make-your-first-api-request
- https://developer.revolut.com/docs/guides/manage-accounts/get-started/prepare-sandbox-environment
- https://developer.revolut.com/docs/api/business
