"""
LLM Client for MIKE-1

Provides LLM integration for catalyst/sentiment analysis.
Currently supports Google Gemini. Add other providers as needed.

Usage:
    client = GeminiClient()  # Uses GEMINI_API_KEY from env
    result = client.assess_catalyst(prompt)
"""

import os
import json
from typing import Optional
import structlog

logger = structlog.get_logger()


class LLMClient:
    """Base class for LLM clients."""

    def assess_catalyst(self, prompt: str) -> Optional[dict]:
        """
        Assess a catalyst/sentiment prompt.

        Args:
            prompt: The prompt to send to the LLM

        Returns:
            dict with has_catalyst, sentiment, confidence, summary, reasoning
            or None on error
        """
        raise NotImplementedError


class GeminiClient(LLMClient):
    """
    Google Gemini LLM client.

    Requires GEMINI_API_KEY environment variable.
    """

    def __init__(self, api_key: Optional[str] = None, model: str = "gemini-2.0-flash"):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self.model = model
        self._client = None

        if not self.api_key:
            logger.warning("GEMINI_API_KEY not set - LLM features disabled")

    def _get_client(self):
        """Lazy initialize the Gemini client."""
        if self._client is None and self.api_key:
            try:
                import google.generativeai as genai
                genai.configure(api_key=self.api_key)
                self._client = genai.GenerativeModel(self.model)
                logger.info("Gemini client initialized", model=self.model)
            except ImportError:
                logger.error("google-generativeai not installed. Run: pip install google-generativeai")
            except Exception as e:
                logger.error("Error initializing Gemini", error=str(e))
        return self._client

    def assess_catalyst(self, prompt: str) -> Optional[dict]:
        """
        Assess catalyst/sentiment using Gemini.

        The prompt should ask for structured output. We parse the response
        to extract the key fields.
        """
        client = self._get_client()
        if not client:
            return None

        try:
            # Add JSON output instruction
            structured_prompt = f"""{prompt}

IMPORTANT: Respond ONLY with valid JSON in this exact format:
{{
    "has_catalyst": true or false,
    "mention_type": "primary" or "secondary" or "passing",
    "sentiment": "bullish" or "bearish" or "neutral",
    "confidence": 0.0 to 1.0,
    "summary": "one sentence summary",
    "reasoning": "why this supports or contradicts the thesis"
}}
"""
            response = client.generate_content(structured_prompt)

            if not response or not response.text:
                return None

            # Parse JSON from response
            text = response.text.strip()

            # Handle markdown code blocks
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1])  # Remove first and last lines

            result = json.loads(text)

            logger.debug(
                "Gemini catalyst assessment",
                has_catalyst=result.get("has_catalyst"),
                sentiment=result.get("sentiment"),
                confidence=result.get("confidence")
            )

            return result

        except json.JSONDecodeError as e:
            logger.error("Error parsing Gemini response as JSON", error=str(e))
            return None
        except Exception as e:
            logger.error("Error calling Gemini", error=str(e))
            return None

    def chat(self, prompt: str) -> Optional[str]:
        """
        General chat with Gemini.

        For non-structured responses.
        """
        client = self._get_client()
        if not client:
            return None

        try:
            response = client.generate_content(prompt)
            return response.text if response else None
        except Exception as e:
            logger.error("Error in Gemini chat", error=str(e))
            return None


class MockLLMClient(LLMClient):
    """
    Mock LLM client for testing.

    Returns neutral assessments without API calls.
    """

    def assess_catalyst(self, prompt: str) -> Optional[dict]:
        """Return mock neutral assessment."""
        return {
            "has_catalyst": False,
            "sentiment": "neutral",
            "confidence": 0.5,
            "summary": "Mock assessment - no real LLM call",
            "reasoning": "This is a mock response for testing"
        }


def get_llm_client() -> Optional[LLMClient]:
    """
    Factory function to get the appropriate LLM client.

    Returns GeminiClient if GEMINI_API_KEY is set, else None.
    """
    if os.environ.get("GEMINI_API_KEY"):
        return GeminiClient()
    return None
