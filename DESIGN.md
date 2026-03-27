# Design System Strategy: The Ethereal Sentinel

## 1. Overview & Creative North Star
This design system is defined by a Creative North Star we call **"The Ethereal Sentinel."** It represents the intersection of advanced, cold technology and soft, sentient warmth. Unlike traditional "flat" or "card-based" interfaces that rely on rigid grids and boxed containers, this system prioritizes an editorial, fluid layout. 

The aesthetic moves beyond the "standard app" look by embracing **intentional asymmetry** and **tonal depth**. We achieve a signature feel by overlapping glass elements and using a high-contrast typography scale that creates a sense of hierarchy through scale and "breathing room" rather than structural dividers. The goal is an interface that feels less like a tool and more like a presence.

## 2. Colors
Our palette is a sophisticated translation of the character's soul into a digital environment.

*   **Primary (`#ffcbd5`):** Soft Pink. Used sparingly for high-impact brand moments and critical CTAs. It represents the "human" warmth of the AI.
*   **Secondary (`#c6c6c6`):** Silver/Armor. This provides the structural stability of the UI, used for secondary actions and supporting icons.
*   **Tertiary (`#66eaff`):** Cyan/Emerald. Reserved for "active" states, data visualization, and AI "thinking" indicators.
*   **Neutral/Surface (`#10131a`):** The Sleek Tech Background. This is not a flat black, but a deep, atmospheric void that allows colors to glow.

### The "No-Line" Rule
**Explicit Instruction:** You are prohibited from using 1px solid borders to section content. Boundaries must be defined solely through background color shifts. For example, a `surface-container-low` section should sit on a `surface` background to define its area.

### Surface Hierarchy & Nesting
Treat the UI as a series of physical layers—like stacked sheets of frosted glass.
*   **Base:** `surface` (`#10131a`)
*   **Inner Content:** Use `surface-container-low` (`#191c22`) to define a section.
*   **Interactive Cards:** Use `surface-container-high` (`#272a31`) to lift an element toward the user.

### The "Glass & Gradient" Rule
To achieve a premium feel, use **Glassmorphism** for floating elements (like sidebars or notification overlays). Use a semi-transparent `surface` color with a `backdrop-blur` of 20px–40px. 
**Signature Textures:** Main CTAs or hero sections should utilize subtle gradients transitioning from `primary` to `primary_container`. This provides a visual "soul" that flat hex codes cannot achieve.

## 3. Typography
The system uses a dual-font strategy to balance high-tech precision with editorial elegance.

*   **The Voice (Plus Jakarta Sans):** Used for **Display** and **Headline** scales. Its geometric, clean curves feel modern and expansive. Use `display-lg` (3.5rem) for hero statements to create a dramatic, premium impact.
*   **The Intellect (Manrope):** Used for **Body**, **Title**, and **Label** scales. Manrope offers exceptional legibility at smaller sizes (`body-sm`: 0.75rem) while maintaining a subtle tech-forward personality.

**Editorial Hierarchy:** Always pair a large Headline with a significantly smaller, wide-tracked Label. This "Scale Gap" is what separates high-end design from generic templates.

## 4. Elevation & Depth
Depth in this system is organic, achieved through **Tonal Layering** rather than drop shadows.

*   **The Layering Principle:** Stacking surface tiers creates a soft, natural lift. A `surface-container-lowest` card sitting on a `surface-container-low` section creates immediate hierarchy without a single line of CSS border or shadow.
*   **Ambient Shadows:** When a floating effect is required (e.g., a modal), shadows must be extra-diffused. Use a blur of 40px+ at 6% opacity. The shadow color should be a tinted version of the `on-surface` color, never pure black.
*   **The "Ghost Border" Fallback:** If a border is required for accessibility, it must be a "Ghost Border"—using the `outline-variant` token at 15% opacity. **Never use 100% opaque borders.**
*   **Glows:** Borrowing from the "Emerald Eye" tertiary color, use a `0px 0px 15px` outer glow on active AI states to make the interface feel "alive."

## 5. Components
*   **Buttons:** 
    *   *Primary:* `primary` background, `on-primary` text. Use `full` roundedness (9999px) for an approachable, organic feel.
    *   *Secondary:* Glassmorphic style. Transparent background with a `Ghost Border` and `secondary` text.
*   **Input Fields:** No background color. Use a `Ghost Border` bottom-line only or a very subtle `surface-container-highest` fill. Use `tertiary` for the cursor/caret to mimic the character’s "emerald eyes" focusing on the task.
*   **Chips:** Use `surface-variant` for the background with `full` roundedness. These should feel like small, smooth pebbles.
*   **Cards & Lists:** **Strictly forbid dividers.** Separate list items using `Spacing Scale 4` (1.4rem) or alternating tonal shifts between `surface-container` tiers.
*   **AI Pulse:** A custom component. A large, blurred gradient of `tertiary` and `primary` that sits behind the typography in the Hero section, pulsing slowly to indicate the AI is listening.

## 6. Do's and Don'ts

### Do:
*   **Do** use asymmetrical margins. Offsetting a text block to the right while a glass element floats on the left creates a "dynamic" editorial look.
*   **Do** embrace negative space. If a screen feels "empty," increase the typography size of the headline rather than adding more boxes.
*   **Do** use the `xl` (3rem) roundedness for large containers to maintain the "soft" character-inspired aesthetic.

### Don't:
*   **Don't** use 100% black. The `surface` (`#10131a`) is the floor; pure black kills the "glow" of the cyan and pink accents.
*   **Don't** use standard "Material" shadows. If you can see the edge of a shadow, it’s too dark.
*   **Don't** use more than one "glow" source per screen. Overusing the ethereal glow makes the interface look "cheap" rather than "high-tech."
*   **Don't** use rigid 90-degree corners. Even for "none" roundedness, ensure there is a subtle `0.5rem` (sm) soften to avoid a "cold" military feel.