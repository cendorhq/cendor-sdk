# Security Policy

We take the security of `cendor-sdk` (and the `cendor-init` CLI in this repo) seriously. Thank you for
helping keep them and their users safe.

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Report vulnerabilities privately through **GitHub Private Vulnerability Reporting**:
<https://github.com/cendorhq/cendor-sdk/security/advisories/new> — or open the **Security** tab of
this repository and choose **Report a vulnerability**. This creates a private advisory only the
maintainers can see, and lets us collaborate on a fix and coordinate disclosure with you.

Please include, where you can:

- the affected package(s) and version(s) — `cendor-sdk`, `cendor-init`, or one of the `cendor-*`
  libraries it depends on,
- a description of the issue and its impact,
- steps to reproduce or a proof of concept,
- any known mitigations.

If the issue is in one of the libraries beneath the SDK (`cendor-core`, `cendor-tokenguard`,
`cendor-guardrails`, `cendor-acttrace`, `cendor-contextkit`, `cendor-squeeze`, `cendor-cassette`),
report it on the [`cendor-libs`](https://github.com/cendorhq/cendor-libs) repository instead — but if
you are unsure, report it here and we will route it.

## Scope

`cendor-sdk` is a **local-first library**: it runs in your process, and Cendor operates no server and
no network service that your agent talks to. That shapes the threat model — there is no hosted Cendor
endpoint to attack. Relevant classes of issues include, for example:

- a redaction bypass, so PII or a secret reaches a provider despite an active `guard(Policy...)`;
- incorrect budget enforcement, so an over-budget run is not refused;
- audit-chain flaws — a chain that `verify()` accepts after tampering, or an entry that escapes it;
- unsafe deserialization of a cassette, checkpoint, session store, or audit file;
- a credential leak — an API key written into an audit entry, a span, a cassette, or an exception
  message;
- prompt/tool paths that let untrusted model output reach a dangerous operation without passing the
  approval and policy seams they document.

`acttrace` produces **evidence to support** a compliance case — it is not a compliance guarantee, and
nothing in this repo is legal advice.

Out of scope: vulnerabilities in a provider's own SDK or API (report those upstream), and issues that
require an attacker to already control the process the SDK runs in.

## What to expect

- We aim to acknowledge a report within a few business days.
- We'll work with you on a fix and a coordinated disclosure timeline, and credit you in the advisory
  unless you prefer to remain anonymous.

## Supported versions

Fixes land on the latest released minor of `cendor-sdk`. Because versions are independent across
languages, the same fix may ship on different version numbers in Python and TypeScript — see the
[parity matrix](https://cendor.ai/docs/languages).
