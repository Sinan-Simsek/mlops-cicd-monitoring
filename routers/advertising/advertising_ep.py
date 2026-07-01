from typing import Optional
from datetime import datetime, timezone
from fastapi import APIRouter, status, Depends
from database import get_db
from sqlmodel import Session, SQLModel, Field, text
import joblib

router = APIRouter()



class Advertising(SQLModel):
    TV: float
    Radio: float
    Newspaper: float

    class Config:
        json_schema_extra = {
            "example": {
                "TV": 230.1,
                "Radio": 37.8,
                "Newspaper": 69.2,
            }
        }

class AdvertisingResponse(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    TV: float = None
    Radio: float = None
    Newspaper: float = None
    PredictedSales: float = None
    # Timestamp implementation
    created_at: Optional[datetime] = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")},
    ) 

estimator_loaded = joblib.load("/app/saved_models/03.randomforest_with_advertising.pkl")



def make_prediction(request):
    # Prediction set
    features = [list(request.model_dump().values())]
    print("features", features)

    prediction_raw = estimator_loaded.predict(features)
    print("prediction_raw", prediction_raw)


    return float(prediction_raw[0])


@router.post("/prediction/advertising", status_code=status.HTTP_201_CREATED)
async def preoredict_advertising(request: Advertising, session: Session = Depends(get_db)):
    prediction = make_prediction(request)

    # Write to DB
    new_predicted_adv = AdvertisingResponse(
            TV=request.TV,
            Radio=request.Radio,
            Newspaper=request.Newspaper,
            PredictedSales=prediction

        )
    
    with session:
        session.add(new_predicted_adv) # Use session
        session.commit()
        session.refresh(new_predicted_adv) # Route ends, get_db() resumes and closes session
    return new_predicted_adv