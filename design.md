---
version: "alpha"
name: "Aura Premium WebGL & Iconify"
description: "Premium dark-themed landing page with advanced animations and interactions. Ideal for portfolios de luxo, estúdios de arquitetura, marcas premium, móveis de alto padrão, wellness premium, creative agencies. AI-ready template."
colors:
  primary: "#0A0A0A"
  secondary: "#FFFFFF"
  tertiary: "#1A1A2E"
  neutral: "#3B82F6"
  surface: "#8B5CF6"
  accent: "#F59E0B"
typography:
  h1:
    fontFamily: Inter
    fontSize: 2.5rem
    fontWeight: 700
  body-md:
    fontFamily: Inter
    fontSize: 1rem
    fontWeight: 400
components:
  button-primary:
    backgroundColor: "{colors.primary}"
    textColor: "{colors.neutral}"
    padding: 12px
---

## Overview

Premium dark-themed landing page with advanced animations and interactions. Ideal for portfolios de luxo, estúdios de arquitetura, marcas premium, móveis de alto padrão, wellness premium, creative agencies. AI-ready template. Stripe changed the game around 2019. Not with flashy WebGL demos—with restraint. A subtle gradient orb floating behind a pricing card. A mesh that responded to scroll position so gently you almost missed it. That was the signal: premium software doesn't scream, it hums.

Vercel picked up the thread. Linear refined it. The border-beam trend emerged from this lineage—a single animated gradient tracing a card's perimeter, suggesting energy contained within structure. It's theatrical lighting for UI. The glow isn't decoration; it's hierarchy. Your eye goes where the light moves.

What makes this generation different from the WebGL experiments of 2015 is intentionality. Nobody's rendering particle systems for the sake of it anymore. The best implementations use fragment shaders as atmosphere—depth cues that make flat interfaces feel spatial without demanding attention. Combined with crisp Iconify icons and deliberate typography, you get pages that feel engineered rather than designed. That distinction matters to the audience buying $200/month developer tools.

- Density: 5/10 — Balanced
- Variance: 4/10 — Moderate
- Motion: 6/10 — Expressive

- **Style:** Premium, Animated, Interactive, Cinematic
- **Keywords:** Iconify icons, WebGL animations, border beam, sonar animation, flashlight hover, marquee infinite, clip animation, lettermark logo, vertical text, staggered scroll, motion blur, premium dark, luxury UI, interactive cards
- **Era:** 2025-2026 Premium Interactive
- **Light/Dark:** ✗ No / ✓ Full

## Colors

- **Preto Profundo** (#0A0A0A) — Primary background surface
- **Branco Puro** (#FFFFFF) — Light surface, card backgrounds
- **Cinza Escuro** (#1A1A2E) — Dark surface, primary background
- **Azul Elétrico** (#3B82F6) — Secondary accent
- **Violeta Neon** (#8B5CF6) — Accent color, emphasis elements
- **Âmbar Quente** (#F59E0B) — Extended palette, decorative use
- **Esmeralda** (#10B981) — Extended palette, decorative use
- **Rosa Vibrante** (#EC4899) — Decorative accent, highlight elements


## Typography

- **Display / Hero:** Inter — Weight 700, tight tracking, used for headline impact
- **Body:** Inter — Weight 400, 16px/1.6 line-height, max 72ch per line
- **UI Labels / Captions:** Inter — 0.875rem, weight 500, slight letter-spacing
- **Monospace:** JetBrains Mono — Used for code, metadata, and technical values

Scale:
- Hero: clamp(2.5rem, 5vw, 4rem)
- H1: 2.25rem
- H2: 1.5rem
- Body: 1rem / 1.6
- Small: 0.875rem


## Layout

- **Grid:** CSS Grid primary. Max-width containment: 1280px centered with 1.5rem side padding.
- **Spacing rhythm:** Balanced. Base unit: 0.5rem (8px).
- **Section vertical gaps:** clamp(4rem, 8vw, 8rem).
- **Hero layout:** Split-screen (text left, visual right).
- **Feature sections:** Zig-zag alternating text+image rows. No 3-equal-columns.
- **Mobile collapse:** All multi-column layouts collapse below 768px. No horizontal overflow.
- **z-index contract:** base (0) / sticky-nav (100) / overlay (200) / modal (300) / toast (500).


## Elevation & Depth

WebGL 4-column clip slide down para imagens, border beam animation 1px em botões pill-shaped no hover, vertical text clip slide down letter by letter, sonar animation em elementos decorativos, flashlight effect sutil no hover/posição do mouse em background e border dos cards, marquee infinito com alpha mask, motion blur em scroll staggered, container-size lines verticais, numeração 01 02 03 em detalhes de seção.

- **Physics:** Spring — stiffness 120, damping 20. Confident, weighted transitions.
- **Entry animations:** Fade + translate-Y (16px → 0) over 480ms ease-out. Staggered cascades for lists: 100ms between items.
- **Hover states:** Scale(1.03) + shadow lift over 200ms.
- **Page transitions:** Fade + slide (300ms).
- **Performance:** Only transform and opacity animated. No layout-triggering properties.


## Shapes

Base corner radius: 9999px. See rounded tokens in front matter for the full scale.


## Components

- **Primary Button:** Pill-shaped (9999px) shape. Accent color fill. Hover: 8% darken + subtle lift shadow. Active: -1px translate tactile press. Font weight 600. No outer glows.
- **Secondary / Ghost Button:** Outline variant. 1.5px border in muted color. Text in primary color. Hover: subtle background fill.
- **Cards:** Pill-shaped (9999px) corners. Surface background. Subtle shadow (0 2px 12px rgba(0,0,0,0.06)). 1px border stroke.
- **Inputs:** Label above input. 1px border stroke. Focus ring: 2px accent color offset 2px. Error text below in semantic red. No floating labels.
- **Navigation:** Primary surface background. Active item: accent color indicator. Font weight 500 when active.
- **Skeletons:** Shimmer animation matching component dimensions. No circular spinners.
- **Empty States:** Icon-based composition with descriptive text and action button.


## Do's and Don'ts

- No emojis in UI — use icon system only (Lucide, Heroicons)
- No pure white (#FFFFFF) backgrounds — use off-white or dark surfaces
- No oversaturated accent colors (saturation cap: 80%)
- No 3-column equal-width feature layouts — use zig-zag or asymmetric grid
- No `h-screen` — use `min-h-[100dvh]`
- No AI copywriting clichés: "Elevate", "Seamless", "Unleash", "Next-Gen"
- No broken external image links — use picsum.photos or inline SVG
- No generic lorem ipsum in demos

- Do Iconify Solar Duotone Bold icons carregados via CDN
- Do <iconify-icon> tag usada (não SVG inline)
- Do Logo com Solar Linear + lettermark tracking-tighter
- Do Simple Icons para logos de empresas 64x64
- Do Fotos reais de headshot nos testimonials
- Do Linhas verticais container-size decorativas
- Do Numeração 01 02 03 nas seções
- Do Border beam 1px animado em botões pill hover
- Do Vertical text clip slide down letter by letter
- Do Sonar animation em decorações
- Do Flashlight hover effect nos cards (background + border)
- Do Marquee infinito com alpha mask
- Do WebGL canvas para animações de imagem
- Do 4-column clip staggered scroll com motion blur
- Do Dark mode consistente
- Do Responsivo mobile/tablet/desktop


## Use Case

Luxury portfolios, Architecture studios, Premium brands, High-end furniture, Premium wellness, Creative agencies

<!-- Source: https://designmd.app/library/aura-premium-webgl-iconify · designmd.app -->
