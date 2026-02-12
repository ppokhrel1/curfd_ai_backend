# Auth Guide (Supabase)

This file explains how authentication works in this backend today, and how Supabase flows like social login and phone OTP fit into it.

## 1) Current implementation in this repo

Auth routes live in `app/api/routes/auth.py` and call Supabase Auth directly:

- `POST /api/v1/auth/register` -> `POST /auth/v1/signup`
- `POST /api/v1/auth/login` -> `POST /auth/v1/token?grant_type=password`
- `GET /api/v1/auth/me` -> `GET /auth/v1/user`
- `GET /api/v1/auth/logout` -> `POST /auth/v1/logout`

The backend does not use custom user tables for sign-up/sign-in. Supabase `auth.users` is the source of truth.

## 2) Required env vars

Set these in `.env`:

- `SUPABASE_URL` (example: `https://<project-ref>.supabase.co`)
- `SUPABASE_ANON_KEY` (preferred for auth calls)
- `SUPABASE_SERVICE_ROLE_KEY` (optional fallback in current code)

Also review token verification for protected routes:

- Most non-auth protected routes use `app/core/deps.py` -> `decode_access_token()`.
- That path validates JWT with `SECRET_KEY`.
- If you plan to use Supabase-issued access tokens everywhere, make sure your verification strategy matches Supabase JWTs.

## 3) Email + password flow (already implemented)

1. Client calls `POST /api/v1/auth/register` with `email`, `password`, optional `display_name`.
2. Backend forwards to Supabase signup endpoint.
3. Client calls `POST /api/v1/auth/login` with `email`, `password`.
4. Backend forwards to Supabase password token endpoint.
5. Backend returns `access_token`, `refresh_token`, `user_id`.
6. Client sends `Authorization: Bearer <access_token>` to protected endpoints.

## 4) Social login (Google, GitHub, Apple, etc.)

Supabase-supported social providers are configured in the Supabase Dashboard under Authentication Providers.

Typical flow:

1. Frontend starts OAuth with Supabase SDK (`signInWithOAuth`).
2. User is redirected to provider (Google/GitHub/etc.).
3. Provider redirects back to your app (`redirectTo` URL must be allowed in Supabase Redirect URLs).
4. Supabase SDK exchanges callback data and creates a session.
5. Frontend sends access token to your backend in `Authorization` header.
6. Backend authorizes requests using that token.

Important:

- Social login is usually initiated in the frontend, not from your FastAPI route.
- Your backend can remain token-consumer only.

## 5) Phone-based login (SMS OTP / WhatsApp OTP)

Typical Supabase phone auth flow:

1. Frontend calls `signInWithOtp({ phone: "+1..." })`.
2. User receives OTP via configured SMS provider.
3. Frontend calls `verifyOtp({ phone, token, type: "sms" })`.
4. Supabase returns session tokens.
5. Frontend uses bearer token for backend APIs.

Notes:

- Phone auth requires provider setup in Supabase (Twilio/MessageBird/Vonage).
- WhatsApp channel requires Twilio WhatsApp sender.

## 6) Passwordless email login (magic link / email OTP)

You can also use email OTP/magic links:

1. Frontend calls `signInWithOtp({ email })`.
2. User gets either magic link or OTP (based on Supabase email template config).
3. Frontend verifies via `verifyOtp(...)`.
4. Session tokens are returned and used against backend APIs.

## 7) MFA (TOTP)

Supabase supports TOTP MFA as a second factor:

1. Enroll factor (`mfa.enroll`) and show QR/secret.
2. Create challenge (`mfa.challenge`).
3. Verify code (`mfa.verify`).

During sign-in, check AAL and prompt for MFA when needed.

## 8) Identity linking

Supabase can link multiple identities to one user:

- Automatic linking for matching verified emails.
- Manual linking (`linkIdentity`) when enabled.

This helps users sign in with multiple providers (for example password + Google) without creating duplicate accounts.

## 9) Practical recommendation for this backend

For fastest rollout:

1. Keep email/password auth in backend routes as currently implemented.
2. Implement social/phone/passwordless auth in frontend using Supabase SDK.
3. Send Supabase access token to backend for API authorization.
4. Unify JWT verification in backend so all protected routes validate Supabase tokens consistently.

## 10) Official docs (Supabase)

- Auth overview: https://supabase.com/docs/guides/auth
- OAuth sign-in: https://supabase.com/docs/reference/javascript/auth-signinwithoauth
- OTP sign-in: https://supabase.com/docs/reference/javascript/auth-signinwithotp
- OTP verify: https://supabase.com/docs/reference/javascript/auth-verifyotp
- Redirect URLs: https://supabase.com/docs/guides/auth/redirect-urls
- MFA TOTP: https://supabase.com/docs/guides/auth/auth-mfa/totp
- Identity linking: https://supabase.com/docs/guides/auth/auth-identity-linking
