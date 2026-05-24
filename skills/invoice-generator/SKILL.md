---
name: invoice-generator
description: Generate a professional PDF invoice from a few details. Use this skill when the user says "create an invoice", "invoice this client", "bill them", "send an invoice", "generate invoice", "make an invoice for", or any variation of wanting to bill someone for work done.
triggers:
  - create an invoice
  - invoice this client
  - bill them
  - generate invoice
  - make an invoice
---

# Invoice Generator

Create a clean, professional invoice as a PDF from a few simple inputs. No templates needed, no software to install. Just tell it who to bill, what for, and how much.

## Instructions

### Step 1: Gather Invoice Details

From the user's request, identify:

- **Client name** — Who is being billed?
- **Client email or address** — Optional but include if provided
- **Your business name** — Pull from CLAUDE.md if available
- **Invoice number** — Auto-generate if not provided (format: INV-YYYY-001)
- **Date** — Today's date unless specified
- **Due date** — Default to Net 30 unless specified
- **Line items** — What services or products are being billed?
- **Amounts** — Price per item or flat rate
- **Tax rate** — Only include if the user mentions tax
- **Payment instructions** — Bank details, PayPal, Venmo, etc. Pull from CLAUDE.md or ask.

If you have enough to build the invoice, build it. Only ask if you're missing the client name or the amounts.

### Step 2: Calculate Totals

For each line item:
- Quantity x Rate = Line Total

Then:
- Subtotal = Sum of all line totals
- Tax = Subtotal x Tax Rate (if applicable)
- **Total Due** = Subtotal + Tax

Double-check your math. Invoices with wrong totals destroy credibility.

### Step 3: Build the Invoice

Use clean, professional formatting:

```
[Your Business Name]
[Your Address / Contact — if available]

INVOICE

Invoice #: [Number]
Date: [Issue Date]
Due: [Due Date]
Bill To: [Client Name / Company]

---

| Description              | Qty | Rate     | Amount   |
|--------------------------|-----|----------|----------|
| [Service/Product]        | [X] | $[X.XX]  | $[X.XX]  |
| [Service/Product]        | [X] | $[X.XX]  | $[X.XX]  |

---

                                    Subtotal: $[X.XX]
                                    Tax (X%): $[X.XX]
                                    TOTAL DUE: $[X.XX]

Payment Terms: [Net 30 / Due on Receipt / etc.]
Payment Method: [Bank transfer / PayPal / Venmo / etc.]

[Payment details if provided]

Thank you for your business.
```

### Step 4: Generate the PDF

Use Python with reportlab or fpdf to generate a properly formatted PDF invoice. Save it to the user's workspace folder.

Name the file: `Invoice-[ClientName]-[Number].pdf`

### Step 5: Deliver

Provide the file link and a one-line summary:
- Invoice total
- Due date
- Client name

Offer: "Want me to adjust anything, add notes, or create a recurring template for this client?"

## Rules

- Always double-check math. Subtotals and totals must be correct.
- Keep it clean and professional. No decorative clutter.
- Use the user's business name and details from CLAUDE.md when available.
- Default to USD unless the user specifies another currency.
- Never fabricate payment details. If you don't have them, leave a placeholder.
