# processor/pdf_parser.py
"""
Multi-strategy PDF parser:
  1. PyMuPDF (fitz) for structured text + table extraction
  2. pdfplumber as fallback for complex tables
  3. LLM-assisted extraction for unstructured narrative sections
"""
import fitz  # PyMuPDF
import pdfplumber
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ExtractedSection:
    """A classified section of the PDF."""
    section_number: str
    section_title: str
    content_type: str           # 'text', 'table', 'mixed'
    raw_text: str
    tables: list[list[list[str]]] = field(default_factory=list)
    page_numbers: list[int] = field(default_factory=list)
    confidence: float = 1.0


@dataclass
class ExtractedDocument:
    """Complete parsed document."""
    file_path: str
    total_pages: int
    metadata: dict
    sections: list[ExtractedSection]
    raw_text: str


class ClinicalTrialPDFParser:
    """
    Extracts structured data from clinical trial PDFs.
    Uses section headers as landmarks for classification.
    """

    # Section patterns that match our generated PDFs
    # AND common ClinicalTrials.gov / protocol formats
    SECTION_PATTERNS = [
        (r'(?:^|\n)\s*(\d+\.)\s*(STUDY IDENTIFICATION)', 'identification'),
        (r'(?:^|\n)\s*(\d+\.)\s*(STUDY OVERVIEW)', 'overview'),
        (r'(?:^|\n)\s*(\d+\.)\s*(STUDY DESIGN)', 'design'),
        (r'(?:^|\n)\s*(\d+\.)\s*(ARMS AND INTERVENTIONS)', 'arms_interventions'),
        (r'(?:^|\n)\s*(\d+\.)\s*(ELIGIBILITY CRITERIA)', 'eligibility'),
        (r'(?:^|\n)\s*(\d+\.)\s*(OUTCOME MEASURES)', 'outcomes'),
        (r'(?:^|\n)\s*(\d+\.)\s*(STUDY LOCATIONS)', 'locations'),
        (r'(?:^|\n)\s*(\d+\.)\s*(ENROLLED PATIENT DATA)', 'patient_summary'),
        (r'PATIENT CASE REPORT:\s*([\w\-]+)', 'patient_detail'),
       # (r'ADDITIONAL ENROLLED PATIENTS', 'patient_table'),
    ]

    METADATA_PATTERNS = {
        'nct_id': r'(?:NCT\s*(?:Number|ID)?[:\s]*)(NCT\d{8})',
        'sponsor': r'(?:Sponsor|Lead Sponsor)[:\s]*(.+?)(?:\n|$)',
        'phase': r'(?:Phase)[:\s]*(Phase\s+[\d/]+|N/A)',
        'status': r'(?:Status|Overall Status)[:\s]*(.+?)(?:\n|$)',
        'therapeutic_area': r'(?:Therapeutic Area)[:\s]*(.+?)(?:\n|$)',
        'enrollment': r'(?:Enrollment)[:\s]*(\d+)',
    }

    def parse(self, pdf_path: str) -> ExtractedDocument:
        """
        Parse a clinical trial PDF into structured sections.
        """
        path = Path(pdf_path)
        if not path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        # ── Step 1: Extract raw text with PyMuPDF ──
        doc = fitz.open(pdf_path)
        raw_text = ""
        page_texts = {}
        for page_num in range(doc.page_count):
            page = doc[page_num]
            text = page.get_text("text")
            page_texts[page_num] = text
            raw_text += f"\n--- PAGE {page_num + 1} ---\n{text}"

        # ── Step 2: Extract tables with pdfplumber ──
        tables_by_page = {}
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages):
                tables = page.extract_tables()
                if tables:
                    tables_by_page[page_num] = tables

        # ── Step 3: Extract metadata from title page ──
        metadata = self._extract_metadata(raw_text)

        # ── Step 4: Split into sections ──
        sections = self._split_into_sections(raw_text, page_texts, tables_by_page)

        doc.close()

        return ExtractedDocument(
            file_path=pdf_path,
            total_pages=len(page_texts),
            metadata=metadata,
            sections=sections,
            raw_text=raw_text
        )

    def _extract_metadata(self, text: str) -> dict:
        """Extract key metadata using regex patterns."""
        metadata = {}
        for key, pattern in self.METADATA_PATTERNS.items():
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                metadata[key] = match.group(1).strip()
        return metadata

    def _split_into_sections(
        self,
        raw_text: str,
        page_texts: dict,
        tables_by_page: dict
    ) -> list[ExtractedSection]:
        """Split raw text into classified sections."""
        sections = []
        section_positions = []

        # Find all section boundaries
        for pattern, section_type in self.SECTION_PATTERNS:
            for match in re.finditer(pattern, raw_text, re.IGNORECASE):

                # ══════════════════════════════════════════════
                # ✅ FIX: Safely extract the section number
                #    match.lastindex is None when the regex
                #    has no capture groups (e.g., the
                #    'ADDITIONAL ENROLLED PATIENTS' pattern)
                # ══════════════════════════════════════════════
                section_number = ""
                if match.lastindex is not None and match.lastindex >= 1:
                    section_number = match.group(1)

                section_positions.append({
                    'start': match.start(),
                    'end': match.end(),
                    'type': section_type,
                    'title': match.group(0).strip(),
                    'number': section_number
                })

        # Sort by position
        section_positions.sort(key=lambda x: x['start'])

        # Extract content between section boundaries
        for i, sec in enumerate(section_positions):
            start = sec['end']
            end = (section_positions[i + 1]['start']
                   if i + 1 < len(section_positions)
                   else len(raw_text))
            content = raw_text[start:end].strip()

            # Find which pages this section spans
            pages = self._find_pages_for_range(
                sec['start'], end, page_texts
            )

            # Collect tables from those pages
            section_tables = []
            for page_num in pages:
                if page_num in tables_by_page:
                    section_tables.extend(tables_by_page[page_num])

            content_type = 'mixed' if section_tables else 'text'
            if not content and section_tables:
                content_type = 'table'

            sections.append(ExtractedSection(
                section_number=sec.get('number', ''),
                section_title=sec['title'],
                content_type=content_type,
                raw_text=content,
                tables=section_tables,
                page_numbers=pages
            ))

        return sections

    def _find_pages_for_range(
        self, start: int, end: int, page_texts: dict
    ) -> list[int]:
        """Determine which pages a text range spans."""
        pages = []
        cumulative = 0
        for page_num in sorted(page_texts.keys()):
            page_len = len(page_texts[page_num]) + 20  # separator overhead
            if cumulative + page_len >= start and cumulative <= end:
                pages.append(page_num)
            cumulative += page_len
        return pages