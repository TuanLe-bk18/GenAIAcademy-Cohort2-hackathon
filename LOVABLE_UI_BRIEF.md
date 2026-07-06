# HeatSafe AI Ops — Creative Brief for Lovable

## The product

Design a compelling operations product called **HeatSafe AI Ops** for two-wheel ride-hailing companies.

HeatSafe helps an operations manager protect drivers during extreme heat without unnecessarily damaging fleet capacity, fulfillment, ETA, or platform economics.

This is not a public weather dashboard, a medical product, or a driver mobile app. It is a decision-support tool for the company operating the fleet.

The central question is:

> Who should take a recovery break, where and when should it happen, and what will the decision mean for drivers and the business?

## The core idea

HeatSafe combines three mechanisms:

- **SafePause:** short, staggered recovery breaks instead of pausing everyone at once.
- **Earnings Guard:** bounded support for estimated earnings lost during a pause.
- **CoolStop:** nearby partner locations providing hydration and recovery support.

AI detects heat-risk escalation, forecasts demand, compares possible interventions, and recommends a plan. A human operations manager reviews the tradeoffs and confirms the decision.

For this prototype, confirmation records a **simulation only**. It never sends a command to drivers.

## The user

The primary user is a fleet or marketplace operations manager monitoring multiple Hanoi zones during a heatwave.

They need to answer three questions quickly:

1. Where is action most urgent?
2. What does the AI recommend and why?
3. Can we protect drivers while staying within cost and service guardrails?

The experience should make a defensible decision possible in under two minutes.

## The essential decision journey

The product should naturally guide the operator through this loop:

**Detect risk → understand demand → review SafePause → compare tradeoffs → confirm simulation → audit the decision**

The exact layout, component system, visualization style, and navigation are yours to explore. Create the clearest and most persuasive operational experience rather than reproducing a conventional dashboard template.

## Information the experience must communicate

At city level:

- Active drivers.
- Expected heat-risk escalations over the next 60 minutes.
- Drivers exposed for 4+ hours.
- Zones that may require action.
- Data freshness and whether AI recommendations are available.

For a selected zone:

- Heat conditions and nearby CoolStop.
- Eligible and mandatory-priority drivers.
- The recommended number of drivers to pause.
- Staggered pause timing or waves.
- Expected risk reduction and protected recovery time.
- Forecast demand.
- Stress-case fulfillment and ETA impact.
- Net platform cost and cost limit.
- Why the model selected this plan.
- Feasible alternatives when useful.

The user must be able to see driver benefit and business impact together. Neither side should feel like an afterthought.

## Product truths that must not be changed

- Drivers exposed for 4+ hours are mandatory-priority in the demo policy.
- SafePause is staggered to preserve supply.
- Recommendations must respect cost, fulfillment, ETA, and mandatory-coverage guardrails.
- AI is advisory; a human confirms every simulated decision.
- If model evidence is unavailable or data are stale, the product must not invent a recommendation.
- Heat Index is a screening indicator, not a medical diagnosis.
- Risk reduction is a model estimate, not guaranteed prevention or causal proof.
- Replay operations and outcomes are simulated.
- The prototype records decisions but does not dispatch commands.

## Important states

Design the experience so it remains trustworthy in these situations:

- AI is ready and a feasible plan exists.
- Data are stale.
- AI is unavailable and the product becomes monitoring-only.
- No plan can satisfy all guardrails.
- A simulation has been recorded successfully.

In failure states, explain what is known, what is unavailable, and what the operator can still do.

## Creative direction

The interface should feel like a modern, credible ride-hailing operations product used during a live event.

Aim for:

- Strong decision hierarchy.
- Calm under pressure rather than visually alarming everywhere.
- Dense enough for professional operations but understandable to a first-time viewer.
- Clear progressive disclosure for technical evidence.
- A distinctive HeatSafe identity connected to heat, movement, protection, and operational control.

Avoid:

- A generic analytics-dashboard template.
- A public weather-app aesthetic.
- A medical or emergency-services aesthetic.
- Decorative AI imagery, excessive gradients, glassmorphism, or meaningless charts.
- Making cloud infrastructure the main story.

You may choose light, dark, or adaptive presentation, typography, color system, map treatment, and visualization approach. Make those choices serve the decision journey.

## Starting scenario

Use a heatwave replay in Hanoi as the default story.

Example zones and conditions:

- Hoàn Kiếm — Heat Index 46.2°C — 316 active drivers — 34 exposed 4h+ — HydraHub Hoàn Kiếm.
- Hai Bà Trưng — 44.5°C — 338 active — 31 exposed 4h+ — Green Rest Hai Bà Trưng.
- Đống Đa — 42.1°C — 355 active — 26 exposed 4h+ — HydraHub Đống Đa.
- Ba Đình — 40.3°C — 284 active — 19 exposed 4h+.
- Cầu Giấy — 38.4°C — 369 active — 22 exposed 4h+.

You can create plausible mock values for model scores and the recommended plan. Clearly treat them as demo estimates and keep them internally consistent.

## Technical boundary

Build a polished React + TypeScript frontend prototype using mock data. Keep mock data behind a simple service layer so it can later connect to the existing Python/GCP backend.

The real system uses BigQuery, BigQuery ML, Gemini, Cloud Storage, and Cloud Run. These can appear as supporting trust or provenance details, but the main story is the operator decision.

Do not add authentication, billing, marketing pages, or unrelated administration features.

## What success looks like

A first-time viewer should quickly understand:

- Hoàn Kiếm is currently the priority.
- HeatSafe recommends a specific staggered SafePause intervention.
- The plan protects a defined driver cohort.
- The expected service and financial impact stays visible.
- A human remains in control.
- The current action is only a simulation.

Create your strongest product concept from this brief. Feel free to challenge conventional dashboard layout and propose a more effective interaction model, while preserving the product truths above.
