# model.py
"""
PII Analysis Module

Provides backward-compatible PII analysis functions while integrating
the new hybrid detection system for flexible keyword matching.
"""

from typing import List, Optional, Dict, Any
from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer

# Import the hybrid detector for advanced matching
from src.hybrid_detector import (
    HybridPIIDetector,
    MatchMode,
    DetectionResult,
    get_available_entity_types,
    DEFAULT_ENTITY_TYPES
)


def analyze_text_from_string(
    text: str,
    file_name: str = "Uploaded text",
    custom_keywords: Optional[List[str]] = None,
    match_mode: str = "exact",
    fuzzy_threshold: int = 85,
    enabled_entities: Optional[List[str]] = None
) -> List[str]:
    """
    Analyze text content for PII using hybrid detection.
    
    This function maintains backward compatibility with the original API
    while adding support for custom keywords and flexible matching modes.
    
    Args:
        text: The text content to analyze
        file_name: Name of the source file (for logging)
        custom_keywords: Optional list of custom keywords/patterns to detect
        match_mode: Keyword matching mode - "exact", "fuzzy", or "regex"
        fuzzy_threshold: Similarity threshold for fuzzy matching (0-100)
        enabled_entities: List of Presidio entity types to detect.
                         If None, all default entities are enabled.
                         Pass empty list [] to disable Presidio detection.
    
    Returns:
        List of unique PII terms found in the text
    """
    # Convert match_mode string to enum
    mode_map = {
        "exact": MatchMode.EXACT,
        "fuzzy": MatchMode.FUZZY,
        "regex": MatchMode.REGEX
    }
    mode = mode_map.get(match_mode.lower(), MatchMode.EXACT)
    
    # Create detector instance
    detector = HybridPIIDetector(enabled_entities=enabled_entities)
    
    # Run detection
    results = detector.detect(
        text=text,
        custom_keywords=custom_keywords,
        match_mode=mode,
        fuzzy_threshold=fuzzy_threshold,
        enabled_entities=enabled_entities
    )
    
    # Extract unique terms
    output_list = detector.get_unique_terms(results)
    
    # Log results
    print(f"Identified PII in {file_name}: {output_list}")
    if custom_keywords:
        print(f"  Custom keywords searched ({match_mode}): {custom_keywords}")
    if enabled_entities is not None:
        print(f"  Presidio entities enabled: {enabled_entities if enabled_entities else 'None'}")
    
    return output_list


def analyze_text_with_positions(
    text: str,
    custom_keywords: Optional[List[str]] = None,
    match_mode: str = "exact",
    fuzzy_threshold: int = 85,
    enabled_entities: Optional[List[str]] = None
) -> List[DetectionResult]:
    """
    Analyze text and return detection results with position information.
    
    This is useful when you need to know exactly where each PII entity
    was found in the text.
    
    Args:
        text: The text content to analyze
        custom_keywords: Optional list of custom keywords/patterns
        match_mode: Keyword matching mode - "exact", "fuzzy", or "regex"
        fuzzy_threshold: Similarity threshold for fuzzy matching (0-100)
        enabled_entities: List of Presidio entity types to detect
    
    Returns:
        List of DetectionResult objects with full position/score info
    """
    mode_map = {
        "exact": MatchMode.EXACT,
        "fuzzy": MatchMode.FUZZY,
        "regex": MatchMode.REGEX
    }
    mode = mode_map.get(match_mode.lower(), MatchMode.EXACT)
    
    detector = HybridPIIDetector(enabled_entities=enabled_entities)
    
    return detector.detect(
        text=text,
        custom_keywords=custom_keywords,
        match_mode=mode,
        fuzzy_threshold=fuzzy_threshold,
        enabled_entities=enabled_entities
    )


def get_entity_types() -> Dict[str, str]:
    """
    Get available Presidio entity types with display labels.
    
    Returns:
        Dictionary mapping entity type codes to human-readable labels
    """
    return get_available_entity_types()


def get_default_entities() -> List[str]:
    """
    Get list of default enabled entity types.
    
    Returns:
        List of entity type codes
    """
    return DEFAULT_ENTITY_TYPES.copy()


# Legacy function for backward compatibility
def analyze_single_file(file_path: str = "extracted.txt") -> List[str]:
    """
    Handle file analysis for a single file on a local machine.
    
    This function is kept for backward compatibility with existing code.
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            text_content = file.read()
        output_list = analyze_text_from_string(text_content, file_path)
        return output_list
    except Exception as e:
        print(f"Error analyzing file: {e}")
        return []
