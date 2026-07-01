from typing import Optional
from datetime import datetime, timezone
from fastapi import APIRouter, status, Depends
from database import get_db
from sqlmodel import Session, SQLModel, Field, text
import joblib

router = APIRouter()

class IrisPredictionModel(SQLModel):
    SepalLengthCm: float
    SepalWidthCm: float
    PetalLengthCm: float
    PetalWidthCm: float

    class Config:
        json_schema_extra = {
            "example": {
                "SepalLengthCm": 5.1,
                "SepalWidthCm": 3.5,
                "PetalLengthCm": 1.4,
                "PetalWidthCm": 0.2,
            }
        }

class IrisResponse(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    SepalLengthCm: float = None
    SepalWidthCm: float = None
    PetalLengthCm: float = None
    PetalWidthCm: float = None
    PredictedSpecie: str = None
    # Timestamp implementation
    created_at: Optional[datetime] = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        sa_column_kwargs={"server_default": text("CURRENT_TIMESTAMP")},
    ) 

classifier_loaded = joblib.load("/app/saved_models/01.knn_with_iris_dataset.pkl")
encoder_loaded = joblib.load("/app/saved_models/02.iris_label_encoder.pkl")

def make_prediction(request):
    # Prediction set
    features = [list(request.model_dump().values())]
    print("features", features)

    prediction_raw = classifier_loaded.predict(features)
    print("prediction_raw", prediction_raw)

    prediction_real = encoder_loaded.inverse_transform(classifier_loaded.predict(features))
    print("Real prediction", prediction_real)

    return prediction_real[0]



@router.post("/prediction/iris",  status_code=status.HTTP_201_CREATED)
async def predict_iris(request: IrisPredictionModel, session: Session = Depends(get_db)):

    prediction = make_prediction(request)
    # Write to DB
    new_predicted_iris = IrisResponse(
            SepalLengthCm=request.SepalLengthCm,
            SepalWidthCm=request.SepalWidthCm,
            PetalLengthCm=request.PetalLengthCm,
            PetalWidthCm=request.PetalWidthCm,
            PredictedSpecie=prediction

        )
    
    with session:
        session.add(new_predicted_iris) # Use session
        session.commit()
        session.refresh(new_predicted_iris) # Route ends, get_db() resumes and closes session

    return new_predicted_iris