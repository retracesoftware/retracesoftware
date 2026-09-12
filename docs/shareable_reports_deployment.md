# Shareable Reports Deployment Note

The current implementation has a reusable core:

```text
ReportService
  -> validates upload payloads
  -> generates server-side UUIDs
  -> canonical-renders HTML/Markdown from JSON
  -> stores report JSON/HTML/Markdown
  -> stores delete-token hashes
```

The stdlib `retrace report-server` command is useful for local smoke testing,
but production should wrap the same core with a hosted adapter and durable
storage.

## Option A: Hosted Python Service

Run a small hosted Python service around `ReportService`.

```text
CLI -> POST /api/reports -> Python service -> DB/object storage
Browser -> GET /r/<uuid> or /f/<uuid> -> Python service
```

Pros:

- closest to the current implementation;
- simple to debug;
- keeps validation and rendering server-side;
- easy to add API-key auth, rate limits, logging, and delete handling in one
  place.

Cons:

- all report reads hit the service unless static caching is added;
- needs production hosting, deploy, observability, and durable storage choices;
- traffic spikes are handled by the app tier.

This is the fastest path if Retrace already has a comfortable Python hosting
surface.

## Option B: Object Storage With Static Reads

Use a thin authenticated API for upload/delete, then serve canonical report
assets from object storage or a CDN.

```text
CLI -> POST /api/reports -> upload API -> object storage
Browser -> GET /r/<uuid> -> CDN/static HTML
Browser -> GET /r/<uuid>.json -> CDN/static JSON
```

Pros:

- cheap and scalable read path;
- report pages are static after canonical rendering;
- CDN/object storage is a good fit for unlisted public reports;
- production app only handles upload/delete/control-plane work.

Cons:

- delete/takedown must invalidate or remove static objects;
- full diagnostic reports need careful noindex/cache/privacy decisions;
- deployment wiring is more complex than a single app service;
- analytics and abuse controls need explicit design.

This is likely the better long-term MVP shape if object storage/CDN is already
available.

## Recommendation

Keep `ReportService` as the shared validation/rendering core either way.

For the first hosted MVP:

1. Use a thin upload/delete API that calls `ReportService`.
2. Store canonical JSON/HTML/Markdown plus metadata and delete-token hash.
3. Serve public/full pages from the simplest durable read path available.
4. Preserve the current local `report-server` for development and release
   smoke testing.

If speed matters more than static-read scalability, start with the hosted
Python service. If CDN/object storage is readily available, use object storage
for reads from the beginning.
