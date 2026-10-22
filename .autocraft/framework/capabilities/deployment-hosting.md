# Capability: deployment-hosting

## In plain words
Getting your software from your computer onto the internet (or users' devices) safely, repeatably, and with a way to undo it.

## Trigger words
deploy, publish, go live, host, server, domain, URL, production, release, cloud, hosting

## Safe defaults
- Pick a managed platform that matches the archetype (see its 'Where it runs'); avoid managing servers yourself
- Three environments at most: local, preview/staging, production; every PR can get a preview
- Deploy only from `main` via CI — never from a laptop; same build artifact promoted through environments
- Health check after deploy; automatic or one-click **rollback**; database changes backward compatible
- Custom domain + HTTPS (automatic certificates); DNS documented
- Cost: set a budget alert on day one; know the free-tier limits
- Production changes by humans: the deploy button is yours

## Must test
- A fresh deploy from `main` works from scratch
- Health check fails the deploy when the app is unhealthy
- Rollback to the previous version works and is rehearsed once
- Config differences between environments are documented

## Common mistakes
Deploying from a laptop; manual server edits; no rollback plan; no budget alert; testing in production; forgetting HTTPS; environments that drift apart

## Don't build it yourself
Servers, load balancers, certificates. Use a managed platform or serverless.
