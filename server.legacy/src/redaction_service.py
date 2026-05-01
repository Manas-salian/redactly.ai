# redaction_service.py
"""
Redaction Service Module

Orchestrates the PDF redaction pipeline:
1. Extract text from PDF (text layer + OCR)
2. Analyze text for PII using hybrid detection
3. Apply redactions to the PDF

Supports custom keywords with flexible matching modes
and toggleable Presidio entity types.
"""

import os
from typing import List, Optional
from src.ocr_redaction import print_contents, legal_redact_pdf
from src.model import analyze_text_from_string


def process_pdf_redaction(
    input_files: List[str],
    output_folder: str,
    method: str = 'full_redact',
    replace_text: str = '[REDACTED]',
    custom_keywords: Optional[List[str]] = None,
    match_mode: str = 'exact',
    fuzzy_threshold: int = 85,
    enabled_entities: Optional[List[str]] = None
) -> List[str]:
    """
    Process a batch of PDF files for redaction.

    Parameters:
        input_files: List of paths to input PDF files
        output_folder: Folder to store output files
        method: Redaction method ('full_redact', 'obfuscate', 'replace')
        replace_text: Text to use for replacement if method is 'replace'
        custom_keywords: Optional list of custom keywords/patterns to detect
        match_mode: Keyword matching mode - 'exact', 'fuzzy', or 'regex'
        fuzzy_threshold: Similarity threshold for fuzzy matching (0-100)
        enabled_entities: List of Presidio entity types to detect.
                         If None, all default entities are enabled.
                         Pass empty list [] to disable Presidio and use only keywords.

    Returns:
        List of paths to redacted PDF files
    """
    output_paths = []
    
    # Determine fuzzy threshold for PDF redaction
    # Only pass threshold if match_mode is fuzzy
    pdf_fuzzy_threshold = fuzzy_threshold if match_mode == 'fuzzy' else None

    for input_path in input_files:
        # Get the base filename
        base_filename = os.path.basename(input_path)
        file_id = os.path.splitext(base_filename)[0]

        # Define paths for intermediate and output files
        extracted_text_path = os.path.join(
            output_folder, f"{file_id}_extracted.txt")
        output_path = os.path.join(output_folder, f"{file_id}_redacted.pdf")

        # Step 1: Extract text from PDF
        print_contents(input_path, extracted_text_path)

        # Step 2: Analyze extracted text to identify PII
        try:
            with open(extracted_text_path, 'r', encoding='utf-8') as file:
                text_content = file.read()
            
            # Use enhanced analysis with custom keywords and entity filtering
            pii_terms = analyze_text_from_string(
                text=text_content,
                file_name=base_filename,
                custom_keywords=custom_keywords,
                match_mode=match_mode,
                fuzzy_threshold=fuzzy_threshold,
                enabled_entities=enabled_entities
            )
            
            # Log detection summary
            print(f"Total PII terms to redact in {base_filename}: {len(pii_terms)}")
            
        except Exception as e:
            raise Exception(f"Error analyzing text: {str(e)}")

        # Step 3: Perform redaction
        legal_redact_pdf(
            input_path,
            output_path,
            pii_terms=pii_terms,
            method=method,
            replace_text=replace_text,
            fuzzy_threshold=pdf_fuzzy_threshold
        )

        # Add to list of processed files
        output_paths.append(output_path)

        # Clean up the extracted text file
        if os.path.exists(extracted_text_path):
            os.remove(extracted_text_path)

    return output_paths
