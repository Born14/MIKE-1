"""
Scouting Rubric for MIKE-1.

Central definition of how trades are scored.
"""
from dataclasses import dataclass
from typing import List, Tuple

@dataclass
class ScoreResult:
    score: float
    reasons: List[str]

class ScoringRubric:
    """
    Centralized scoring logic for the Judge.
    """
    
    # Delta Scoring
    @staticmethod
    def score_delta(delta: float) -> ScoreResult:
        reasons = []
        score = 0.0
        
        # A-Tier: 0.30 - 0.45 (+2)
        if 0.30 <= delta <= 0.45:
            score += 2.0
            reasons.append(f"Delta in A-tier sweet spot ({delta:.2f})")
            
        # B-Tier: 0.15 - 0.30 (+1)
        elif 0.15 <= delta < 0.30:
            score += 1.0
            reasons.append(f"Delta in B-tier range ({delta:.2f})")
            
        # Lottery: < 0.15 (-1)
        elif delta < 0.15:
            score -= 1.0
            reasons.append(f"Low delta ({delta:.2f}) - lottery ticket")
            
        return ScoreResult(score, reasons)

    # DTE Scoring
    @staticmethod
    def score_dte(dte: int) -> ScoreResult:
        reasons = []
        score = 0.0
        
        # Gamma Trap: < 2 days (-2) (unless explicitly 0DTE strategy, but general rule applies)
        if dte < 2:
            score -= 2.0
            reasons.append(f"Low DTE ({dte} days) - gamma risk high")
            
        # Sweet Spot: 3 - 14 days (+1)
        elif 3 <= dte <= 14:
            score += 1.0
            reasons.append(f"DTE in sweet spot ({dte} days)")
            
        # Far Out: > 14 days (Neutral/No penalty for now, but maybe less momentum leverage)
        elif dte > 14:
            reasons.append(f"DTE > 14 days ({dte})")
            
        return ScoreResult(score, reasons)

    # Technical Scoring (Volume, VWAP, RSI)
    @staticmethod
    def score_technicals(
        vol_ratio: float, 
        price_vs_vwap_pct: float, 
        rsi: float, 
        direction: str
    ) -> ScoreResult:
        reasons = []
        score = 5.0  # Base
        
        # 1. Volume
        if vol_ratio >= 3.0:
            score += 4
            reasons.append(f"Strong volume spike ({vol_ratio:.1f}x avg)")
        elif vol_ratio >= 2.0:
            score += 2
            reasons.append(f"Volume elevated ({vol_ratio:.1f}x avg)")
        elif vol_ratio < 1.0:
            score -= 2
            reasons.append(f"Volume below average ({vol_ratio:.1f}x)")
            
        # 2. VWAP
        is_call = direction == "call"
        if is_call:
            if price_vs_vwap_pct > 0.5:
                score += 3
                reasons.append(f"Price above VWAP (+{price_vs_vwap_pct:.1f}%)")
            elif price_vs_vwap_pct < -1.0:
                score -= 2
                reasons.append(f"Price below VWAP ({price_vs_vwap_pct:.1f}%)")
        else: # Put
            if price_vs_vwap_pct < -0.5:
                score += 3
                reasons.append(f"Price below VWAP ({price_vs_vwap_pct:.1f}%)")
            elif price_vs_vwap_pct > 1.0:
                score -= 2
                reasons.append(f"Price above VWAP (+{price_vs_vwap_pct:.1f}%)")
                
        # 3. RSI
        # Call: Wants not overbought (>85 bad), maybe oversold bounce (<30 good)
        if is_call:
            if rsi < 30:
                score += 2
                reasons.append(f"RSI oversold ({rsi:.0f}) - bullish reversal setup")
            elif rsi > 85:
                score -= 3
                reasons.append(f"RSI extreme overbought ({rsi:.0f})")
            elif rsi > 70:
                score -= 1
                reasons.append(f"RSI overbought ({rsi:.0f})")
            else:
                score += 1
                reasons.append(f"RSI neutral ({rsi:.0f})")
        else: # Put
            if rsi > 70:
                score += 2
                reasons.append(f"RSI overbought ({rsi:.0f}) - bearish reversal setup")
            elif rsi < 15:
                score -= 3
                reasons.append(f"RSI extreme oversold ({rsi:.0f})")
            elif rsi < 30:
                score -= 1
                reasons.append(f"RSI oversold ({rsi:.0f})")
            else:
                score += 1
                reasons.append(f"RSI neutral ({rsi:.0f})")
                
        return ScoreResult(max(0, min(10, score)), reasons)

    # Liquidity Scoring
    @staticmethod
    def score_liquidity(
        oi: int, 
        spread_pct: float, 
        vol_oi_ratio: float,
        is_unusual: bool,
        min_oi: int = 500,
        max_spread: float = 10.0
    ) -> ScoreResult:
        score = 5.0
        reasons = []
        
        # OI
        if oi >= min_oi * 2:
            score += 4
            reasons.append(f"Strong OI ({oi:,})")
        elif oi >= min_oi:
            score += 2
            reasons.append(f"Adequate OI ({oi:,})")
        else:
            score -= 3
            reasons.append(f"Low OI ({oi:,})")
            
        # Spread
        if spread_pct <= max_spread / 2:
            score += 4
            reasons.append(f"Tight spread ({spread_pct:.1f}%)")
        elif spread_pct <= max_spread:
            score += 2
            reasons.append(f"Acceptable spread ({spread_pct:.1f}%)")
        else:
            score -= 3
            reasons.append(f"Wide spread ({spread_pct:.1f}%)")
            
        # UOA
        if is_unusual:
            score += 1.5
            reasons.append(f"UNUSUAL ACTIVITY: Vol/OI {vol_oi_ratio:.1f}x")
            
        return ScoreResult(max(0, min(10, score)), reasons)
