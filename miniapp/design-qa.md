# Compact navigation and scroll-edge port

final result: passed

Scope: visual/behavioral code port into the isolated Taro worktree only. Not a claim of WeChat device acceptance, backend readiness or full accessibility certification.

Source: `../../scroll-shadow-preview-20260912-r1/qa/compact/训练-compact-nav.png` and the accepted 56px fade/64px compact variant at port 8651. Implementation: `qa/navigation/workouts-compact-nav.png`, `workouts-fade-middle.png`, `nutrition-fade-middle.png`; combined and inspected in `qa/navigation/port-comparison.png`.

The nav comparison uses the same selected Training tab. Source screen is 393×852 CSS px at DPR 1; Taro screenshots also use 393×852 at DPR 1, plus 320/430-width behavior checks. Source uses fixed CSS pixels; Taro retains its 750-unit responsive sizing (128rpx nav, 112rpx fade), explaining the approximately 5% height difference at 393px width. This is intentional project sizing compatibility, not a density error. Full screen content/scroll state differs between fixtures and is explicitly labeled, not claimed as a card-layout clone. Focused nav crops are compared at native pixel sizes.

Five fidelity surfaces: existing system typography and 23rpx labels retained; card layout untouched and compact bar/gap match agreed nominal proportions; deep green/off-white/lemon tokens unchanged; existing licensed Lucide SVGs retain crispness and proportions; no business copy changed or comparison buttons ported. The gradient overlays reproduce the solid-background fade without intercepting clicks. No actionable P0/P1/P2 visual mismatch in the scoped navigation/edge treatment. Existing H5 host button/typography differences are not presented as accurate WeChat rendering.

90 H5 fixture checks pass: route/selection synchronization, Agent composer and keyboard visibility, unsaved Agent/150g meal drafts, native scrolling and edge state on 320/393/430 widths, last-content clearance, food-add anchor targeting. All requests intercepted with read-only fixtures. 334 unit/interaction/contract checks and TypeScript pass. A first regression run found optional edge measurement scheduling in hosts without selector APIs; fixed by bypassing enhancement before scheduling, then the complete suite passed without weakening original tests.

Compiled WeChat validation passed (26 JavaScript files), frozen manifest generated (684349 bytes, +2742 bytes over r3). User started Docker; original local service /ready, /test-info and synthetic login/GET plans passed on 192.168.1.21:8541. On 2026-09-12 the user confirmed all five r4 phone acceptance groups passed. The desktop concern about hiding navigation while the Agent keyboard is open was explicitly closed after phone verification; keyboard behavior remains unchanged. This is user-reported device acceptance, not automated device evidence. The user authorized an independent local commit; no production deployment, database migration, push or merge is included.
