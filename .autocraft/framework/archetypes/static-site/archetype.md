# Archetype: static-site

## Summary
A website made of pre-built pages with little or no server logic: marketing site, portfolio, blog, documentation, landing page.

## In plain words
"I want a website that shows information." Fast, cheap, and hard to hack because there is no server running your code. The easiest thing to build — a great first project.

## Typical products
Landing pages, company sites, docs sites, blogs, event pages.

## Questions the requirements-analyst must ask
1. What is the site for and who visits it? (default: ask)
2. How many pages, and who edits the content (you in code, or non-technical editors)? (default: ≤10 pages, edited in markdown)
3. Need a contact form, newsletter signup or comments? (default: contact form via a form service)
4. Need your own domain name? (default: yes, buy later)
5. Brand colors/logo, or pick a clean default? (default: clean default)

## Architecture must cover
Site generator choice (Astro/Next static export/Hugo/Docusaurus), page and content structure, navigation, SEO (titles, descriptions, sitemap, social cards), performance budget, accessibility, forms via third-party service, analytics (privacy-friendly), domain/DNS/HTTPS, content update workflow.

## Testing strategy
- **Build test:** the site builds with zero errors/broken links (link checker in CI).
- **Unit:** any custom code (form validation, helpers).
- **E2E smoke:** key pages load, navigation works, form submits (Playwright).
- **Quality audits:** Lighthouse (performance/accessibility/SEO) thresholds in CI.

## Walking skeleton (task 1)
Home page + one more page build and deploy to a preview URL on every PR; link check and Lighthouse run in CI.

## CI & tooling
Build, link check, lint/format, Lighthouse CI, dependency audit, preview deployment per PR.

## Where it runs (simple defaults)
Static hosting with a CDN: GitHub Pages, Cloudflare Pages, Netlify or Vercel. Free tier is usually enough. HTTPS is automatic.

## Security focus
No secrets in the repo or built output; third-party scripts (analytics, widgets) are the main risk — minimize and pin; form spam protection; security headers (CSP); dependency CVEs in the build toolchain.

## Release checklist additions
No broken links; every page has title/description; images optimized and have alt text; Lighthouse thresholds met; 404 page exists; domain + HTTPS verified; analytics/consent compliant; rollback = redeploy previous commit.

## Common pitfalls (reviewers look for these)
Huge unoptimized images; missing alt text; no 404/redirects; hard-coded absolute URLs; unpinned third-party scripts; forgetting social/SEO tags; content only editable by developers when it shouldn't be.

## Suggested stacks
Astro or Next.js (static export) → `node` · Hugo → `go` · Docusaurus/VitePress for docs → `node`.
