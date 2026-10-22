# Capability: payments

## In plain words
Taking money from customers (one-time, subscriptions, marketplaces). Card details must never touch your system. Mistakes cost real money and can create legal problems.

## Trigger words
pay, payment, checkout, buy, subscription, billing, invoice, price, cart, refund, Stripe, store

## Safe defaults
- Use a payment provider's hosted checkout / payment elements (Stripe, Paddle, Lemon Squeezy) — **you never see card numbers** (keeps you out of heavy PCI scope)
- Treat the provider's **webhook** as the source of truth for 'paid'; verify the webhook signature
- Idempotency keys on every payment request; store amounts as integers in the smallest currency unit, with currency
- Keep order/payment state machine explicit (pending → paid → fulfilled → refunded)
- Test mode keys in dev/staging; live keys only in production secrets
- Handle refunds, failed payments, disputes, taxes/VAT (provider tax tools)

## Must test
- Paying twice with the same key charges once
- Webhook with bad signature is rejected; duplicate webhook is harmless
- Failed/cancelled payment never marks an order paid
- Amount and currency can't be tampered with from the browser
- Refund flow works end-to-end in test mode

## Common mistakes
Trusting the browser's 'payment succeeded' redirect; floating-point money; unverified webhooks; storing card data; no handling for failed renewals; mixing test and live keys; ignoring tax and refund rules

## Don't build it yourself
Card handling, fraud checks, subscription billing, tax calculation. Use a payment provider end-to-end.
