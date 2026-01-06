"""
Test Judge Integration

Tests the full Judge workflow with mocked dependencies:
1. Mocked broker returning sample technical/liquidity data
2. Mocked LLM client returning sample catalyst assessments
3. Verifying score aggregation (35% technical + 35% liquidity + 30% catalyst)
4. Grade thresholds (A/B/NO tiers)
5. Direction alignment logic

No API keys required - uses mocks for everything.
"""

import os
import sys
from dataclasses import dataclass
from unittest.mock import patch

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from mike1.modules.judge import Judge, TradeGrade
from mike1.modules.social import SocialData


@dataclass
class MockOptionQuote:
    """Mock option quote for testing."""
    open_interest: int = 5000
    volume: int = 1000
    bid: float = 2.50
    ask: float = 2.55
    delta: float = 0.35


class MockBroker:
    """
    Mock broker that returns configurable test data.

    Configure via set_* methods before calling Judge.
    """

    def __init__(self):
        self._price = 150.0
        self._volume_data = {
            "current_volume": 5000000,
            "avg_volume": 2000000
        }
        self._vwap_data = {"vwap": 148.0}
        self._rsi = 55.0
        self._option_quote = MockOptionQuote()
        self._news = []
        self._connected = True

    def connect(self) -> bool:
        return True

    def disconnect(self):
        pass

    def get_stock_price(self, symbol: str) -> float:
        return self._price

    def get_volume_data(self, symbol: str) -> dict:
        return self._volume_data

    def get_vwap(self, symbol: str) -> dict:
        return self._vwap_data

    def get_rsi(self, symbol: str, period: int = 14) -> float:
        return self._rsi

    def get_option_quote(self, symbol: str, strike: float, exp: str, opt_type: str):
        return self._option_quote

    def get_news(self, symbol: str, limit: int = 5) -> list:
        return self._news

    # Configuration methods
    def set_price(self, price: float):
        self._price = price

    def set_volume(self, current: int, avg: int):
        self._volume_data = {"current_volume": current, "avg_volume": avg}

    def set_vwap(self, vwap: float):
        self._vwap_data = {"vwap": vwap}

    def set_rsi(self, rsi: float):
        self._rsi = rsi

    def set_option(self, oi: int = 5000, volume: int = 1000, bid: float = 2.50,
                   ask: float = 2.55, delta: float = 0.35):
        self._option_quote = MockOptionQuote(oi, volume, bid, ask, delta)

    def set_news(self, headlines: list[str]):
        self._news = [{"headline": h} for h in headlines]


class MockLLMClient:
    """
    Mock LLM client that returns configurable responses.
    """

    def __init__(self):
        self._response = {
            "has_catalyst": False,
            "mention_type": "passing",
            "sentiment": "neutral",
            "confidence": 0.5,
            "summary": "Mock assessment",
            "reasoning": "Test reasoning"
        }

    def assess_catalyst(self, _prompt: str) -> dict:
        return self._response

    def set_response(self, has_catalyst: bool = False, sentiment: str = "neutral",
                     confidence: float = 0.5, mention_type: str = "passing",
                     summary: str = "Mock", reasoning: str = "Test"):
        self._response = {
            "has_catalyst": has_catalyst,
            "mention_type": mention_type,
            "sentiment": sentiment,
            "confidence": confidence,
            "summary": summary,
            "reasoning": reasoning
        }


class MockSocialClient:
    """Mock social client that returns empty data (no API calls)."""

    def get_social_data(self, symbol: str) -> SocialData:
        return SocialData(symbol=symbol)


@patch('mike1.modules.social.get_social_client')
def test_grade_thresholds(mock_get_social):
    """Test that grade thresholds work correctly."""
    mock_get_social.return_value = MockSocialClient()

    print("=" * 60)
    print("TEST: Grade Thresholds")
    print("=" * 60)
    print()

    # A-TIER: Score >= 7.0
    # B-TIER: Score >= 5.0 and < 7.0
    # NO_TRADE: Score < 5.0

    test_cases = [
        # A-TIER scenario: Strong technicals + good liquidity (score >= 7.0)
        {
            "name": "A-TIER scenario",
            "setup": lambda b, l: (
                b.set_volume(10000000, 2000000),  # 5x volume
                b.set_vwap(145.0),  # Price above VWAP
                b.set_rsi(55),
                b.set_option(10000, 2000, 2.50, 2.52, 0.40),  # Good liquidity
                l.set_response(True, "bullish", 0.9, "primary", "Strong catalyst", "Earnings beat")
            ),
            "expected_grade": TradeGrade.A_TIER
        },
        # B-TIER scenario: Mixed signals (score 5.0-6.9)
        {
            "name": "B-TIER scenario",
            "setup": lambda b, l: (
                b.set_volume(2000000, 2000000),  # 1.0x volume (no boost)
                b.set_vwap(150.5),  # Slightly below VWAP for call (-0.33%)
                b.set_rsi(55),  # Neutral
                # Medium OI, wider spread to lower liquidity score
                b.set_option(600, 200, 2.30, 2.60, 0.28),  # ~12% spread
                l.set_response(False, "neutral", 0.3, "passing", "No catalyst", "")
            ),
            "expected_grade": TradeGrade.B_TIER
        },
        # NO_TRADE scenario: Weak across the board (score < 5.0)
        {
            "name": "NO_TRADE scenario",
            "setup": lambda b, l: (
                b.set_volume(1000000, 2000000),  # Below average volume
                b.set_vwap(155.0),  # Price below VWAP for call
                b.set_rsi(85),  # Overbought
                b.set_option(200, 50, 2.50, 3.00, 0.10),  # Poor liquidity
                l.set_response(False, "bearish", 0.1, "passing", "Weak", "Against direction")
            ),
            "expected_grade": TradeGrade.NO_TRADE
        }
    ]

    passed = 0
    failed = 0

    for case in test_cases:
        print(f"[TEST] {case['name']}")

        # Reset and configure
        broker = MockBroker()
        llm = MockLLMClient()
        case['setup'](broker, llm)

        judge = Judge(broker, llm)
        verdict = judge.grade("TEST", "call", strike=150.0, expiration="2026-01-17")

        if verdict.grade == case['expected_grade']:
            print(f"  PASS: Grade={verdict.grade.value} Score={verdict.score:.1f}")
            passed += 1
        else:
            print(f"  FAIL: Expected {case['expected_grade'].value}, got {verdict.grade.value}")
            print(f"        Score={verdict.score:.1f} (tech={verdict.technical_score:.1f}, liq={verdict.liquidity_score:.1f}, cat={verdict.catalyst_score:.1f})")
            failed += 1

    print()
    print(f"Results: {passed}/{passed + failed} passed")
    return failed == 0


@patch('mike1.modules.social.get_social_client')
def test_weight_calculation(mock_get_social):
    """Test that weighted score calculation is correct."""
    mock_get_social.return_value = MockSocialClient()

    print()
    print("=" * 60)
    print("TEST: Weight Calculation")
    print("=" * 60)
    print()

    broker = MockBroker()
    llm = MockLLMClient()
    judge = Judge(broker, llm)

    # Weights should be: tech=35%, liq=35%, cat=30%
    expected_weights = {"technical": 0.35, "liquidity": 0.35, "catalyst": 0.30}

    print("[TEST] Verify weights sum to 1.0")
    weight_sum = sum(judge.WEIGHTS.values())
    if abs(weight_sum - 1.0) < 0.001:
        print(f"  PASS: Weights sum to {weight_sum}")
    else:
        print(f"  FAIL: Weights sum to {weight_sum}, expected 1.0")
        return False

    print()
    print("[TEST] Verify individual weights")
    all_correct = True
    for factor, expected in expected_weights.items():
        actual = judge.WEIGHTS.get(factor)
        if abs(actual - expected) < 0.001:
            print(f"  PASS: {factor} = {actual}")
        else:
            print(f"  FAIL: {factor} = {actual}, expected {expected}")
            all_correct = False

    print()
    print("[TEST] Verify score calculation")
    # Set up known scenario
    broker.set_volume(4000000, 2000000)  # 2x volume = +2 points, score ~7
    broker.set_vwap(148.0)  # Above VWAP = +3 points
    broker.set_rsi(55)  # Neutral = +1 point
    broker.set_option(5000, 1000, 2.50, 2.55, 0.35)  # Good options
    llm.set_response(True, "bullish", 0.85, "primary")

    verdict = judge.grade("TEST", "call", strike=150.0, expiration="2026-01-17")

    # Manually calculate expected score
    expected_score = (
        verdict.technical_score * 0.35 +
        verdict.liquidity_score * 0.35 +
        verdict.catalyst_score * 0.30
    )

    if abs(verdict.score - expected_score) < 0.1:
        print(f"  PASS: Score {verdict.score:.2f} matches calculation {expected_score:.2f}")
    else:
        print(f"  FAIL: Score {verdict.score:.2f} != expected {expected_score:.2f}")
        all_correct = False

    print()
    return all_correct


@patch('mike1.modules.social.get_social_client')
def test_catalyst_scoring(mock_get_social):
    """Test that catalyst scoring works correctly based on confidence."""
    mock_get_social.return_value = MockSocialClient()

    print()
    print("=" * 60)
    print("TEST: Catalyst Scoring")
    print("=" * 60)
    print()

    # Test that catalyst with high confidence boosts score vs no catalyst
    test_cases = [
        {
            "name": "High confidence catalyst vs no catalyst",
            "has_catalyst": True,
            "confidence": 0.85,
            "expected_boost_min": 4  # has_catalyst(+2) + high confidence(+3) = +5
        },
        {
            "name": "Moderate confidence catalyst vs no catalyst",
            "has_catalyst": True,
            "confidence": 0.6,
            "expected_boost_min": 2  # has_catalyst(+2) + moderate confidence(+1) = +3
        },
        {
            "name": "Low confidence catalyst vs no catalyst",
            "has_catalyst": True,
            "confidence": 0.3,
            "expected_boost_min": 1  # has_catalyst(+2) only
        },
    ]

    passed = 0
    failed = 0

    for case in test_cases:
        print(f"[TEST] {case['name']}")

        broker = MockBroker()
        broker.set_volume(4000000, 2000000)
        broker.set_vwap(148.0)
        broker.set_rsi(55)
        broker.set_option(5000, 1000, 2.50, 2.55, 0.35)
        broker.set_news(["Test headline for sentiment analysis"])

        # With catalyst
        llm = MockLLMClient()
        llm.set_response(case['has_catalyst'], "bullish", case['confidence'], "primary")
        judge = Judge(broker, llm)
        verdict = judge.grade("TEST", "call", strike=150.0, expiration="2026-01-17")

        # Without catalyst (baseline)
        llm_neutral = MockLLMClient()
        llm_neutral.set_response(False, "neutral", 0.0, "passing")
        judge_neutral = Judge(broker, llm_neutral)
        verdict_neutral = judge_neutral.grade("TEST", "call", strike=150.0, expiration="2026-01-17")

        score_diff = verdict.catalyst_score - verdict_neutral.catalyst_score

        if score_diff >= case['expected_boost_min']:
            print(f"  PASS: Catalyst score boosted by {score_diff:.1f} points (min expected: {case['expected_boost_min']})")
            passed += 1
        else:
            print(f"  FAIL: Boost {score_diff:.1f} < expected min {case['expected_boost_min']}")
            failed += 1

    print()
    print(f"Results: {passed}/{passed + failed} passed")
    return failed == 0


@patch('mike1.modules.social.get_social_client')
def test_unusual_activity(mock_get_social):
    """Test unusual options activity detection."""
    mock_get_social.return_value = MockSocialClient()

    print()
    print("=" * 60)
    print("TEST: Unusual Options Activity Detection")
    print("=" * 60)
    print()

    broker = MockBroker()
    judge = Judge(broker, None)  # No LLM needed

    test_cases = [
        # Vol/OI > 1.25 with sufficient volume = unusual
        {
            "name": "High Vol/OI (unusual)",
            "oi": 1000,
            "volume": 2000,  # Vol/OI = 2.0
            "expected_unusual": True
        },
        # Vol/OI < 1.25 = not unusual
        {
            "name": "Normal Vol/OI",
            "oi": 5000,
            "volume": 1000,  # Vol/OI = 0.2
            "expected_unusual": False
        },
        # Low absolute volume = not counted even if ratio high
        {
            "name": "High ratio but low volume",
            "oi": 50,
            "volume": 100,  # Ratio 2.0 but volume < 500
            "expected_unusual": False
        },
        # Low OI = not counted
        {
            "name": "Low OI",
            "oi": 50,
            "volume": 1000,
            "expected_unusual": False
        },
    ]

    passed = 0
    failed = 0

    for case in test_cases:
        print(f"[TEST] {case['name']}")

        broker.set_option(case['oi'], case['volume'], 2.50, 2.55, 0.35)

        verdict = judge.grade("TEST", "call", strike=150.0, expiration="2026-01-17")

        if verdict.liquidity:
            is_unusual = verdict.liquidity.is_unusual_activity
            if is_unusual == case['expected_unusual']:
                print(f"  PASS: is_unusual={is_unusual}")
                passed += 1
            else:
                print(f"  FAIL: is_unusual={is_unusual}, expected {case['expected_unusual']}")
                print(f"        Vol/OI ratio: {verdict.liquidity.vol_oi_ratio:.2f}")
                failed += 1
        else:
            print(f"  FAIL: No liquidity data")
            failed += 1

    print()
    print(f"Results: {passed}/{passed + failed} passed")
    return failed == 0


@patch('mike1.modules.social.get_social_client')
def test_no_llm(mock_get_social):
    """Test Judge works without LLM client."""
    mock_get_social.return_value = MockSocialClient()

    print()
    print("=" * 60)
    print("TEST: No LLM Client")
    print("=" * 60)
    print()

    broker = MockBroker()
    broker.set_volume(4000000, 2000000)
    broker.set_vwap(148.0)
    broker.set_rsi(55)
    broker.set_option(5000, 1000, 2.50, 2.55, 0.35)

    judge = Judge(broker, llm_client=None)  # No LLM

    print("[TEST] Judge should work without LLM")
    verdict = judge.grade("TEST", "call", strike=150.0, expiration="2026-01-17")

    if verdict is not None:
        print(f"  PASS: Got verdict: {verdict.grade.value} ({verdict.score:.1f}/10)")
        print(f"        Catalyst score defaulted to: {verdict.catalyst_score}")
        return True
    else:
        print("  FAIL: No verdict returned")
        return False


@patch('mike1.modules.social.get_social_client')
def test_verdict_to_dict(mock_get_social):
    """Test JudgeVerdict serialization."""
    mock_get_social.return_value = MockSocialClient()

    print()
    print("=" * 60)
    print("TEST: Verdict Serialization")
    print("=" * 60)
    print()

    broker = MockBroker()
    llm = MockLLMClient()
    judge = Judge(broker, llm)

    verdict = judge.grade("NVDA", "call", strike=150.0, expiration="2026-01-17")

    print("[TEST] to_dict() should return valid dictionary")
    d = verdict.to_dict()

    required_keys = ["symbol", "direction", "grade", "score", "technical_score",
                     "liquidity_score", "catalyst_score", "reasoning", "timestamp"]

    missing = [k for k in required_keys if k not in d]
    if not missing:
        print(f"  PASS: All required keys present")
        print(f"        Sample: symbol={d['symbol']}, grade={d['grade']}, score={d['score']}")
        return True
    else:
        print(f"  FAIL: Missing keys: {missing}")
        return False


if __name__ == "__main__":
    print()
    print("MIKE-1 Judge Integration Tests")
    print("=" * 60)
    print()

    results = []
    results.append(("Grade Thresholds", test_grade_thresholds()))
    results.append(("Weight Calculation", test_weight_calculation()))
    results.append(("Catalyst Scoring", test_catalyst_scoring()))
    results.append(("Unusual Activity", test_unusual_activity()))
    results.append(("No LLM", test_no_llm()))
    results.append(("Verdict Serialization", test_verdict_to_dict()))

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
