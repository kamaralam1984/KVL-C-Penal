# KVL Hosting Panel — frontend

Next.js (App Router) + TypeScript + Tailwind CSS v4. Phase 1: design system, shell and role areas with demo data.

```bash
npm install
npm run dev      # http://localhost:3000
npm run lint
npm run build
```

- Design tokens: `src/app/globals.css` (light default, `.dark` theme)
- Role navigation: `src/config/nav.ts`
- Components: `src/components/ui`, `charts`, `shell`
- Demo fixtures: `src/lib/demo` (never import these directly in screens; go through `src/lib/api`)
- Component showcase: `/design-system`
