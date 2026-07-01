from fastapi import APIRouter, Depends, HTTPException, status
import json
import logging
import os

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain.chat_models import init_chat_model
from models import ProductReview, RawProductReview, ProductReviewRateResult
from database import get_db
from sqlmodel import Session

load_dotenv()


router = APIRouter()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("review_analysis")

SYSTEM_PROMPT = (
    "You analyze product reviews and return structured data.\n"
    "Sentiment values:\n"
    "  - 'positive': clearly favorable overall.\n"
    "  - 'negative': clearly unfavorable overall.\n"
    "  - 'neutral': purely factual / indifferent / no clear evaluation.\n"
    "  - 'mixed': contains both clearly positive and clearly negative points.\n"
    "Always include an honest 'confidence' (0-1). Lower it for short, "
    "ambiguous, sarcastic, or off-topic reviews. Detect 'language' as a "
    "two-letter ISO 639-1 code (e.g. 'en', 'tr', 'de')."
)

REQUIRED_FIELDS = ("user", "product", "review")

# Which LLM backend /llm/chat uses. "google_genai" (Gemini, default) or
# "openrouter" (any OpenRouter-hosted model via its OpenAI-compatible API).
# Set LLM_PROVIDER + the matching API key in .env (local) or the app-secret
# Secret (Kubernetes).
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "google_genai").strip().lower()

# Tuning shared across providers.
_MODEL_KWARGS = dict(temperature=0.1, timeout=30, max_tokens=500)


def build_model():
    if LLM_PROVIDER == "openrouter":
        return init_chat_model(
            os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
            model_provider="openai",
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
            **_MODEL_KWARGS,
        )
    if LLM_PROVIDER in ("google_genai", "gemini", "google"):
        return init_chat_model(
            os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite"),
            model_provider="google_genai",
            **_MODEL_KWARGS,
        )
    raise ValueError(
        f"Unknown LLM_PROVIDER {LLM_PROVIDER!r}; use 'google_genai' or 'openrouter'."
    )


def build_agent():
    return create_agent(
        model=build_model(),
        tools=[],
        response_format=ToolStrategy(schema=ProductReview),
        system_prompt=SYSTEM_PROMPT,
    )

def analyze_one(agent, review_text: str) -> ProductReview:
    result = agent.invoke({
        "messages": [{
            "role": "user",
            "content": f"Analyze this review: '{review_text}'",
        }]
    })
    response = result.get("structured_response")
    if response is None:
        raise RuntimeError("agent returned no structured_response")
    return response


# Build the agent lazily on first request, not at import time: it needs a
# Google API key, and we want the app (and its other endpoints) to start
# even when GOOGLE_API_KEY is unset — only /llm/chat should then error.
_agent = None


def get_agent():
    global _agent
    if _agent is None:
        _agent = build_agent()
    return _agent


@router.post("/llm/chat", response_model=ProductReviewRateResult)
async def make_chat(request: RawProductReview, session: Session = Depends(get_db)):
    try:
        analysis = analyze_one(get_agent(), request.review)
    except Exception as e:
        log.error(e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"LLM analysis failed (is GOOGLE_API_KEY set?): {e}",
        )

    product_review = ProductReviewRateResult(
                user_info = request.user,
                review = request.review,
                product = request.product,
                rate = analysis.rating,
                confidence = analysis.confidence,
                sentiment = analysis.sentiment,
                language = analysis.language,
                key_points = json.dumps(analysis.key_points)
                )
    
    with session:
        session.add(product_review) # Use session
        session.commit()
        session.refresh(product_review) # Route ends, get_db() resumes and closes session
                
    return  product_review