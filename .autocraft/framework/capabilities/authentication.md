# Capability: authentication

## In plain words
Letting people sign up and sign in, and deciding what each person may see or do. Authentication = who you are; authorization = what you may do. Getting either wrong is the most common way apps leak data.

## Trigger words
login, sign in, sign up, account, password, users, roles, admin, permissions, member, profile, SSO

## Safe defaults
- Use a proven provider or framework (Auth0/Clerk/Supabase Auth/Firebase Auth, or the framework's built-in auth) instead of writing password code
- Passwords: never stored in plain text; use the provider's hashing; offer passwordless/OAuth sign-in where possible
- Sessions: secure, httpOnly, sameSite cookies; short-lived tokens; logout truly ends the session
- Authorization checked on the **server** for every request, defaulting to deny; roles minimal (user/admin)
- Rate-limit login; lockout/backoff; email verification; password reset that expires
- Add MFA for admins

## Must test
- User A cannot read or change user B's data (try it with two accounts)
- Every protected route rejects anonymous and wrong-role requests
- Expired/forged tokens are rejected; logout invalidates the session
- Password reset links work once and expire

## Common mistakes
Hiding buttons instead of enforcing on the server; trusting a user id sent by the browser; roll-your-own crypto/password storage; tokens in localStorage without thought; admin routes with no check; endless sessions; leaking whether an email exists

## Don't build it yourself
Password hashing, session management, OAuth/OIDC flows, MFA. Use a provider or the framework's maintained library.
