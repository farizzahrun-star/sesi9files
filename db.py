from turtle import title
from typing import Optional, List, Dict
from datetime import date
from sqlalchemy import create_engine, text
from pydantic_settings import BaseSettings, SettingsConfigDict
import pandas as pd
import logging

log = logging.getLogger(__name__)


class DBSettings(BaseSettings):
    database_url: str
    student_name: str

    model_config = SettingsConfigDict(
        env_file=".env", case_sensitive=False, extra="ignore"
    )


settings = DBSettings()
engine = create_engine(settings.database_url)

SCHEMA = "ai_engineer"
ARTICLES_TABLE = f'"{SCHEMA}".articles_{settings.student_name}'
ATTRITION_TABLE_NAME = f"raw_attrition_{settings.student_name}"
ATTRITION_TABLE = f'"{SCHEMA}"."{ATTRITION_TABLE_NAME}"'


def init_db():
    """Create tables and indexes if they don't exist."""
    ddl = f"""
    CREATE TABLE IF NOT EXISTS {ARTICLES_TABLE} (
        id           SERIAL PRIMARY KEY,
        title        TEXT NOT NULL,
        url          TEXT UNIQUE NOT NULL,
        source       TEXT NOT NULL,
        content      TEXT,
        published_at TIMESTAMP,
        scraped_at   TIMESTAMP DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_articles_source
        ON {ARTICLES_TABLE}(source);
    CREATE INDEX IF NOT EXISTS idx_articles_published
        ON {ARTICLES_TABLE}(published_at DESC);
    """
    with engine.begin() as conn:
        conn.execute(text(ddl))
    log.info("Database ready. Table: %s", ARTICLES_TABLE)


def save_articles(df: pd.DataFrame) -> int:
    """Insert articles with ON CONFLICT — safe to re-run."""
    if df.empty:
        log.warning("DataFrame kosong, tidak ada yang di-insert.")
        return 0

    inserted = 0
    with engine.begin() as conn:
        for _, row in df.iterrows():
            result = conn.execute(
                text(f"""
                    INSERT INTO {ARTICLES_TABLE}
                        (title, url, source, content, published_at)
                    VALUES
                        (:title, :url, :source, :content, :published_at)
                    ON CONFLICT (url) DO NOTHING
                """),
                {
                    "title": row["title"],
                    "url": row["url"],
                    "source": row["source"],
                    "content": row["content"],
                    "published_at": row["published_at"]
                    if pd.notna(row["published_at"])
                    else None,
                },
            )
            inserted += result.rowcount

    log.info("Insert selesai: %d baru dari %d record.", inserted, len(df))
    return inserted


def count_articles() -> int:
    """Count total articles in DB."""
    with engine.connect() as conn:
        result = conn.execute(text(f"SELECT COUNT(*) FROM {ARTICLES_TABLE}"))
        return result.scalar()


def count_articles_by_source() -> Dict[str, int]:
    """
    Hitung jumlah artikel per source.
    Return contoh: {"bbc": 50, "nytimes": 30, "books.toscrape": 200}
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                f"SELECT source, COUNT(*) AS cnt FROM {ARTICLES_TABLE} "
                f"GROUP BY source ORDER BY cnt DESC"
            )
        ).mappings().all()
        return {row["source"]: row["cnt"] for row in rows}


def get_articles(
    source: Optional[str] = None,
    title: Optional[str] = None,
    limit: int = 20,
) -> List[Dict]:
    """
    Cari articles dengan optional filter source dan title.

    Contoh pemanggilan:
      get_articles(source="bbc", limit=5)           → artikel BBC saja
      get_articles(title="python", limit=10)         → judul mengandung "python"
      get_articles(source="bbc", title="AI", limit=5) → BBC + judul ada "AI"
    """
    query = f"SELECT * FROM {ARTICLES_TABLE} WHERE 1=1"
    params: dict = {}

    if source:
        query += " AND source = :source"
        params["source"] = source.lower()

    if title:
        query += " AND title ILIKE :title"
        params["title"] = f"%{title}%"

    query += " ORDER BY published_at DESC NULLS LAST LIMIT :limit"
    params["limit"] = limit

    with engine.connect() as conn:
        rows = conn.execute(text(query), params).mappings().all()
    return [dict(r) for r in rows]


def search_articles(
    source: Optional[str] = None,
    title: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    page: int = 1,
    per_page: int = 10,
) -> Dict:
    """Search articles with filters and return one page plus its total count."""
    filters = []
    params: dict = {}

    if source:
        filters.append("source = :source")
        params["source"] = source.lower()
    if title:
        filters.append("title ILIKE :title")
        params["title"] = f"%{title}%"
    if date_from:
        filters.append("published_at >= :date_from")
        params["date_from"] = date_from
    if date_to:
        filters.append("published_at < :date_to_exclusive")
        params["date_to_exclusive"] = date_to.fromordinal(
            date_to.toordinal() + 1)

    where_clause = " AND ".join(filters) if filters else "1=1"
    offset = (page - 1) * per_page

    with engine.connect() as conn:
        total_items = conn.execute(
            text(
                f"SELECT COUNT(*) FROM {ARTICLES_TABLE} WHERE {where_clause}"),
            params,
        ).scalar()
        rows = conn.execute(
            text(
                f"SELECT * FROM {ARTICLES_TABLE} WHERE {where_clause} "
                "ORDER BY published_at DESC NULLS LAST LIMIT :limit OFFSET :offset"
            ),
            {**params, "limit": per_page, "offset": offset},
        ).mappings().all()

    return {
        "data": [dict(row) for row in rows],
        "page": page,
        "per_page": per_page,
        "total_items": total_items,
    }


def get_article_by_id(article_id: int) -> Optional[Dict]:
    """Get single article by ID. Return None jika tidak ditemukan."""
    with engine.connect() as conn:
        row = conn.execute(
            text(f"SELECT * FROM {ARTICLES_TABLE} WHERE id = :id"),
            {"id": article_id},
        ).mappings().first()
        return dict(row) if row else None


def get_attrition_summary() -> Dict:
    """
    Overall stats: total karyawan, jumlah & rate attrition.
    Return contoh: {"total_employees": 1470, "attrition_yes": 237, ...}
    """
    with engine.connect() as conn:
        total = conn.execute(
            text(f"SELECT COUNT(*) FROM {ATTRITION_TABLE}")
        ).scalar()
        attrition_yes = conn.execute(
            text(
                f"SELECT COUNT(*) FROM {ATTRITION_TABLE} WHERE \"Attrition\" = :status"),
            {"status": "Yes"},
        ).scalar()

    attrition_rate = round(attrition_yes / total, 4) if total else 0.0
    return {
        "total_employees": total,
        "attrition_yes": attrition_yes,
        "attrition_no": total - attrition_yes,
        "attrition_rate": attrition_rate,
    }


def get_attrition_by_department() -> List[Dict]:
    """
    Attrition rate + avg income per department.
    Ini gabungan query 1 & 2 dari transform_attrition.sql Session 9.
    """
    query = f"""
        SELECT
            "Department",
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE "Attrition" = 'Yes') AS attrition_count,
            ROUND(
                COUNT(*) FILTER (WHERE "Attrition" = 'Yes')::numeric / COUNT(*), 4
            ) AS attrition_rate,
            ROUND(AVG("MonthlyIncome")::numeric, 2) AS avg_income
        FROM {ATTRITION_TABLE}
        GROUP BY "Department"
        ORDER BY attrition_rate DESC;
    """
    with engine.connect() as conn:
        rows = conn.execute(text(query)).mappings().all()
    return [dict(r) for r in rows]


def get_attrition_department_detail(dept_name: str) -> Optional[Dict]:
    """Return employee, attrition, and income statistics for one department."""
    summary_query = f"""
        SELECT
            "Department" AS department,
            COUNT(*) AS total_employees,
            COUNT(*) FILTER (WHERE "Attrition" = 'Yes') AS attrition_count,
            ROUND(
                COUNT(*) FILTER (WHERE "Attrition" = 'Yes')::numeric / COUNT(*), 4
            ) AS attrition_rate,
            ROUND(AVG("MonthlyIncome")::numeric, 2) AS avg_income,
            MIN("MonthlyIncome") AS min_income,
            MAX("MonthlyIncome") AS max_income
        FROM {ATTRITION_TABLE}
        WHERE "Department" = :department
        GROUP BY "Department"
    """
    earners_query = f"""
        SELECT "EmployeeNumber", "JobRole", "MonthlyIncome"
        FROM (
            SELECT
                "EmployeeNumber",
                "JobRole",
                "MonthlyIncome",
                RANK() OVER (ORDER BY "MonthlyIncome" DESC) AS income_rank
            FROM {ATTRITION_TABLE}
            WHERE "Department" = :department
        ) ranked
        WHERE income_rank <= 3
        ORDER BY income_rank
        LIMIT 3
    """
    with engine.connect() as conn:
        summary = conn.execute(
            text(summary_query), {"department": dept_name}
        ).mappings().first()
        if summary is None:
            return None
        top_earners = conn.execute(
            text(earners_query), {"department": dept_name}
        ).mappings().all()

    return {**dict(summary), "top_earners": [dict(row) for row in top_earners]}


def get_attrition_risk_profile(limit: int = 20) -> List[Dict]:
    """Find employees matching the high-risk attrition criteria."""
    query = f"""
        WITH dept_avg AS (
            SELECT "Department", AVG("MonthlyIncome") AS avg_income
            FROM {ATTRITION_TABLE}
            GROUP BY "Department"
        )
        SELECT e.*
        FROM {ATTRITION_TABLE} e
        JOIN dept_avg d ON e."Department" = d."Department"
        WHERE e."OverTime" = 'Yes'
          AND e."YearsAtCompany" < 3
          AND e."MonthlyIncome" < d.avg_income
        ORDER BY e."Department", e."MonthlyIncome" ASC
        LIMIT :limit
    """
    with engine.connect() as conn:
        rows = conn.execute(text(query), {"limit": limit}).mappings().all()
    return [dict(row) for row in rows]


def get_attrition_by_tenure() -> List[Dict]:
    """
    Attrition by tenure bucket.
    Ini query 3 dari transform_attrition.sql Session 9.
    """
    query = f"""
        SELECT
            CASE
                WHEN "YearsAtCompany" < 2 THEN '1. new (<2Y)'
                WHEN "YearsAtCompany" < 5 THEN '2. mid (2-5Y)'
                ELSE '3. veteran (5y+)'
            END AS tenure_bucket,
            COUNT(*) AS total_karyawan,
            COUNT(*) FILTER (WHERE "Attrition" = 'Yes') AS yang_attrition
        FROM {ATTRITION_TABLE}
        GROUP BY tenure_bucket
        ORDER BY tenure_bucket;
    """
    with engine.connect() as conn:
        rows = conn.execute(text(query)).mappings().all()
    return [dict(r) for r in rows]


def get_attrition_by_overtime() -> List[Dict]:
    """
    Attrition by overtime status.
    Ini query 4 dari transform_attrition.sql Session 9.
    """
    query = f"""
        SELECT
            "OverTime",
            "Attrition",
            COUNT(*) AS jumlah
        FROM {ATTRITION_TABLE}
        GROUP BY "OverTime", "Attrition"
        ORDER BY "OverTime", "Attrition";
    """
    with engine.connect() as conn:
        rows = conn.execute(text(query)).mappings().all()
    return [dict(r) for r in rows]


def get_top_earners_by_department(limit_per_dept: int = 5) -> List[Dict]:
    """
    Top earners per department menggunakan RANK() window function.
    Ini query 5 dari transform_attrition.sql Session 9.
    """
    query = f"""
        SELECT * FROM (
            SELECT
                "EmployeeNumber",
                "Department",
                "JobRole",
                "MonthlyIncome",
                RANK() OVER (
                    PARTITION BY "Department"
                    ORDER BY "MonthlyIncome" DESC
                ) AS income_rank
            FROM {ATTRITION_TABLE}
        ) ranked
        WHERE income_rank <= :limit_per_dept
        ORDER BY "Department", income_rank;
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text(query),
            {"limit_per_dept": limit_per_dept},
        ).mappings().all()
    return [dict(r) for r in rows]
