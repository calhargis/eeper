# web — eeper PWA

SvelteKit, built to static files (`@sveltejs/adapter-static`) and served by
Caddy. Installable as a phone app in later phases.

## Develop

```bash
npm ci
npm run dev      # local dev server
npm run check    # svelte-check (TypeScript type-check)
npm run lint     # eslint
npm run build    # static build to ./build
```

## Container

`docker build -t eeper/web web/` produces the served image (static build →
Caddy). The image is built multi-arch (amd64 + arm64) and scanned in CI.

Phase 0 ships only a placeholder shell; Live/Tonight/Trends/Devices/Settings
views arrive per the [implementation plan](../docs/IMPLEMENTATION_PLAN.md).
