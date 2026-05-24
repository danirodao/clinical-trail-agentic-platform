---
name: slide-deck-builder
description: Generate visual slide decks with rich components
triggers:
  - make a slide deck
  - create slides
  - build a presentation
  - generate slides
---

# Slide Deck Builder

Generate beautiful, self-contained HTML slide decks with rich visual components. No PowerPoint, no Google Slides, no dependencies. Just open the HTML file in a browser and present.

## How It Works

When you say "make a slide deck about [topic]" or "create slides for [presentation]", this skill builds a complete HTML file with fullscreen slides, keyboard navigation, and polished visual components.

## Slide Structure

Every deck follows this proven flow:

| Slide | Purpose | Content |
|-------|---------|---------|
| **1. Title** | Set the stage | Big title, subtitle, your name or brand |
| **2. Setup** | Frame the problem or context | Why this matters, what the audience will learn |
| **3-8. Core Content** | Deliver the meat | One idea per slide, each with a visual component |
| **9. Evidence** | Prove your point | Stats, quotes, case studies, examples |
| **10. Takeaway** | Land the message | Summary, call to action, next steps |

Adjust the number of core content slides based on topic complexity. Simple topics: 5-7 total slides. Complex topics: 10-15 slides. Never exceed 15 slides.

## Visual Components

Every single slide MUST have a visual component. Text-only slides are not allowed. Choose from:

### Card Grid
2-4 cards in a row, each with an icon/emoji, title, and one-line description. Use for: features, benefits, categories.

### Comparison Panel
Two columns side by side with a vs divider. Use for: old vs new, tool A vs tool B, before vs after.

### Stat Callout
One huge number or percentage with a short label below. Use for: impressive data points, growth numbers, costs.

### Step Flow
Horizontal or vertical numbered steps with arrows between them. Use for: processes, tutorials, how-it-works.

### Quote Block
Large quotation marks, italic text, attribution below. Use for: testimonials, expert quotes, key statements.

### Icon + Label List
Vertical list with emoji or icons on the left, labels on the right. Use for: feature lists, agendas, checklists.

### Code Block
Monospace text on a dark card with syntax-style coloring. Use for: code examples, command line, config files.

### Timeline
Horizontal line with dots and labels above and below. Use for: history, roadmap, project phases.

### Image + Caption
Centered image placeholder with a caption below. Use for: screenshots, diagrams, product photos.

### Metric Dashboard
3-4 stat boxes in a row, each with a label and value. Use for: KPIs, performance data, comparisons.

## HTML Output

Generate a single self-contained HTML file. No external fonts, CDN links, or images. Everything inline.

**Layout:** Fullscreen slides (100vw x 100vh), content centered, max-width 900px, 60px padding.

**Colors:** Background `#0a0a0a`, text `#ffffff`, secondary `#a0a0a0`, cards `#1a1a1a`, borders `#2a2a2a`, plus one accent color per deck.

**Typography:** Headings bold sans-serif 48-64px, body 24-28px, stat numbers 72-96px in accent color, code monospace 18px.

**Navigation:** Arrow keys to move slides, counter in bottom-right, smooth transitions, optional click-to-advance.

## Content Rules

1. **One idea per slide.** If you have two points, make two slides
2. **Max 30 words per slide.** Slides are visual aids, not documents
3. **Every slide has a visual component.** No exceptions. See the list above
4. **Consistent accent color.** Pick one accent and use it for all highlights, buttons, and emphasis
5. **No bullet point dumps.** If you need bullets, use an Icon + Label List component instead
6. **Big text beats small text.** When in doubt, make it bigger
7. **Whitespace is your friend.** Crowded slides lose the audience

## Accent Color Selection

Pick an accent color based on the topic:

| Topic Area | Suggested Accent | Hex |
|-----------|-----------------|-----|
| AI / Tech | Electric blue | `#3B82F6` |
| Business | Warm amber | `#F59E0B` |
| Design | Coral pink | `#F43F5E` |
| Finance | Emerald green | `#10B981` |
| Health | Soft teal | `#14B8A6` |
| Education | Royal purple | `#8B5CF6` |
| Marketing | Vibrant orange | `#F97316` |
| General | Clean white accent on dark | `#E5E5E5` |

## Example Usage

- "Make a slide deck about how AI agents work"
- "Create slides for my team meeting on Q1 results"
- "Build a presentation explaining our new product"
- "Generate slides for a 5-minute talk on prompt engineering"

## Tips for Best Results

- Tell the skill how many slides you want and it will adjust density
- Mention your audience so the content level is calibrated
- Share your brand color and it becomes the accent
- Ask for speaker notes if you want talking points alongside each slide
- The HTML file works offline -- no internet needed to present
