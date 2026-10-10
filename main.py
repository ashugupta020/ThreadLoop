
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from io import BytesIO
from pathlib import Path
from datetime import datetime
import sqlite3
import qrcode

app = FastAPI(title="ThreadLoop")

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "threadloop.db"
TEMPLATES_DIR = BASE_DIR / "templates"

# Sample garment data
garments = {
    "TL-1001": {
        "name": "Cotton T-shirt",
        "material": "100% cotton",
        "care": "Wash gently in cold water",
        "status": "Sample data — not verified"
    }
}


# Database setup
def init_db():
    connection = sqlite3.connect(DB_PATH)

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
    connection.close()


init_db()


# Home page
@app.get("/")
def home():
    return FileResponse(TEMPLATES_DIR / "index.html")


# Garment passport page
@app.get("/garment/{garment_id}")
def garment_passport(garment_id: str):
    if garment_id not in garments:
        raise HTTPException(
            status_code=404,
            detail="Garment not found"
        )

    return FileResponse(TEMPLATES_DIR / "garment.html")


# Get garment details
@app.get("/api/garment/{garment_id}")
def get_garment(garment_id: str):
    garment = garments.get(garment_id)

    if garment is None:
        raise HTTPException(
            status_code=404,
            detail="Garment not found"
        )

    return {
        "garment_id": garment_id,
        "name": garment["name"],
        "material": garment["material"],
        "care": garment["care"],
        "status": garment["status"]
    }


# Recommend the garment's next life
@app.get("/api/next-life/{garment_id}")
def next_life(
    garment_id: str,
    condition: str = Query(default="good")
):
    garment = garments.get(garment_id)

    if garment is None:
        raise HTTPException(
            status_code=404,
            detail="Garment not found"
        )

    recommendations = {
        "good": {
            "recommendation": "Resale or donation",
            "reason": "The garment may still be useful to someone else."
        },
        "minor_damage": {
            "recommendation": "Repair first",
            "reason": "Repair may extend the garment's useful life."
        },
        "worn_out": {
            "recommendation": "Check recycling options",
            "reason": "Find a partner that accepts this material and condition."
        }
    }

    if condition not in recommendations:
        raise HTTPException(
            status_code=400,
            detail="Condition must be good, minor_damage, or worn_out"
        )

    result = recommendations[condition]

    return {
        "garment_id": garment_id,
        "garment": garment["name"],
        "material": garment["material"],
        "condition": condition,
        "recommendation": result["recommendation"],
        "reason": result["reason"],
        "note": (
            "Prototype recommendation. Confirm garment condition "
            "and partner requirements before handoff."
        )
    }


# Generate a QR code
@app.get("/qr.png")
def generate_qr(
    garment_id: str = Query(default="TL-1001")
):
    if garment_id not in garments:
        raise HTTPException(
            status_code=404,
            detail="Garment not found"
        )

    qr_url = (
        f"http://127.0.0.1:8000/garment/{garment_id}"
    )

    image = qrcode.make(qr_url)

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)

    return StreamingResponse(
        buffer,
        media_type="image/png"
    )


# Recovery map page
@app.get("/map")
def recovery_map():
    return FileResponse(TEMPLATES_DIR / "map.html")


# Submit a garment handoff
@app.post("/api/handoffs")
def create_handoff(
    garment_id: str,
    destination: str
):
    if garment_id not in garments:
        raise HTTPException(
            status_code=404,
            detail="Garment not found"
        )

    connection = sqlite3.connect(DB_PATH)

    cursor = connection.execute(
        """
        INSERT INTO handoffs
        (garment_id, destination, status, created_at, points)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            garment_id,
            destination,
            "pending",
            datetime.now().isoformat(timespec="seconds"),
            0
        )
    )

    handoff_id = cursor.lastrowid

    connection.commit()
    connection.close()

    return {
        "message": "Handoff submitted successfully",
        "handoff_id": handoff_id,
        "garment_id": garment_id,
        "destination": destination,
        "status": "pending",
        "points": 0
    }


# Confirm a garment handoff
@app.post("/api/handoffs/{handoff_id}/confirm")
def confirm_handoff(handoff_id: int):
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row

    try:
        handoff = connection.execute(
            "SELECT * FROM handoffs WHERE id = ?",
            (handoff_id,)
        ).fetchone()

        if handoff is None:
            raise HTTPException(
                status_code=404,
                detail="Handoff not found"
            )

        if handoff["status"] == "confirmed":
            raise HTTPException(
                status_code=400,
                detail="Handoff already confirmed"
            )

        points = 10

        connection.execute(
            """
            UPDATE handoffs
            SET status = ?, points = ?
            WHERE id = ?
            """,
            ("confirmed", points, handoff_id)
        )

        connection.commit()

        return {
            "message": "Handoff confirmed successfully",
            "handoff_id": handoff_id,
            "status": "confirmed",
            "points_awarded": points
        }

    finally:
        connection.close()


# View a handoff's current status
@app.get("/api/handoffs/{handoff_id}")
def get_handoff(handoff_id: int):
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row

    try:
        handoff = connection.execute(
            "SELECT * FROM handoffs WHERE id = ?",
            (handoff_id,)
        ).fetchone()

        if handoff is None:
            raise HTTPException(
                status_code=404,
                detail="Handoff not found"
            )

        return dict(handoff)

    finally:
        connection.close()
