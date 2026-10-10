premium-dashboard-architect
Design elite, production-grade BI and web dashboards that combine rigorous analytical KPI frameworks with high-end Awwwards-tier visual craft. Use when users ask to design, layout, or build modern executive dashboards, SaaS analytics views, real-time control centers, or data visualization platforms.

Instructions
Premium Dashboard Architect
Overview
This skill synthesizes structured business intelligence architecture (KPI definition, information hierarchy, layout heuristics, and tool selection) with bespoke, high-end digital design principles (nested double-bezel cards, micro-interactions, dark OLED aesthetics, refined typography, and fluid kinetic motion). It bridges the gap between data utility and tier-one agency craftsmanship.

When to Use
Designing executive scorecards, SaaS analytics dashboards, or operational control centers where visual quality and clarity must both be top tier.
Transforming cluttered, generic, or default BI/UI dashboards into high-end, responsive products.
Selecting KPIs and structuring data density while establishing luxury UI themes, custom motion physics, and nested card architectures.
Writing frontend specifications or direct Tailwind/React layouts for bespoke data interfaces.
When NOT to Use
Writing complex backend SQL queries or data warehouse ETL pipelines (use dedicated data engineering skills).
Building simple, generic spreadsheet charts with standard office defaults.
Designing marketing landing pages without dashboard or analytical UI components.
Core Architectural Framework
1. KPI Strategy and Information Hierarchy
Before rendering visuals, structure the analytical narrative into three distinct tiers:

Level 1 (Headline KPIs): 3–5 high-impact metrics positioned top-left or across a hero island (e.g., Net ARR, Pipeline Coverage, MTTR). Always pair values with comparative baselines (vs. prior period, % of quota, or target thresholds).
Level 2 (Analytical Context): 4–6 secondary charts providing categorical and temporal breakdowns (e.g., cohort retention, regional distribution, latency heatmaps).
Level 3 (Granular Details): Collapsible data tables, audit logs, or deep-dive filters positioned lower in the visual stream.
2. High-End Visual Guardrails (Strict Anti-Patterns)
Do not use standard, low-effort defaults:

Banned Typography: Avoid generic fallback system fonts like Inter, Roboto, Arial, or Comic Sans. Specify high-character typefaces: Geist, Plus Jakarta Sans, or Clash Display for geometric data, and variable serif fonts for editorial dashboards.
Banned Cards & Shadows: Avoid flat 1px solid gray borders and harsh, murky drop shadows (rgba(0,0,0,0.3)).
Banned Layouts: Avoid symmetrical 3-column Bootstrap-style grids without intentional negative space.
Banned Icons: Avoid heavy, thick-stroked glyphs. Use ultra-light, refined line iconography (e.g., Phosphor Light, Remix Line).
3. Visual Archetypes
A. Ethereal Glass (SaaS / Observability / Tech)
Palette: Deep OLED black (#050505), radial mesh background glow (subtle emerald, indigo, or cyan orbs).
Surfaces: Dark glass containers with backdrop-blur-2xl, hair-thin borders (border border-white/10), and crisp high-contrast white text.
Data Accents: Neon cyan (#00F0FF), electric emerald (#10B981), amber warning, and vivid crimson for critical alerts.
B. Editorial Luxury (Wealth Management / Strategic / Executive)
Palette: Warm cream (#FDFBF7), muted slate, espresso, or dark forest green tones.
Surfaces: Physical paper texture or soft CSS grain (opacity-[0.03]), paired with high-contrast serif headlines and crisp sans numbers.
Data Accents: Deep navy, muted sage, burgundy, and warm bronze.
4. Component Craft: The Double-Bezel Card Architecture
Every primary KPI tile and chart container should employ nested structural depth rather than sitting flat:

Outer Frame: A structural shell with soft padding (p-1.5 to p-2), subtle backdrop fill (bg-white/5 or bg-black/5), concentric radius (rounded-[1.75rem]), and a hairline border (ring-1 ring-white/10 or border border-black/5).
Inner Core: The interactive surface nested inside with a calibrated radius (rounded-[calc(1.75rem-0.375rem)]), distinct surface fill, and an internal highlight (shadow-[inset_0_1px_1px_rgba(255,255,255,0.12)]).
Trailing Action Pills: Secondary actions or trend arrows must sit inside their own dedicated circular or pill enclosure, never floating as bare text next to a label.
5. Layout and Grid Orchestration
Asymmetrical Bento Grid (Desktop)
┌───────────────────────────────────────────────────────────────┐
│  Floating Filter Island (Detached, blur pill, subtle ring)     │
├───────────────────────────────┬───────────────────────────────┤
│  HERO KPI (Large Card)        │  SECONDARY KPI 1  │  KPI 2    │
│  col-span-8, row-span-2       ├───────────────────┴───────────┤
│  Includes primary trendline   │  SECONDARY KPI 3  │  KPI 4    │
├───────────────────────────────┴───────────────────────────────┤
│  Contextual Chart (col-span-7)│  Detail Breakdown (col-span-5)│
└───────────────────────────────┴───────────────────────────────┘
Mobile Responsive Collapse
Below 768px, collapse multi-column spans into a clean single column (grid-cols-1 gap-6).
Limit hero cards to the top 3 critical metrics; place granular charts into horizontal swipeable carousels or collapsible accordions.
Use min-h-[100dvh] instead of h-screen to eliminate mobile browser viewport jumping.
6. Fluid Motion and Kinetic Physics
Interpolation: Never use linear transitions. Use spring curves and custom beziers (e.g., cubic-bezier(0.32, 0.72, 0, 1) over 600–800ms).
Hover Kinetic Feedback: When hovering over KPI tiles, subtly scale the inner core (group-active:scale-[0.99]) and translate trailing indicator badges diagonally (group-hover:translate-x-0.5 group-hover:-translate-y-0.5).
GPU Safety: Animate only transform and opacity. Never animate top, left, width, or height. Keep heavy blur filters restricted to fixed overlays and islands rather than scrolling containers.
7. BI Tool Implementation Mapping
Custom React / Tailwind / Framer Motion: The gold standard for fully bespoke, agency-tier interactive data software.
Tableau / Power BI: Apply these principles by customizing JSON theme files, stripping standard drop shadows, enforcing custom hex palettes, and embedding custom SVG badges.
Grafana: Ideal for dark-mode telemetry. Enforce strict panel spacing, custom threshold coloring, and high-density time-series alignment.
Pre-Flight Quality Checklist
[ ] 3–5 primary KPIs are clearly identified and benchmarked with comparative baselines.
[ ] Titles state actionable insights (e.g., "Retention Increased 4.2%") rather than passive labels.
[ ] Cards utilize double-bezel nesting (outer shell + inner core) with concentric radii.
[ ] Color usage is constrained: neutral backgrounds with deliberate accent signaling (red, green, blue, amber).
[ ] Mobile breakpoints cleanly stack into a single column without clipping or touch-target overlap.
[ ] Motion curves use custom cubic-beziers; layout properties are never animated directly.