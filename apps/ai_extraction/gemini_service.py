from google import genai
from google.genai import types
from django.conf import settings
import json
import logging
import re

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """
You are an expert contract analyst. Your job is to carefully read the ENTIRE contract document — including all pages, schedules, annexures, and commercial sections — and extract structured data.

IMPORTANT SCANNING RULES:
- The document may be an MSA (Master Service Agreement), SOW (Scope of Work), or both combined.
- Key information like dates, services, and costs may appear ANYWHERE: at the top, in the middle, or in schedules/annexures at the very end.
- ALWAYS scan the ENTIRE document before responding. Do NOT stop at the first page.
- For services: look for sections titled "Schedule", "Annexure", "Scope of Work", "Services", "Commercials", "Fees", "Pricing", "Payment Schedule" — these sections contain the actual service names, types, and costs.
- For dates: look for "Start Date", "End Date", "Effective Date", "Term", "Commencement", "Expiry" — these may appear in headings, bold text, tables, or body text.
- For client/vendor: look for "between X and Y", party definitions, signature blocks, letterheads.

SERVICE EXTRACTION RULES (very important):
- Extract EACH service as a separate entry in the services array.
- For service_name: use the specific service name like "GA4 Retainer (Web)", "GA4 360 License", "SEO Consulting", "Digital Marketing Retainer", "GA4 One-Time Implementation", etc.
- For service_type: classify as one of — "Retainer", "License", "Implementation", "Consulting", "Managed Services", "One-Time Project", "Analytics & Reporting", "SEO", "Paid Media", "Content", "Other"
- For value: extract the exact pricing string from the document, e.g. "INR 75,000/month", "INR 3,00,000 one-time", "INR 1,68,957/month". Include currency and frequency.
- For start_date / end_date on services: extract the specific start and end dates (YYYY-MM-DD) for EACH service if mentioned in schedules, annexures, scope tables, or terms.
- Contract-level start_date should be the earliest service start date, and contract-level end_date should be the latest service end date.

Return ONLY a valid JSON object. No markdown, no explanation, no code fences. Just raw JSON.

{
  "client_name": "Full legal name of the CLIENT (the company receiving services) — NOT the service provider. Look in party definitions, 'between X and Y', signature block.",
  "vendor_name": "Full legal name of the SERVICE PROVIDER / vendor. e.g. Logicserve Digital Consultancy Services Private Limited",
  "contract_name": "Descriptive name combining client + primary service, e.g. 'Aditya Birla Sun Life AMC - GA4 Retainer & SEO Agreement'",
  "service_name": "Primary/first service name from the document",
  "service_type": "Primary service type — Retainer / License / Implementation / Consulting / etc.",
  "start_date": "YYYY-MM-DD format. Earliest service start date (or overall contract start date).",
  "end_date": "YYYY-MM-DD format. Latest service end date (or overall contract end date).",
  "effective_date": "YYYY-MM-DD or null",
  "agreement_date": "YYYY-MM-DD or null — date the agreement was signed/executed",
  "scope_date": "YYYY-MM-DD or null",
  "schedule_date": "YYYY-MM-DD or null",
  "annexure_date": "YYYY-MM-DD or null",
  "execution_date": "YYYY-MM-DD or null",
  "lock_in_period": "e.g. '6 months', '1 year till 31st March 2025' or null",
  "renewal_clause": "full text of the renewal/auto-renewal clause or null",
  "notice_period": "e.g. '30 days', '90 days written notice' or null",
  "email_ids": ["all email addresses found verbatim in the document"],
  "services": [
    {
      "service_name": "Specific service name e.g. GA4 Retainer (Web), GA4 360 License, SEO Consulting",
      "service_type": "Retainer / License / Implementation / Consulting / SEO / Paid Media / Content / Other",
      "description": "Brief description of what this service covers or null",
      "start_date": "YYYY-MM-DD or null",
      "end_date": "YYYY-MM-DD or null",
      "value": "Exact pricing string from document e.g. INR 75,000/month or INR 3,00,000 one-time"
    }
  ],
  "confidence_score": 0.0,
  "extraction_notes": "List any fields that were unclear, missing, or assumed. Note if pricing was found in Schedule/Annexure."
}

COMMON DOCUMENT PATTERNS TO LOOK FOR:

Pattern 1 — SOW style (dates at top):
  "Start Date: 1st April 2024 - Retainer & GA4 360 License"
  → start_date: "2024-04-01", services[0].service_name: "GA4 Retainer (Web)", services[1].service_name: "GA4 360 License"

Pattern 2 — MSA style (dates in Term clause):
  "This Agreement will commence on 1st April 2025 and remain in force till 31st March 2027"
  → start_date: "2025-04-01", end_date: "2027-03-31"

Pattern 3 — Schedule/Annexure pricing:
  "Google Analytics Retainer | 1st June 2025 - 31st March 2027 | 30 hrs | INR 75,000/month"
  → services[].service_name: "GA4 Retainer (Web)", value: "INR 75,000/month"

Pattern 4 — Multiple services in one doc:
  "GA4 One-Time Implementation | INR 3,00,000 | One Time"
  "GA4 Retainer | INR 75,000/month | Monthly"
  "Firebase Analytics for Apps | INR 75,000/month | Monthly"
  → Extract ALL THREE as separate entries in the services array.

Pattern 5 — Client identification:
  "between Aditya Birla Sun Life AMC Limited ('Client') and Logicserve Digital ('Service Provider')"
  → client_name: "Aditya Birla Sun Life AMC Limited", vendor_name: "Logicserve Digital Consultancy Services Private Limited"
"""


def get_gemini_client():
    return genai.Client(api_key=settings.GEMINI_API_KEY)


def extract_contract_data_from_text(text: str) -> dict:
    """Extract structured contract data from text using Gemini."""
    client = get_gemini_client()

    # Send full document — truncate only if extremely long
    contract_text = text[:80000]
    prompt = EXTRACTION_PROMPT + f"\n\n--- FULL CONTRACT DOCUMENT ---\n{contract_text}\n--- END OF DOCUMENT ---"

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,  # Low temperature for factual extraction
                thinking_config=types.ThinkingConfig(thinking_budget=8000),
            ),
        )
        raw = response.text.strip()
        return _parse_json_response(raw)
    except Exception as exc:
        logger.error(f"Gemini text extraction error: {exc}")
        # Retry without thinking config
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
            )
            return _parse_json_response(response.text.strip())
        except Exception as exc2:
            return _empty_extraction(f"Gemini error: {str(exc2)}")


def extract_contract_data_from_image(image_bytes: bytes, mime_type: str) -> dict:
    """Extract structured contract data from an image using Gemini Vision."""
    client = get_gemini_client()
    image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[EXTRACTION_PROMPT, image_part],
        )
        raw = response.text.strip()
        return _parse_json_response(raw)
    except Exception as exc:
        logger.error(f"Gemini vision extraction error: {exc}")
        return _empty_extraction(f"Gemini Vision error: {str(exc)}")


def extract_contract_data_from_pdf(pdf_bytes: bytes) -> dict:
    """
    For PDFs: try native PDF → text extraction first.
    If scanned/image-based, render pages and send to Gemini Vision.
    For multi-page PDFs, send ALL pages to get complete context.
    """
    try:
        import fitz  # PyMuPDF

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        total_pages = len(doc)
        full_text = ""

        # Extract text from ALL pages (not just first 20)
        for page_num in range(total_pages):
            page = doc[page_num]
            page_text = page.get_text()
            full_text += f"\n--- PAGE {page_num + 1} ---\n{page_text}"

        # If we got meaningful text, use text-based extraction
        if len(full_text.strip()) > 300:
            logger.info(f"PDF has {total_pages} pages, {len(full_text)} chars of text. Using text extraction.")
            return extract_contract_data_from_text(full_text)
        else:
            # Scanned/image PDF — render pages and use Vision
            logger.info(f"PDF appears scanned ({len(full_text.strip())} chars). Using Vision extraction.")
            return _extract_from_scanned_pdf(doc)

    except ImportError:
        # PyMuPDF not available — send PDF bytes directly to Gemini
        logger.warning("PyMuPDF not available, sending PDF bytes directly to Gemini.")
        return extract_contract_data_from_image(pdf_bytes, "application/pdf")
    except Exception as exc:
        logger.error(f"PDF extraction error: {exc}")
        return _empty_extraction(f"PDF processing error: {str(exc)}")


def _extract_from_scanned_pdf(doc) -> dict:
    """Render ALL PDF pages to images and run Gemini Vision on them."""
    client = get_gemini_client()
    total_pages = len(doc)

    # Send up to 15 pages for scanned PDFs (Gemini can handle many images)
    max_pages = min(total_pages, 15)

    parts = [EXTRACTION_PROMPT + f"\n\nThis is a scanned PDF with {total_pages} pages. I am sending you {max_pages} pages as images. Read ALL of them carefully, including any schedules, annexures, or commercial sections at the end."]

    for page_num in range(max_pages):
        page = doc[page_num]
        mat = page.get_pixmap(dpi=180)  # Good quality, manageable size
        img_bytes = mat.tobytes("png")
        parts.append(types.Part.from_bytes(data=img_bytes, mime_type="image/png"))

    if len(parts) == 1:
        return _empty_extraction("No pages could be rendered.")

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=parts,
        )
        return _parse_json_response(response.text.strip())
    except Exception as exc:
        logger.error(f"Scanned PDF Gemini error: {exc}")
        return _tesseract_fallback(doc)


def _tesseract_fallback(doc) -> dict:
    """Last resort: Tesseract OCR on all PDF pages."""
    try:
        import pytesseract
        from PIL import Image
        import io

        full_text = ""
        for page_num in range(len(doc)):
            page = doc[page_num]
            mat = page.get_pixmap(dpi=300)
            img = Image.open(io.BytesIO(mat.tobytes("png")))
            text = pytesseract.image_to_string(img, lang="eng")
            full_text += f"\n--- PAGE {page_num + 1} ---\n{text}"

        if full_text.strip():
            return extract_contract_data_from_text(full_text)
        return _empty_extraction("Tesseract could not extract text.")
    except Exception as exc:
        logger.error(f"Tesseract fallback error: {exc}")
        return _empty_extraction(f"All OCR methods failed: {str(exc)}")


def _parse_json_response(raw: str) -> dict:
    """Parse and validate the JSON returned by Gemini."""
    # Strip markdown code fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE)
    raw = raw.strip()

    try:
        data = json.loads(raw)
        return _normalize_extracted_data(data)
    except json.JSONDecodeError as exc:
        logger.error(f"Failed to parse Gemini JSON: {exc}\nRaw: {raw[:500]}")
        return _empty_extraction("JSON parse error from AI response.")


def _normalize_extracted_data(data: dict) -> dict:
    """Normalize and validate extracted data."""
    date_fields = [
        "start_date", "end_date", "effective_date", "agreement_date",
        "scope_date", "schedule_date", "annexure_date", "execution_date",
    ]
    for field in date_fields:
        val = data.get(field)
        if val and not _is_valid_date(val):
            data[field] = None

    # Normalize services array
    services = data.get("services", [])
    normalized_services = []
    for s in services:
        if isinstance(s, dict) and s.get("service_name", "").strip():
            normalized_services.append({
                "service_name": s.get("service_name", ""),
                "service_type": s.get("service_type", ""),
                "description": s.get("description", ""),
                "start_date": s.get("start_date") if _is_valid_date(s.get("start_date", "")) else None,
                "end_date": s.get("end_date") if _is_valid_date(s.get("end_date", "")) else None,
                "value": s.get("value", ""),
            })
    data["services"] = normalized_services

    # Contract Start Date should automatically be the earliest service start date,
    # and Contract End Date should be the latest service end date.
    service_start_dates = [s["start_date"] for s in normalized_services if s.get("start_date")]
    service_end_dates = [s["end_date"] for s in normalized_services if s.get("end_date")]
    if service_start_dates:
        data["start_date"] = min(service_start_dates)
    if service_end_dates:
        data["end_date"] = max(service_end_dates)

    data.setdefault("email_ids", [])
    data.setdefault("confidence_score", 0.5)
    data.setdefault("extraction_notes", "")
    data["_extraction_success"] = True
    return data


def _is_valid_date(val) -> bool:
    if not val:
        return False
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}$", str(val).strip()))


def _empty_extraction(reason: str) -> dict:
    return {
        "client_name": None, "vendor_name": None, "contract_name": None,
        "service_name": None, "service_type": None,
        "start_date": None, "end_date": None, "effective_date": None,
        "agreement_date": None, "scope_date": None, "schedule_date": None,
        "annexure_date": None, "execution_date": None,
        "lock_in_period": None, "renewal_clause": None, "notice_period": None,
        "email_ids": [], "services": [],
        "confidence_score": 0.0,
        "extraction_notes": reason,
        "_extraction_success": False,
    }
