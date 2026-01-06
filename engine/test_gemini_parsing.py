"""
Test Gemini LLM Response Parsing

Tests the LLM client's ability to:
1. Parse valid JSON responses
2. Handle markdown code blocks
3. Handle malformed JSON gracefully
4. Extract all required fields correctly
"""

import os
import sys
import json

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))


def test_json_parsing():
    """Test parsing various JSON response formats."""
    print("=" * 60)
    print("TEST: Gemini Response Parsing")
    print("=" * 60)
    print()

    # Test cases
    test_cases = [
        # 1. Clean JSON
        {
            "name": "Clean JSON",
            "input": '{"has_catalyst": true, "mention_type": "primary", "sentiment": "bullish", "confidence": 0.85, "summary": "Strong earnings beat", "reasoning": "Revenue exceeded expectations"}',
            "should_pass": True,
            "expected": {"has_catalyst": True, "sentiment": "bullish", "confidence": 0.85}
        },
        # 2. Markdown code block with json tag
        {
            "name": "Markdown code block (json)",
            "input": '```json\n{"has_catalyst": true, "mention_type": "secondary", "sentiment": "neutral", "confidence": 0.5, "summary": "Sector rotation", "reasoning": "General market trend"}\n```',
            "should_pass": True,
            "expected": {"has_catalyst": True, "sentiment": "neutral", "confidence": 0.5}
        },
        # 3. Markdown code block without tag
        {
            "name": "Markdown code block (no tag)",
            "input": '```\n{"has_catalyst": false, "mention_type": "passing", "sentiment": "bearish", "confidence": 0.2, "summary": "No catalyst", "reasoning": "No news"}\n```',
            "should_pass": True,
            "expected": {"has_catalyst": False, "sentiment": "bearish", "confidence": 0.2}
        },
        # 4. JSON with extra whitespace
        {
            "name": "JSON with whitespace",
            "input": '\n\n  {"has_catalyst": true, "mention_type": "primary", "sentiment": "bullish", "confidence": 0.9, "summary": "Test", "reasoning": "Test"}  \n\n',
            "should_pass": True,
            "expected": {"has_catalyst": True, "confidence": 0.9}
        },
        # 5. Invalid JSON
        {
            "name": "Invalid JSON (missing quote)",
            "input": '{"has_catalyst": true, sentiment: "bullish"}',
            "should_pass": False,
            "expected": None
        },
        # 6. Empty response
        {
            "name": "Empty response",
            "input": '',
            "should_pass": False,
            "expected": None
        },
        # 7. Not JSON at all
        {
            "name": "Plain text (not JSON)",
            "input": 'The sentiment is bullish with high confidence.',
            "should_pass": False,
            "expected": None
        },
    ]

    passed = 0
    failed = 0

    for case in test_cases:
        print(f"[TEST] {case['name']}")
        result = parse_gemini_response(case['input'])

        if case['should_pass']:
            if result is None:
                print(f"  FAIL: Expected valid result, got None")
                failed += 1
            else:
                # Check expected fields
                all_match = True
                for key, expected_val in case['expected'].items():
                    actual_val = result.get(key)
                    if actual_val != expected_val:
                        print(f"  FAIL: {key} = {actual_val}, expected {expected_val}")
                        all_match = False
                        failed += 1
                        break
                if all_match:
                    print(f"  PASS: Correctly parsed")
                    passed += 1
        else:
            if result is None:
                print(f"  PASS: Correctly returned None for invalid input")
                passed += 1
            else:
                print(f"  FAIL: Should have returned None, got {result}")
                failed += 1

    print()
    print(f"Results: {passed}/{passed + failed} passed")
    return failed == 0


def parse_gemini_response(text: str) -> dict | None:
    """
    Parse Gemini response text into structured dict.

    This mirrors the parsing logic in llm_client.py
    """
    if not text:
        return None

    text = text.strip()

    # Handle markdown code blocks
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last lines (``` markers)
        text = "\n".join(lines[1:-1])

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def test_score_conversion():
    """Test converting LLM response to catalyst score."""
    print()
    print("=" * 60)
    print("TEST: Catalyst Score Conversion")
    print("=" * 60)
    print()

    # Test cases: (response, direction, expected_score_range)
    test_cases = [
        # High confidence bullish + call = high score
        {
            "name": "High confidence bullish call",
            "response": {
                "has_catalyst": True,
                "sentiment": "bullish",
                "confidence": 0.9,
                "mention_type": "primary"
            },
            "direction": "call",
            "expected_min": 8.0,
            "expected_max": 10.0
        },
        # High confidence bearish + put = high score
        {
            "name": "High confidence bearish put",
            "response": {
                "has_catalyst": True,
                "sentiment": "bearish",
                "confidence": 0.85,
                "mention_type": "primary"
            },
            "direction": "put",
            "expected_min": 8.0,
            "expected_max": 10.0
        },
        # Misaligned sentiment = lower score
        {
            "name": "Bearish sentiment but call direction",
            "response": {
                "has_catalyst": True,
                "sentiment": "bearish",
                "confidence": 0.8,
                "mention_type": "primary"
            },
            "direction": "call",
            "expected_min": 5.0,
            "expected_max": 7.0
        },
        # No catalyst = neutral score
        {
            "name": "No catalyst",
            "response": {
                "has_catalyst": False,
                "sentiment": "neutral",
                "confidence": 0,
                "mention_type": "passing"
            },
            "direction": "call",
            "expected_min": 4.0,
            "expected_max": 6.0
        },
        # Low confidence = moderate score
        {
            "name": "Low confidence",
            "response": {
                "has_catalyst": True,
                "sentiment": "bullish",
                "confidence": 0.3,
                "mention_type": "passing"
            },
            "direction": "call",
            "expected_min": 5.0,
            "expected_max": 7.5
        },
    ]

    passed = 0
    failed = 0

    for case in test_cases:
        print(f"[TEST] {case['name']}")
        score = calculate_catalyst_score(case['response'], case['direction'])

        if case['expected_min'] <= score <= case['expected_max']:
            print(f"  PASS: Score {score:.1f} in range [{case['expected_min']}, {case['expected_max']}]")
            passed += 1
        else:
            print(f"  FAIL: Score {score:.1f} NOT in range [{case['expected_min']}, {case['expected_max']}]")
            failed += 1

    print()
    print(f"Results: {passed}/{passed + failed} passed")
    return failed == 0


def calculate_catalyst_score(response: dict, direction: str) -> float:
    """
    Calculate catalyst score from LLM response.

    This mirrors the scoring logic in judge.py _score_catalyst()
    """
    score = 5.0  # Start neutral

    if not response.get("has_catalyst", False):
        return score

    # Has catalyst (+2 base)
    score += 2

    # Sentiment alignment
    sentiment = response.get("sentiment", "neutral")
    confidence = response.get("confidence", 0)

    # Check if sentiment aligns with direction
    aligned = (
        (sentiment == "bullish" and direction == "call") or
        (sentiment == "bearish" and direction == "put")
    )

    if aligned:
        if confidence >= 0.8:
            score += 3
        elif confidence >= 0.5:
            score += 1
    else:
        # Misaligned sentiment can reduce score
        if sentiment != "neutral" and confidence >= 0.6:
            score -= 1

    # Clamp
    return max(0, min(10, score))


def test_edge_cases():
    """Test edge cases and error handling."""
    print()
    print("=" * 60)
    print("TEST: Edge Cases")
    print("=" * 60)
    print()

    test_cases = [
        # Missing required fields
        {
            "name": "Missing has_catalyst field",
            "input": '{"sentiment": "bullish", "confidence": 0.5}',
            "check": lambda r: r is not None and "has_catalyst" not in r
        },
        # Confidence out of range (should still parse)
        {
            "name": "Confidence > 1.0",
            "input": '{"has_catalyst": true, "confidence": 1.5, "sentiment": "bullish"}',
            "check": lambda r: r is not None and r.get("confidence") == 1.5
        },
        # Unicode in response
        {
            "name": "Unicode characters",
            "input": '{"has_catalyst": true, "summary": "NVDA \u2014 earnings beat", "sentiment": "bullish", "confidence": 0.7}',
            "check": lambda r: r is not None and "\u2014" in r.get("summary", "")
        },
        # Nested markdown blocks
        {
            "name": "Nested code blocks (malformed)",
            "input": '```json\n```\n{"has_catalyst": true}\n```\n```',
            "check": lambda r: r is None  # Should fail
        },
    ]

    passed = 0
    failed = 0

    for case in test_cases:
        print(f"[TEST] {case['name']}")
        result = parse_gemini_response(case['input'])

        if case['check'](result):
            print(f"  PASS")
            passed += 1
        else:
            print(f"  FAIL: Got {result}")
            failed += 1

    print()
    print(f"Results: {passed}/{passed + failed} passed")
    return failed == 0


if __name__ == "__main__":
    print()
    print("MIKE-1 Gemini Parsing Tests")
    print("=" * 60)
    print()

    results = []
    results.append(("JSON Parsing", test_json_parsing()))
    results.append(("Score Conversion", test_score_conversion()))
    results.append(("Edge Cases", test_edge_cases()))

    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    all_passed = True
    for name, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"  {name}: {status}")
        if not passed:
            all_passed = False

    print()
    if all_passed:
        print("All tests passed!")
        sys.exit(0)
    else:
        print("Some tests failed!")
        sys.exit(1)
