from datetime import date
from math import ceil
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Header, Depends
from pydantic import BaseModel, Field

from db import (
    count_articles,
    count_articles_by_source,
    get_attrition_department_detail,
    get_attrition_risk_profile,
    search_articles,
)

app = FastAPI(
    title="AI Prediction API",
    description=(
        "API untuk pemeriksaan kesehatan layanan, akses data contoh, "
        "dan prediksi label dari input teks."
    ),
    version="1.0.0",
)

API_KEY = "mini-project-2"


def verify_api_key(x_api_key: Optional[str] = Header(None)):
    if x_api_key != API_KEY:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API Key"
        )

class PredictRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    threshold: float = Field(ge=0.0, le=1.0)

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "text": "Layanan ini memberikan pengalaman yang sangat baik.",
                    "threshold": 0.7,
                }
            ]
        }
    }


sample_data = [
    {"id": 1, "category": "electronics", "name": "Wireless headphones"},
    {"id": 2, "category": "electronics", "name": "Mechanical keyboard"},
    {"id": 3, "category": "electronics", "name": "Portable speaker"},
    {"id": 4, "category": "books", "name": "The Design of Everyday Things"},
    {"id": 5, "category": "books", "name": "Clean Code"},
]


@app.get("/health", tags=["System"], summary="Periksa status server")
def health_check():
    return {"status": "ok"}


@app.get(
    "/articles/search",
    tags=["Articles"],
    summary="Cari artikel",
    dependencies=[Depends(verify_api_key)],
)
def article_search(
    source: Optional[str] = Query(None),
    title: Optional[str] = Query(None),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=50),
):
    if date_from and date_to and date_from > date_to:
        raise HTTPException(
            status_code=422, detail="date_from must be on or before date_to"
        )

    result = search_articles(
        source=source,
        title=title,
        date_from=date_from,
        date_to=date_to,
        page=page,
        per_page=per_page,
    )
    result["total_pages"] = ceil(result["total_items"] / per_page)
    return result


@app.get("/articles/stats", tags=["Articles"], summary="Statistik artikel")
def article_stats():
    return {
        "total_articles": count_articles(),
        "by_source": count_articles_by_source(),
    }


@app.get(
    "/attrition/department/{dept_name}",
    tags=["Attrition"],
    summary="Statistik attrition per department",
)
def attrition_department_detail(dept_name: str):
    result = get_attrition_department_detail(dept_name)
    if result is None:
        raise HTTPException(status_code=404, detail="Department not found")
    return result


@app.get(
    "/attrition/risk-profile",
    tags=["Attrition"],
    summary="Daftar karyawan berisiko attrition tinggi",
)
def attrition_risk_profile(limit: int = Query(20, ge=1, le=100)):
    return get_attrition_risk_profile(limit=limit)


@app.get("/data", tags=["Data"], summary="Ambil data contoh")
def list_data(
    category: Optional[str] = Query(
        None, description="Filter berdasarkan kategori"),
    limit: Optional[int] = Query(
        None, ge=1, description="Jumlah maksimum data"),
):
    results = sample_data
    if category is not None:
        results = [item for item in results if item["category"] == category]
    if limit is not None:
        results = results[:limit]
    return results


@app.post(
    "/predict",
    tags=["Prediction"],
    summary="Prediksi label dari input teks",
    description="Endpoint ini digunakan untuk memprediksi label dari input teks.",
)
def predict(request: PredictRequest):
    confidence = 0.92
    return {
        "label": "positif" if confidence >= request.threshold else "netral",
        "confidence": confidence,
        "input_text": request.text,
    }
