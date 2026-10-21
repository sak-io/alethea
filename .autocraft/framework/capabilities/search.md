# Capability: search

## In plain words
Letting users find things by typing words: product search, document search, filters, autocomplete.

## Trigger words
search, find, filter, autocomplete, suggestions, lookup, query, relevance

## Safe defaults
- Start with the database's own full-text search (Postgres FTS) for modest data; move to a search service (Algolia/Typesense/Meilisearch/OpenSearch) only when you need typo-tolerance, speed at scale or facets
- Keep the index in sync via events/jobs; plan reindexing from the source of truth
- Authorization-aware results (never show items the user can't see)
- Pagination, limits and timeouts on every query; sanitize input
- Define 10–20 sample queries with expected results to judge relevance

## Must test
- Sample queries return the expected top results
- Permission filtering: restricted items never appear
- Index updates after create/update/delete; reindex from scratch works
- Empty, very long, or special-character queries behave

## Common mistakes
Leaking private items in results; index drifting out of sync; unbounded result sets; no relevance testing; running a heavy search cluster for tiny data

## Don't build it yourself
Search engines. Use database search first, then a managed search service.
