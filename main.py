
import os
import hmac
import sqlite3
from io import BytesIO
from pathlib import Path
from datetime import datetime, timezone

import psycopg
import qrcode
from psycopg.rows import dict_row
from fastapi import FastAPI, HTTPException, Query, Header
from fastapi.responses import FileResponse, StreamingResponse

app = FastAPI(title="ThreadLoop")

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "threadloop.db"
TEMPLATES_DIR = BASE_DIR / "templates"

DATABASE_URL = os.getenv("DATABASE_URL")
BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000").rstrip("/")
PARTNER_CONFIRM_TOKEN = os.getenv("PARTNER_CONFIRM_TOKEN")

garments = {
    "TL-1001": {
        "name": "Cotton T-shirt",
        "material": "100% cotton",
        "care": "Wash gently in cold water",
        "status": "Sample data — not verified"
    }
}


def get_connection():
    if DATABASE_URL:
        return psycopg.connect(
            DATABASE_URL,
            row_factory=dict_row,
            connect_timeout=10
        )

    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    connection = get_connection()

    try:
        if DATABASE_URL:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS handoffs (
                    id BIGSERIAL PRIMARY KEY,
                    garment_id TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    points INTEGER NOT NULL DEFAULT 0
                )
            """)
        else:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS handoffs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    garment_id TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TEXT NOT NULL,
                    points INTEGER NOT NULL DEFAULT 0
                )
            """)

        connection.commit()
    finally:
        connection.close()


init_db()


def execute_query(connection, sql, params=()):
    if DATABASE_URL:
        sql = sql.replace("?", "%s")
    return connection.execute(sql, params)


@app.get("/")
def home():
    return FileResponse(TEMPLATES_DIR / "index.html")


@app.get("/garment/{garment_id}")
def garment_passport(garment_id: str):
    if garment_id not in garments:
        raise HTTPException(status_code=404, detail="Garment not found")

    return FileResponse(TEMPLATES_DIR / "garment.html")


@app.get("/api/garment/{garment_id}")
def get_garment(garment_id: str):
    garment = garments.get(garment_id)

    if garment is None:
        raise HTTPException(status_code=404, detail="Garment not found")

    return {
        "garment_id": garment_id,
        "name": garment["name"],
        "material": garment["material"],
        "care": garment["care"],
        "status": garment["status"]
    }


@app.get("/api/next-life/{garment_id}")
def next_life(
    garment_id: str,
    condition: str = Query(default="good")
):
    garment = garments.get(garment_id)

    if garment is None:
        raise HTTPException(status_code=404, detail="Garment not found")

    recommendations = {
        "good": (
            "Resale or donation",
            "The garment may still be useful to someone else."
        ),
        "minor_damage": (
            "Repair first",
            "Repair may extend the garment's useful life."
        ),
        "worn_out": (
            "Check recycling options",
            "Find a partner that accepts this material and condition."
        )
    }

    if condition not in recommendations:
        raise HTTPException(
            status_code=400,
            detail="Condition must be good, minor_damage, or worn_out"
        )

    recommendation, reason = recommendations[condition]

    return {
        "garment_id": garment_id,
        "garment": garment["name"],
        "material": garment["material"],
        "condition": condition,
        "recommendation": recommendation,
        "reason": reason,
        "note": (
            "Prototype recommendation. Confirm garment condition "
            "and partner requirements before handoff."
        )
    }


@app.get("/qr.png")
def generate_qr(garment_id: str = Query(default="TL-1001")):
    if garment_id not in garments:
        raise HTTPException(status_code=404, detail="Garment not found")

    qr_url = f"{BASE_URL}/garment/{garment_id}"
    image = qrcode.make(qr_url)

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)

    return StreamingResponse(buffer, media_type="image/png")


@app.get("/map")
def recovery_map():
    return FileResponse(TEMPLATES_DIR / "map.html")


@app.post("/api/handoffs")
def create_handoff(garment_id: str, destination: str):
    if garment_id not in garments:
        raise HTTPException(status_code=404, detail="Garment not found")

    destination = destination.strip()
    if not destination or len(destination) > 200:
        raise HTTPException(
            status_code=400,
            detail="Destination must contain 1–200 characters"
        )

    connection = get_connection()

    try:
        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

        if DATABASE_URL:
            cursor = execute_query(
                connection,
                """
                INSERT INTO handoffs
                    (garment_id, destination, status, created_at, points)
                VALUES (?, ?, ?, ?, ?)
                RETURNING id
                """,
                (garment_id, destination, "pending", created_at, 0)
            )
            handoff_id = cursor.fetchone()["id"]
        else:
            cursor = execute_query(
                connection,
                """
                INSERT INTO handoffs
                    (garment_id, destination, status, created_at, points)
                VALUES (?, ?, ?, ?, ?)
                """,
                (garment_id, destination, "pending", created_at, 0)
            )
            handoff_id = cursor.lastrowid

        connection.commit()

        return {
            "message": "Handoff submitted successfully",
            "handoff_id": handoff_id,
            "garment_id": garment_id,
            "destination": destination,
            "status": "pending",
            "points": 0
        }
    finally:
        connection.close()


@app.post("/api/handoffs/{handoff_id}/confirm")
def confirm_handoff(
    handoff_id: int,
    x_partner_token: str | None = Header(default=None)
):
    if not PARTNER_CONFIRM_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="Partner confirmation is not configured"
        )

    if (
        not x_partner_token
        or not hmac.compare_digest(
            x_partner_token,
            PARTNER_CONFIRM_TOKEN
        )
    ):
        raise HTTPException(status_code=403, detail="Invalid partner token")

    connection = get_connection()

    try:
        handoff = execute_query(
            connection,
            "SELECT * FROM handoffs WHERE id = ?",
            (handoff_id,)
        ).fetchone()

        if handoff is None:
            raise HTTPException(status_code=404, detail="Handoff not found")

        if handoff["status"] == "confirmed":
            raise HTTPException(
                status_code=400,
                detail="Handoff already confirmed"
            )

        execute_query(
            connection,
            """
            UPDATE handoffs
            SET status = ?, points = ?
            WHERE id = ?
            """,
            ("confirmed", 10, handoff_id)
        )

        connection.commit()

        return {
            "message": "Prototype handoff confirmed",
            "handoff_id": handoff_id,
            "status": "confirmed",
            "points_awarded": 10
        }
    finally:
        connection.close()


@app.get("/api/handoffs/{handoff_id}")
def get_handoff(handoff_id: int):
    connection = get_connection()

    try:
        handoff = execute_query(
            connection,
            "SELECT * FROM handoffs WHERE id = ?",
            (handoff_id,)
        ).fetchone()

        if handoff is None:
            raise HTTPException(status_code=404, detail="Handoff not found")

        return dict(handoff)
    finally:
        connection.close()
