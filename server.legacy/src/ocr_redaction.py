# ocr_redaction.py
"""
OCR and PDF Redaction Module

Handles text extraction from PDFs (both text layer and OCR from images)
and applies redactions with accurate position detection.

Supports:
- Native PDF text layer extraction
- OCR for scanned documents/embedded images  
- Fuzzy matching for typo-tolerant redaction
- Character-level position detection as fallback
"""

import fitz
import pytesseract
import cv2
import io
import re
import numpy as np
from PIL import Image
from typing import List, Optional, Tuple, Dict, Any

# Import RapidFuzz for fuzzy matching in OCR results
try:
    from rapidfuzz import fuzz
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False
    print("Warning: rapidfuzz not installed. Fuzzy matching in OCR disabled.")


def print_contents(input_path, output_txt_path):
    """
    Extract all text content from a PDF and write it to a text file.

    Parameters:
        input_path: Path to the input PDF file
        output_txt_path: Path where the extracted text will be saved
    """
    doc = fitz.open(input_path)
    all_text = []

    # Extract text from document text layer
    for page_num, page in enumerate(doc):
        # Use "text" mode for better text extraction
        text = page.get_text("text")
        if text.strip():  # Only add non-empty text
            all_text.append(f"--- Page {page_num + 1} ---\n{text}\n")

        # Also extract text from images using OCR
        for img in page.get_images(full=True):
            xref = img[0]
            base_image = doc.extract_image(xref)
            img_bytes = base_image["image"]

            # Convert image bytes to format suitable for OCR
            image = Image.open(io.BytesIO(img_bytes)).convert("RGB")
            open_cv_image = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)

            # Perform OCR on the image
            ocr_text = perform_ocr(open_cv_image)
            if ocr_text.strip():  # Only add non-empty OCR text
                all_text.append(
                    f"--- Image OCR on Page {page_num + 1} ---\n{ocr_text}\n")

    # Write all extracted text to the output file
    with open(output_txt_path, 'w', encoding='utf-8') as f:
        f.write("".join(all_text))

    print(f"Extracted text saved to {output_txt_path}")
    doc.close()


def ocr_from_pdf(pdf_path):
    doc = fitz.open(pdf_path)
    all_text = ""

    for i, page in enumerate(doc):
        pix = page.get_pixmap(dpi=300)

        img = Image.open(io.BytesIO(pix.tobytes("png")))

        text = pytesseract.image_to_string(img)
        all_text += f"\n--- Page {i + 1} ---\n{text}"

    return all_text


def perform_ocr(image):
    """
    Perform OCR on an image and return the extracted text.

    Parameters:
        image: The image to process (OpenCV format).

    Returns:
        Extracted text as a string.
    """
    custom_config = r'--oem 3 --psm 6 -c preserve_interword_spaces=1'
    ocr_text = pytesseract.image_to_string(image, config=custom_config)
    return ocr_text


def search_text_with_fallback(
    page: fitz.Page,
    term: str,
    fuzzy_threshold: Optional[int] = None
) -> List[fitz.Rect]:
    """
    Search for text in a PDF page with fallback strategies.
    
    First tries PyMuPDF's native search, then falls back to
    character-level search for better accuracy with complex layouts.
    
    Args:
        page: PyMuPDF page object
        term: Text to search for
        fuzzy_threshold: If set, enable fuzzy matching (0-100)
        
    Returns:
        List of fitz.Rect objects representing match positions
    """
    # Strategy 1: Native PyMuPDF search (fastest, handles most cases)
    areas = page.search_for(term)
    if areas:
        return areas
    
    # Strategy 2: Case-insensitive search
    areas = page.search_for(term.lower())
    if areas:
        return areas
    areas = page.search_for(term.upper())
    if areas:
        return areas
    
    # Strategy 3: Character-level search using text dictionary
    if not areas:
        areas = _character_level_search(page, term)
        if areas:
            return areas
    
    # Strategy 4: Fuzzy matching (if enabled and RapidFuzz available)
    if fuzzy_threshold and RAPIDFUZZ_AVAILABLE:
        areas = _fuzzy_search_in_page(page, term, fuzzy_threshold)
        if areas:
            return areas
    
    return []


def _character_level_search(
    page: fitz.Page,
    term: str
) -> List[fitz.Rect]:
    """
    Search for text using character-level position mapping.
    Useful for text split across spans or with unusual formatting.
    """
    positions = []
    term_lower = term.lower()
    
    try:
        text_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
        
        for block in text_dict.get("blocks", []):
            if "lines" not in block:
                continue
            
            for line in block["lines"]:
                # Concatenate all spans in the line
                line_text = ""
                span_info = []  # [(start_idx, end_idx, bbox), ...]
                
                for span in line["spans"]:
                    span_text = span["text"]
                    span_start = len(line_text)
                    span_end = span_start + len(span_text)
                    span_info.append((span_start, span_end, span["bbox"]))
                    line_text += span_text
                
                # Search for term in concatenated line text
                line_lower = line_text.lower()
                idx = 0
                while True:
                    found_idx = line_lower.find(term_lower, idx)
                    if found_idx == -1:
                        break
                    
                    # Find bounding box for the match
                    match_end = found_idx + len(term)
                    rect = _get_rect_for_range(span_info, found_idx, match_end)
                    if rect:
                        positions.append(rect)
                    
                    idx = found_idx + 1
                    
    except Exception as e:
        print(f"Character-level search error: {e}")
    
    return positions


def _get_rect_for_range(
    span_info: List[Tuple[int, int, Tuple[float, float, float, float]]],
    start: int,
    end: int
) -> Optional[fitz.Rect]:
    """Get bounding rectangle for a character range across spans"""
    rects = []
    
    for span_start, span_end, bbox in span_info:
        # Check if this span overlaps with our range
        if span_end > start and span_start < end:
            rects.append(fitz.Rect(bbox))
    
    if not rects:
        return None
    
    # Union all overlapping span rectangles
    result = rects[0]
    for rect in rects[1:]:
        result = result | rect  # Union operation
    
    return result


def _fuzzy_search_in_page(
    page: fitz.Page,
    term: str,
    threshold: int
) -> List[fitz.Rect]:
    """
    Fuzzy search for a term in the page using RapidFuzz.
    Returns positions of words that match within the threshold.
    """
    positions = []
    
    try:
        text_dict = page.get_text("dict")
        
        for block in text_dict.get("blocks", []):
            if "lines" not in block:
                continue
            
            for line in block["lines"]:
                for span in line["spans"]:
                    words = span["text"].split()
                    bbox = span["bbox"]
                    
                    # Check each word in the span
                    for word in words:
                        score = fuzz.ratio(word.lower(), term.lower())
                        if score >= threshold:
                            # For simplicity, use the whole span bbox
                            # More precise would be to calculate word position
                            positions.append(fitz.Rect(bbox))
                            break
                            
    except Exception as e:
        print(f"Fuzzy search error: {e}")
    
    return positions


def legal_redact_pdf(input_path, output_path, pii_terms=None,
                     method="full_redact", replace_text="[REDACTED]",
                     fuzzy_threshold: Optional[int] = None):
    """
    Redact sensitive information from a PDF.

    Parameters:
        input_path: Path to the input PDF file
        output_path: Path where the redacted PDF will be saved
        pii_terms: List of terms to redact (sensitive information)
        method: "full_redact" (remove text), "obfuscate" (black box), 
                "replace" (text substitution)
        replace_text: Text to insert if method="replace"
        fuzzy_threshold: If set, enable fuzzy matching for text search (0-100)
    """
    if pii_terms is None:
        pii_terms = []

    doc = fitz.open(input_path)

    # Process all pages first
    for page_num, page in enumerate(doc):
        # --- TEXT LAYER PROCESSING ---
        for term in pii_terms:
            # Use enhanced search with fallback strategies
            areas = search_text_with_fallback(page, term, fuzzy_threshold)

            for rect in areas:
                if method == "replace":
                    page.add_redact_annot(rect, text=replace_text)
                elif method == "obfuscate":
                    page.add_redact_annot(rect, fill=(0, 0, 0))
                else:  # full legal redaction
                    page.add_redact_annot(rect, text="")

        # Apply text redactions
        page.apply_redactions()

    # --- IMAGE LAYER PROCESSING --- (separate pass to avoid xref conflicts)
    for page_num, page in enumerate(doc):
        for img in page.get_images(full=True):
            xref = img[0]

            try:
                base_image = doc.extract_image(xref)
                if base_image:
                    img_bytes = base_image["image"]
                    pix = fitz.Pixmap(img_bytes)

                    # Process image (assuming process_image_with_ocr is defined elsewhere)
                    # This function should return image bytes after OCR and redaction
                    if callable(process_image_with_ocr):
                        processed_bytes = process_image_with_ocr(
                            img_bytes,
                            pii_terms,
                            method=method,
                            replace_text=replace_text,
                            fuzzy_threshold=fuzzy_threshold
                        )

                        # Create a new pixmap from processed bytes
                        new_pix = fitz.Pixmap(processed_bytes)

                        # Replace the image - safer approach
                        doc.delete_image(xref)  # Remove old image
                        new_xref = doc.add_image_ref(
                            new_pix.tobytes())  # Add new image

                        # Update the image reference in the page
                        page.replace_image(xref, new_xref)
            except Exception as e:
                print(f"Error processing image on page {page_num+1}: {str(e)}")
                continue

    # Remove metadata and sensitive tags
    doc.set_metadata({})

    # PyMuPDF 1.18.0+ uses different methods for XML metadata
    try:
        doc.del_xml_metadata()  # Newer versions
    except AttributeError:
        try:
            doc.setMetadata({})  # Older versions alternative
        except:
            pass

    # Save with security settings
    doc.save(output_path,
             deflate=True,
             garbage=4,  # Maximum cleanup of unused objects
             clean=True)  # Sanitize content
    doc.close()


def process_image_with_ocr(img_bytes, pii_terms, method, replace_text,
                           fuzzy_threshold: Optional[int] = None):
    """
    Process image with OCR and redaction.
    
    Args:
        img_bytes: Image data as bytes
        pii_terms: List of PII terms to redact
        method: Redaction method (full_redact, obfuscate, replace)
        replace_text: Replacement text for 'replace' method
        fuzzy_threshold: If set, enable fuzzy matching (0-100)
    """
    image = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    open_cv_image = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)

    # Enhanced OCR processing
    custom_config = r'--oem 3 --psm 6 -c preserve_interword_spaces=1'
    ocr_result = pytesseract.image_to_data(
        open_cv_image,
        config=custom_config,
        output_type=pytesseract.Output.DICT
    )

    for i in range(len(ocr_result['text'])):
        text = ocr_result['text'][i].strip()
        if not text:
            continue
            
        # Check if this word matches any PII term
        is_match = False
        
        for term in pii_terms:
            # Exact match (case-insensitive)
            if term.lower() in text.lower():
                is_match = True
                break
            
            # Fuzzy match if threshold is set
            if fuzzy_threshold and RAPIDFUZZ_AVAILABLE:
                score = fuzz.ratio(text.lower(), term.lower())
                if score >= fuzzy_threshold:
                    is_match = True
                    break
                    
                # Also check partial ratio for longer terms
                if len(term) > 4:
                    partial_score = fuzz.partial_ratio(term.lower(), text.lower())
                    if partial_score >= fuzzy_threshold:
                        is_match = True
                        break
        
        if is_match:
            x, y, w, h = (
                ocr_result['left'][i],
                ocr_result['top'][i],
                ocr_result['width'][i],
                ocr_result['height'][i]
            )

            if method == "replace":
                # White background + new text
                cv2.rectangle(open_cv_image, (x, y),
                              (x+w, y+h), (255, 255, 255), -1)
                cv2.putText(
                    open_cv_image,
                    replace_text,
                    (x, y+h//2),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (0, 0, 0),
                    1
                )
            elif method == "obfuscate":
                cv2.rectangle(open_cv_image, (x, y), (x+w, y+h), (0, 0, 0), -1)
            else:  # full redaction
                cv2.rectangle(open_cv_image, (x, y),
                              (x+w, y+h), (255, 255, 255), -1)

    # Convert back to bytes
    _, img_encoded = cv2.imencode('.png', open_cv_image)
    return img_encoded.tobytes()
