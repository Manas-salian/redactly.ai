# hybrid_detector.py
"""
Hybrid PII Detection Module

Combines multiple detection strategies for flexible, privacy-first PII redaction:
1. Rule-based: Presidio with regex patterns and deny-lists
2. Fuzzy matching: RapidFuzz for OCR errors and typos  
3. Exact matching: Case-insensitive literal matching
4. Regex patterns: User-defined regex for advanced matching
"""

import re
from typing import List, Optional, Dict, Any, Literal
from dataclasses import dataclass
from enum import Enum

from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
from presidio_analyzer.nlp_engine import NlpEngineProvider
from rapidfuzz import fuzz, process


class MatchMode(Enum):
    """Supported keyword matching modes"""
    EXACT = "exact"           # Case-insensitive exact match
    FUZZY = "fuzzy"           # Fuzzy matching with configurable threshold
    REGEX = "regex"           # User-provided regex patterns


@dataclass
class DetectionResult:
    """Represents a detected PII entity with position info"""
    text: str                  # The matched text
    entity_type: str           # Type of entity (PERSON, CUSTOM_KEYWORD, etc.)
    start: int                 # Start position in text
    end: int                   # End position in text
    score: float               # Confidence score (0.0 - 1.0)
    match_mode: str            # How it was detected (presidio, exact, fuzzy, regex)


# Default Presidio entity types that can be toggled
DEFAULT_ENTITY_TYPES = [
    "PERSON",
    "EMAIL_ADDRESS", 
    "PHONE_NUMBER",
    "CREDIT_CARD",
    "US_SSN",
    "US_PASSPORT",
    "LOCATION",
    "DATE_TIME",
    "IP_ADDRESS",
    "IBAN_CODE",
    "NRP",  # National Registration Number
    "MEDICAL_LICENSE",
    "URL",
]

# Deny list to prevent false positives on common table headers/metadata
DENY_LIST = {
    "name", "student", "father", "mother", "guardian", "aadhaar", "caste",
    "category", "income", "board", "university", "college", "course",
    "seat", "type", "quota", "number", "id", "date", "year", "branch",
    "discipline", "attestation", "submitted", "considered", "eligible",
    "details", "status", "bank", "branch"
}

# Entity type display names for UI
ENTITY_TYPE_LABELS = {
    "PERSON": "Names",
    "EMAIL_ADDRESS": "Email Addresses",
    "PHONE_NUMBER": "Phone Numbers",
    "CREDIT_CARD": "Credit Card Numbers",
    "US_SSN": "Social Security Numbers (US)",
    "US_PASSPORT": "Passport Numbers (US)",
    "LOCATION": "Locations/Addresses",
    "DATE_TIME": "Dates & Times",
    "IP_ADDRESS": "IP Addresses",
    "IBAN_CODE": "Bank Account Numbers (IBAN)",
    "NRP": "National IDs",
    "MEDICAL_LICENSE": "Medical License Numbers",
    "URL": "URLs/Web Addresses",
    "AADHAAR_IN": "Aadhaar Numbers (India)",
}


class HybridPIIDetector:
    """
    Hybrid PII detection engine combining Presidio NER with 
    flexible keyword matching (exact, fuzzy, regex).
    
    All processing is local - no cloud API calls.
    """
    
    def __init__(self, enabled_entities: Optional[List[str]] = None):
        """
        Initialize the detector.
        
        Args:
            enabled_entities: List of Presidio entity types to detect.
                            If None, all default entities are enabled.
        """
        # Configure Presidio to use en_core_web_md model
        configuration = {
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_md"}],
        }
        provider = NlpEngineProvider(nlp_configuration=configuration)
        nlp_engine = provider.create_engine()
        
        self.analyzer = AnalyzerEngine(nlp_engine=nlp_engine)
        self._add_custom_recognizers()
        
        # Set enabled entities (default to all if not specified)
        self.enabled_entities = enabled_entities or DEFAULT_ENTITY_TYPES.copy()
        
    def _add_custom_recognizers(self):
        """Add custom pattern recognizers for additional PII types"""
        
        # Aadhaar number pattern (India): 1234-5678-9012 or 123456789012
        aadhaar_pattern = Pattern(
            name="aadhaar_pattern",
            regex=r"\b\d{4}[- ]?\d{4}[- ]?\d{4}\b",
            score=0.85
        )
        aadhaar_recognizer = PatternRecognizer(
            supported_entity="AADHAAR_IN",
            patterns=[aadhaar_pattern],
            context=["aadhaar", "uid", "uidai", "unique identification"]
        )
        self.analyzer.registry.add_recognizer(aadhaar_recognizer)
        
        # PAN Card pattern (India): ABCDE1234F
        pan_pattern = Pattern(
            name="pan_pattern",
            regex=r"\b[A-Z]{5}[0-9]{4}[A-Z]\b",
            score=0.85
        )
        pan_recognizer = PatternRecognizer(
            supported_entity="PAN_IN",
            patterns=[pan_pattern],
            context=["pan", "permanent account number", "income tax"]
        )
        self.analyzer.registry.add_recognizer(pan_recognizer)
    
    def detect(
        self,
        text: str,
        custom_keywords: Optional[List[str]] = None,
        match_mode: MatchMode = MatchMode.EXACT,
        fuzzy_threshold: int = 85,
        enabled_entities: Optional[List[str]] = None,
        min_score: float = 0.6
    ) -> List[DetectionResult]:
        """
        Detect PII in text using hybrid approach.
        
        Args:
            text: The text to analyze
            custom_keywords: List of custom keywords/patterns to detect
            match_mode: How to match custom keywords (exact, fuzzy, regex)
            fuzzy_threshold: Minimum similarity score for fuzzy matching (0-100)
            enabled_entities: Override default enabled Presidio entities
            min_score: Minimum confidence score for Presidio results
            
        Returns:
            List of DetectionResult objects with matched PII
        """
        results: List[DetectionResult] = []
        entities_to_use = enabled_entities if enabled_entities is not None else self.enabled_entities
        
        # Layer 1: Presidio NER detection (if any entities enabled)
        if entities_to_use:
            presidio_results = self._detect_with_presidio(
                text, entities_to_use, min_score
            )
            results.extend(presidio_results)
        
        # Layer 2: Custom keyword detection
        if custom_keywords:
            keyword_results = self._detect_keywords(
                text, custom_keywords, match_mode, fuzzy_threshold
            )
            results.extend(keyword_results)
        
        # Layer 3: Label-based heuristics (Tables/Forms)
        label_results = self._detect_label_based_names(text)
        results.extend(label_results)
        
        # Deduplicate overlapping results
        results = self._deduplicate_results(results)
        
        # Deduplicate overlapping results
        results = self._deduplicate_results(results)
        
        # Filter deny list
        results = self._filter_deny_list(results)
        
        return results

    def _filter_deny_list(self, results: List[DetectionResult]) -> List[DetectionResult]:
        """Filter out matches that are in the deny list"""
        filtered = []
        for r in results:
            text_lower = r.text.lower().strip()
            # Split into words and check if matches fully or is just a common header word
            if text_lower in DENY_LIST:
                continue
            # Also filter if it's just "Name" or "Father Name" which might match part of the regex group if captured poorly
            if "name" in text_lower and len(text_lower.split()) <= 2:
                 # Be careful, "My Name" is not PII, but "John Name" is weird.
                 # If the text is strictly one of the deny words
                 continue
            filtered.append(r)
        return filtered
    
    def _detect_with_presidio(
        self,
        text: str,
        entities: List[str],
        min_score: float
    ) -> List[DetectionResult]:
        """Run Presidio analyzer on text"""
        results = []
        
        try:
            # Add AADHAAR_IN and PAN_IN to entities if not already present
            all_entities = list(entities)
            if "AADHAAR_IN" not in all_entities:
                all_entities.append("AADHAAR_IN")
            if "PAN_IN" not in all_entities:
                all_entities.append("PAN_IN")
                
            presidio_results = self.analyzer.analyze(
                text=text,
                language='en',
                entities=all_entities
            )
            
            for r in presidio_results:
                if r.score >= min_score:
                    results.append(DetectionResult(
                        text=text[r.start:r.end],
                        entity_type=r.entity_type,
                        start=r.start,
                        end=r.end,
                        score=r.score,
                        match_mode="presidio"
                    ))
        except Exception as e:
            print(f"Presidio analysis error: {e}")
            
        return results
    
    def _detect_keywords(
        self,
        text: str,
        keywords: List[str],
        match_mode: MatchMode,
        fuzzy_threshold: int
    ) -> List[DetectionResult]:
        """Detect custom keywords using specified matching mode"""
        
        if match_mode == MatchMode.EXACT:
            return self._exact_match(text, keywords)
        elif match_mode == MatchMode.FUZZY:
            return self._fuzzy_match(text, keywords, fuzzy_threshold)
        elif match_mode == MatchMode.REGEX:
            return self._regex_match(text, keywords)
        else:
            return self._exact_match(text, keywords)
    
    def _exact_match(
        self,
        text: str,
        keywords: List[str]
    ) -> List[DetectionResult]:
        """Case-insensitive exact matching"""
        results = []
        text_lower = text.lower()
        
        for keyword in keywords:
            if not keyword.strip():
                continue
                
            keyword_lower = keyword.lower().strip()
            start = 0
            
            while True:
                idx = text_lower.find(keyword_lower, start)
                if idx == -1:
                    break
                    
                results.append(DetectionResult(
                    text=text[idx:idx + len(keyword)],
                    entity_type="CUSTOM_KEYWORD",
                    start=idx,
                    end=idx + len(keyword),
                    score=1.0,
                    match_mode="exact"
                ))
                start = idx + 1
                
        return results
    
    def _fuzzy_match(
        self,
        text: str,
        keywords: List[str],
        threshold: int
    ) -> List[DetectionResult]:
        """Fuzzy matching using RapidFuzz for typo tolerance"""
        results = []
        
        # Split text into words while preserving positions
        word_pattern = re.compile(r'\b\w+\b')
        
        for match in word_pattern.finditer(text):
            word = match.group()
            word_start = match.start()
            word_end = match.end()
            
            for keyword in keywords:
                if not keyword.strip():
                    continue
                    
                # Calculate similarity score
                score = fuzz.ratio(word.lower(), keyword.lower().strip())
                
                if score >= threshold:
                    results.append(DetectionResult(
                        text=word,
                        entity_type="CUSTOM_KEYWORD",
                        start=word_start,
                        end=word_end,
                        score=score / 100.0,
                        match_mode="fuzzy"
                    ))
                    break  # Don't match same word multiple times
                    
                # Also check partial ratio for longer keywords
                if len(keyword) > 4:
                    partial_score = fuzz.partial_ratio(
                        keyword.lower().strip(), 
                        word.lower()
                    )
                    if partial_score >= threshold:
                        results.append(DetectionResult(
                            text=word,
                            entity_type="CUSTOM_KEYWORD",
                            start=word_start,
                            end=word_end,
                            score=partial_score / 100.0,
                            match_mode="fuzzy_partial"
                        ))
                        break
                        
        return results
    
    def _regex_match(
        self,
        text: str,
        patterns: List[str]
    ) -> List[DetectionResult]:
        """Match using user-provided regex patterns"""
        results = []
        
        for pattern in patterns:
            if not pattern.strip():
                continue
                
            try:
                regex = re.compile(pattern.strip(), re.IGNORECASE)
                
                for match in regex.finditer(text):
                    results.append(DetectionResult(
                        text=match.group(),
                        entity_type="CUSTOM_PATTERN",
                        start=match.start(),
                        end=match.end(),
                        score=1.0,
                        match_mode="regex"
                    ))
            except re.error as e:
                print(f"Invalid regex pattern '{pattern}': {e}")
                continue
                
        return results
    
    def _detect_label_based_names(self, text: str) -> List[DetectionResult]:
        """
        Detect names based on label context (e.g. 'Father Name: JOHN DOE')
        Captures ONLY the value, not the label.
        """
        results = []
        # Pattern: Label -> optional sep -> Value
        # Labels: Name, Student Name, Father Name, Mother Name, Guardian Name
        # Separators: :, -, or just whitespace
        # Value: Uppercase/Titlecase words, avoiding common headers like "ID", "Number"
        
        # Complex regex to handle various forms seen in Indian documents:
        # "Name as in Aadhaar MANAS S"
        # "Father Name KISHOR KUMAR"
        regex = re.compile(
            r"(?i)\b(?:Name|Student|Father|Mother|Guardian|Husband|Wife)(?:\s+(?:Name|of|Student))?[\s\:\-\.]+(?:as\s+in\s+Aadhaar)?[\s\:\-\.]*([A-Z][A-Za-z\s\.]{2,40})(?=$|\s{2,}|\n)",
            re.MULTILINE
        )
        
        for match in regex.finditer(text):
            value = match.group(1).strip()
            
            # Check if value is actually a header or simple word
            if not value or len(value) < 2:
                continue
                
            val_lower = value.lower()
            
            # Filter matches that are actually other labels or noise
            if val_lower in DENY_LIST or "id" in val_lower or "number" in val_lower:
                continue
                
            results.append(DetectionResult(
                text=value,
                entity_type="PERSON",
                start=match.start(1),
                end=match.end(1),
                score=0.85,
                match_mode="label_heuristic"
            ))
            
        return results

    def _deduplicate_results(
        self,
        results: List[DetectionResult]
    ) -> List[DetectionResult]:
        """Remove overlapping/duplicate results, keeping highest score"""
        if not results:
            return []
            
        # Sort by start position, then by score (descending)
        sorted_results = sorted(
            results, 
            key=lambda r: (r.start, -r.score)
        )
        
        deduplicated = []
        last_end = -1
        
        for result in sorted_results:
            # If this result doesn't overlap with the last accepted result
            if result.start >= last_end:
                deduplicated.append(result)
                last_end = result.end
            # If overlapping, only add if exact same match isn't already there
            elif result.text not in [r.text for r in deduplicated]:
                # Check if it's a different detection of overlapping text
                pass
                
        return deduplicated
    
    def get_unique_terms(self, results: List[DetectionResult]) -> List[str]:
        """Extract unique PII terms from detection results"""
        return list(set(r.text for r in results))


def get_available_entity_types() -> Dict[str, str]:
    """
    Get available Presidio entity types with display labels.
    Used by frontend to show toggleable options.
    """
    return ENTITY_TYPE_LABELS.copy()


def create_detector(enabled_entities: Optional[List[str]] = None) -> HybridPIIDetector:
    """Factory function to create a detector instance"""
    return HybridPIIDetector(enabled_entities=enabled_entities)
