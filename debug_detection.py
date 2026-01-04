import sys
import os

# Add server to path
sys.path.append(os.path.join(os.getcwd(), 'server'))

from src.hybrid_detector import HybridPIIDetector, MatchMode

detector = HybridPIIDetector()

# Simulate table rows based on user description
test_cases = [
    "Name as in Aadhaar   MANAS S",
    "Student ID           21220755937",
    "Father Name          KISHOR KUMAR",
    "College District     DAKSHINA KANNADA",
    "Seat Type            SNQ",
    "Caste                BILLAVA"
]

print("--- Accuracy Test ---")
for text in test_cases:
    results = detector.detect(text, enabled_entities=["PERSON", "AADHAAR_IN", "LOCATION"])
    print(f"\nText: '{text}'")
    for r in results:
        print(f"  FAILED MATCH: Redacting '{r.text}' ({r.entity_type}, score={r.score:.2f})")
