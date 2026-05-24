---
name: budget-dashboard
description: Generate an interactive financial dashboard as a self-contained HTML file with Apple Swiss design. Includes sliders to adjust income and expenses in real-time, donut chart breakdown, 12-month savings projection with investment scenarios, milestone tracker, and benchmark comparisons. Use this skill when the user says "budget dashboard", "financial dashboard", "interactive budget", "budget app", "visualize my budget", "budget planner", "where's my money going", "savings dashboard", or any variation of wanting to see their finances visually.
triggers:
  - budget dashboard
  - financial dashboard
  - interactive budget
  - budget app
  - visualize my budget
  - budget planner
  - help me budget
  - where's my money going
  - savings dashboard
  - see my finances
---

# Budget Dashboard

Build an interactive, self-contained HTML financial dashboard with an **Apple Swiss design aesthetic** — clean white backgrounds, SF-style typography (use Instrument Sans + Newsreader from Google Fonts), generous whitespace, soft shadows, pill-shaped buttons, and Apple system colors (#34c759 green, #007aff blue, #ff2d55 pink, #ff9500 orange, #af52de purple).

## What It Delivers

A single HTML file the user can open in any browser. Everything updates in real-time as they drag sliders. No backend, no dependencies beyond Chart.js from CDN.

## Features Required

1. **Top stat cards** (4 across) — Income, Expenses, Savings, Savings Rate — each with an icon, the value, and a subtitle
2. **Slider panel** — Sliders for income and each expense category. Each slider shows current value and range. All sliders fire `update()` on input.
3. **Donut chart** — Shows expense breakdown + savings as the largest slice. Center text shows savings rate %. Uses Chart.js doughnut.
4. **Legend** — Color-coded list beside the donut with category names, dollar amounts, and percentages.
5. **12-month projection chart** — Line chart (Chart.js) with 3 scenario toggle buttons: "Just Saving" (0% return), "High-Yield Savings" (4.5% APY), "Invested" (7% annual). Chart color changes per scenario.
6. **Milestones** — Progress bars toward: Emergency Fund 3mo, Emergency Fund 6mo, $100K, $250K. Each shows ETA in months/years.
7. **Benchmark comparisons** — Housing, Food, Savings, Wants vs. standard guidelines (50/30/20 rule). Visual bar with guideline marker.
8. **Insight box** — Dynamic text that changes based on savings rate (different messages for <20%, 20-50%, 50%+, and negative).

## Instructions

### Step 1: Gather the Numbers

Ask for (or help estimate):

**For Personal:**
- Total monthly income (after tax)
- Fixed expenses (rent, utilities, insurance, subscriptions, loan payments)
- Variable expenses (food, gas, entertainment, shopping)
- Current savings rate
- Any debt payments
- Financial goals

**For Business:**
- Monthly revenue
- Fixed costs (rent, software, salaries, insurance)
- Variable costs (marketing, contractors, supplies, travel)
- Growth investments
- Tax set-aside percentage
- Profit targets

### Step 2: Build the Dashboard

Generate a self-contained HTML file with all CSS inline (in a `<style>` tag) and all JS inline (in a `<script>` tag). Import Chart.js from CDN:

```html
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
```

Import fonts from Google Fonts:
```html
<link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600;700&family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;0,6..72,600&display=swap" rel="stylesheet">
```

### Step 3: Design Rules (Apple Swiss)

- **Background**: #f5f5f7 (Apple light gray)
- **Cards**: #ffffff with border-radius: 20px, soft shadow (0 1px 3px rgba(0,0,0,0.04), 0 4px 12px rgba(0,0,0,0.04))
- **Typography**: Instrument Sans for body/UI, Newsreader (serif) for large numbers and headings
- **Colors**: Use Apple system colors — green #34c759, blue #007aff, pink #ff2d55, orange #ff9500, purple #af52de, teal #5ac8fa
- **Sliders**: Clean white thumbs with blue border, thin 4px tracks
- **Buttons**: Pill-shaped (border-radius: 100px), active state fills with blue
- **Spacing**: Generous — 24-28px card padding, 16px grid gaps
- **Animations**: Subtle fadeIn on load with staggered delays
- **Hover states**: Cards lift slightly with enhanced shadow
- **No dark mode** — keep it light and clean

### Step 4: Populate with User's Numbers

Set the slider default values and state object to match the user's actual income and expense numbers. All sliders should have sensible min/max ranges (e.g., income 2k-30k, rent 0-5k).

### Step 5: Save and Deliver

Save as: `financial-dashboard.html`

The file should be completely self-contained — opening it in any browser should display the full interactive dashboard with no other dependencies.

### Step 6: Verify

Double-check:
- All math adds up (income - expenses = savings)
- Donut chart percentages total 100%
- Projection chart uses correct compound interest formulas
- Milestone ETAs are calculated correctly
- All sliders update everything in real-time

## Rules

- Never give specific investment advice. Budget visualization is fine. "Put your money in X stock" is not.
- Include a small footer disclaimer that this is a planning tool, not financial advice.
- Always double-check the math. Budget totals must add up correctly.
- If the user shares sensitive financial details, handle them with care and don't store them in memory.
- Round to whole dollars.
