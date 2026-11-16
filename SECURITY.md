# Security Policy

Fitness Agent handles authentication, health screening answers and workout history. Treat all deployments as systems containing sensitive personal data.

## Reporting a vulnerability

Please use GitHub private vulnerability reporting for security issues. Do not open a public issue containing credentials, access tokens, user data, production URLs or reproduction steps that expose another user's records.

Include the affected version, component, impact, minimal reproduction steps and any suggested mitigation. Reports about cross-user access, authentication bypass, tool authorization, prompt-injection paths, secret exposure and unsafe health guidance receive the highest priority.

## Secrets

The repository must never contain real values for:

- `DEEPSEEK_API_KEY`
- `WECHAT_APP_SECRET`
- `SECRET_KEY`
- database passwords or production connection URLs
- CloudBase or other hosting credentials

If a secret is committed, rotate it immediately before cleaning Git history. Deleting it only from the latest revision is not sufficient.

## Agent security properties

- The model cannot query the database directly.
- User identity is injected by the server and is absent from model-facing tool schemas.
- The server, not the model, constructs the tool allowlist.
- Write tools remain hidden until they have an explicit confirmation and idempotency contract.
- Health red flags can stop tool execution through deterministic rules.
- Tool calls and decisions retain an audit trail without storing secret values.

## Health disclaimer

This project is not a medical device and does not provide diagnosis or treatment. Production deployments should direct users with urgent symptoms to stop training and seek appropriate medical help.
