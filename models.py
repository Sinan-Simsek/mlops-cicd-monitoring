from datetime import datetime, timezone
from typing import Optional, Literal
from sqlmodel import SQLModel, Field, text

# Using for request/input
class RawProductReview(SQLModel):
    user: str = Field(..., description="The username or identifier of the reviewer")
    product: str = Field(..., description="The name of the product being reviewed")
    review: str = Field(..., description="The full text content of the review")

    # Pydantic v2 configuration style
    class Config:
        from_attributes = True
        json_schema_extra = {
            "example": {
                "user": "john_doe",
                "product": "Wireless Headphones XYZ",
                "review": "Amazing product! 5 stars. Quick delivery and great quality, but quite pricey.",
            }
        }

# Product Review Analysis. When AI answer the request uses this model/format
class ProductReview(SQLModel):
    """Analysis of a product review."""
    rating: int = Field(
        description="The rating of the product (1-5)",
        ge=1,
        le=5,
    )
    sentiment: Literal["positive", "negative", "neutral", "mixed"] = Field(
        description=(
            "Overall sentiment of the review. Use 'mixed' when the review "
            "expresses both clearly positive and clearly negative points; "
            "'neutral' for purely factual or indifferent reviews."
        )
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Your honest confidence in this analysis, 0-1. "
            "Lower it for short, ambiguous, or sarcastic reviews."
        ),
    )
    language: str = Field(
        description="ISO 639-1 language code of the review text, e.g. 'en', 'tr', 'de'.",
        min_length=2,
        max_length=2,
    )
    key_points: list[str] = Field(
        description="Key points from the review. Lowercase, 1-3 words each."
    )

# When we(FastAPI) response the request we use this model/format
class ProductReviewRate(SQLModel):
    ## raw input
    user_info: str = Field(description="User information or identifier")
    review: str = Field(description="The original review text")
    product: str = Field(index=True, description="Product name or identifier")
    ## llm generated
    rate: Optional[int] = Field(default=None, description="Rating 1-5")
    sentiment: Optional[str] = Field(
        default=None,
        index=True,
        description="One of: positive, negative, neutral, mixed",
    )
    confidence: Optional[float] = Field(
        default=None,
        description="Model's self-reported confidence in the analysis (0-1)",
    )
    language: Optional[str] = Field(
        default=None,
        index=True,
        description="ISO 639-1 language code detected from the review (e.g. 'en', 'tr')",
    )
    key_points: Optional[str] = Field(default=None, description="Key points as JSON string")



class ProductReviewRateResult(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_info: str = Field(description="User information or identifier")
    review: str = Field(description="The original review text")
    product: str = Field(index=True, description="Product name or identifier")
    ## llm generated
    rate: Optional[int] = Field(default=None, description="Rating 1-5")
    sentiment: Optional[str] = Field(
        default=None,
        index=True,
        description="One of: positive, negative, neutral, mixed",
    )
    confidence: Optional[float] = Field(
        default=None,
        description="Model's self-reported confidence in the analysis (0-1)",
    )
    language: Optional[str] = Field(
        default=None,
        index=True,
        description="ISO 639-1 language code detected from the review (e.g. 'en', 'tr')",
    )
    key_points: Optional[str] = Field(default=None, description="Key points as JSON string")
    # Timestamp implementation
    created_at: Optional[datetime] = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")},
    ) 

class Comment(SQLModel):
    comment: str

    class Config:
        json_schema_extra = {
            "example": {
                "comment": "Among the many lovely details of this film are views about the gender barriers in the Middle East and the customs of a city that, while modern, is still a culture of men. As Juliette and Tareq wander the streets of Cairo we recognize subtleties that exist, subtleties that director Nadda never forces. The gorgeous cinematography is by Luc Montpellier and the musical score is by Niall Byrne. This film is more a poem than a story, a welcome change from the usual youngster-oriented love stories and more of a mature episode of ageless flirtation."
            }
        }

class CommentPredict(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    comment: str
    sentiment: str
    client_ip: str
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)