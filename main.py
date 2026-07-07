import os
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from datetime import datetime
import psycopg2 # PostgreSQL 연결용 (Render 환경)
import sqlite3   # SQLite 연결용 (로컬 환경)
from typing import List, Optional

app = FastAPI(title="나만의 번핏 클라우드 API")

# Render가 제공하는 데이터베이스 주소가 있으면 Postgres, 없으면 로컬 SQLite 사용
DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    if DATABASE_URL:
        # 클라우드 환경 (PostgreSQL)
        return psycopg2.connect(DATABASE_URL)
    else:
        # 로컬 환경 (SQLite)
        conn = sqlite3.connect("burnfit_clone.db")
        conn.row_factory = sqlite3.Row # 딕셔너리 형태로 읽기 위함
        return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # DB 종류에 따른 자동 문법 대응 (Postgres는 AUTOINCREMENT 대신 SERIAL 사용)
    id_type = "SERIAL PRIMARY KEY" if DATABASE_URL else "INTEGER PRIMARY KEY AUTOINCREMENT"
    text_type = "TEXT"
    real_type = "REAL"
    integer_type = "INTEGER"
    
    # 1. 운동 종목 테이블
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS exercises (
            id {id_type},
            name {text_type} NOT NULL UNIQUE,
            category {text_type} NOT NULL
        )
    """)
    
    # 2. 운동 세션 테이블
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS workout_sessions (
            id {id_type},
            date {text_type} NOT NULL,
            title {text_type} NOT NULL
        )
    """)
    
    # 3. 세트 기록 테이블
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS workout_sets (
            id {id_type},
            session_id {integer_type},
            exercise_name {text_type} NOT NULL,
            set_num {integer_type} NOT NULL,
            weight {real_type} NOT NULL,
            reps {integer_type} NOT NULL
        )
    """)
    
    # 기본 더미 운동 삽입
    cursor.execute("SELECT COUNT(*) FROM exercises")
    if cursor.fetchone()[0] == 0:
        default_exercises = [
            ('바벨 벤치프레스', '가슴'),
            ('인클라인 덤벨프레스', '가슴'),
            ('스쿼트', '하체'),
            ('데드리프트', '전신'),
            ('바벨 컬', '이두')
        ]
        for name, cat in default_exercises:
            cursor.execute("INSERT INTO exercises (name, category) VALUES (%s, %s)" if DATABASE_URL else "INSERT INTO exercises (name, category) VALUES (?, ?)", (name, cat))
        
    conn.commit()
    conn.close()

init_db()

# --- Pydantic 모델 ---
class SetRecord(BaseModel):
    exercise_name: str
    set_num: int
    weight: float
    reps: int

class WorkoutCreate(BaseModel):
    title: str
    sets: List[SetRecord]

class NewExercise(BaseModel):
    name: str
    category: str

# --- API 엔드포인트 ---

@app.post("/api/workouts")
def save_workout(data: WorkoutCreate):
    conn = get_db_connection()
    cursor = conn.cursor()
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    try:
        q1 = "INSERT INTO workout_sessions (date, title) VALUES (%s, %s) RETURNING id" if DATABASE_URL else "INSERT INTO workout_sessions (date, title) VALUES (?, ?)"
        if DATABASE_URL:
            cursor.execute(q1, (today_str, data.title))
            session_id = cursor.fetchone()[0]
        else:
            cursor.execute(q1, (today_str, data.title))
            session_id = cursor.lastrowid
        
        q2 = "INSERT INTO workout_sets (session_id, exercise_name, set_num, weight, reps) VALUES (%s, %s, %s, %s, %s)" if DATABASE_URL else "INSERT INTO workout_sets (session_id, exercise_name, set_num, weight, reps) VALUES (?, ?, ?, ?, ?)"
        for s in data.sets:
            cursor.execute(q2, (session_id, s.exercise_name, s.set_num, s.weight, s.reps))
            
        conn.commit()
        return {"status": "success", "session_id": session_id}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.get("/api/exercises")
def get_all_exercises():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT name, category FROM exercises ORDER BY category, name")
    rows = cursor.fetchall()
    conn.close()
    return [{"name": row[0], "category": row[1]} for row in rows]

@app.post("/api/exercises")
def add_new_exercise(exercise: NewExercise):
    conn = get_db_connection()
    cursor = conn.cursor()
    q = "INSERT INTO exercises (name, category) VALUES (%s, %s)" if DATABASE_URL else "INSERT INTO exercises (name, category) VALUES (?, ?)"
    try:
        cursor.execute(q, (exercise.name, exercise.category))
        conn.commit()
        return {"status": "success"}
    except:
        raise HTTPException(status_code=400, detail="이미 존재하거나 등록 실패")
    finally:
        conn.close()

@app.get("/api/exercises/{exercise_name}/latest")
def get_latest_exercise_record(exercise_name: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    q = "SELECT weight, reps FROM workout_sets WHERE exercise_name = %s ORDER BY id DESC LIMIT 5" if DATABASE_URL else "SELECT weight, reps FROM workout_sets WHERE exercise_name = ? ORDER BY id DESC LIMIT 5"
    cursor.execute(q, (exercise_name,))
    rows = cursor.fetchall()
    conn.close()
    return {"latest": [f"{row[0]}kg x {row[1]}" for row in rows] if rows else "기록 없음"}

@app.get("/api/workouts/dates")
def get_workout_dates():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT date FROM workout_sessions ORDER BY date ASC")
    rows = cursor.fetchall()
    conn.close()
    return {"workout_dates": [row[0] for row in rows]}

@app.get("/api/workouts/by-date/{date}")
def get_workout_by_date(date: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    q1 = "SELECT id, title FROM workout_sessions WHERE date = %s" if DATABASE_URL else "SELECT id, title FROM workout_sessions WHERE date = ?"
    cursor.execute(q1, (date,))
    session = cursor.fetchone()
    if not session:
        conn.close()
        return {"has_data": False}
    session_id, title = session
    
    q2 = "SELECT exercise_name, COUNT(id) FROM workout_sets WHERE session_id = %s GROUP BY exercise_name" if DATABASE_URL else "SELECT exercise_name, COUNT(id) FROM workout_sets WHERE session_id = ? GROUP BY exercise_name"
    cursor.execute(q2, (session_id,))
    rows = cursor.fetchall()
    conn.close()
    return {"has_data": True, "title": title, "exercises": [{"name": r[0], "sets": r[1]} for r in rows]}

@app.get("/api/exercises/{exercise_name}/1rm-history")
def get_1rm_history(exercise_name: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    q = """
        SELECT s.date, w.weight, w.reps FROM workout_sets w
        JOIN workout_sessions s ON w.session_id = s.id
        WHERE w.exercise_name = %s ORDER BY s.date ASC
    """ if DATABASE_URL else """
        SELECT s.date, w.weight, w.reps FROM workout_sets w
        JOIN workout_sessions s ON w.session_id = s.id
        WHERE w.exercise_name = ? ORDER BY s.date ASC
    """
    cursor.execute(q, (exercise_name,))
    rows = cursor.fetchall()
    conn.close()
    
    if not rows: return {"labels": [], "data": []}
    daily_max = {}
    for date, w, r in rows:
        val = round(w * (1 + r / 30), 1)
        if date not in daily_max or val > daily_max[date]: daily_max[date] = val
    return {"labels": list(daily_max.keys()), "data": list(daily_max.values())}

@app.get("/api/workouts/search")
def search_past_workouts(date: Optional[str] = None, exercise_name: Optional[str] = None):
    conn = get_db_connection()
    cursor = conn.cursor()
    if date and not exercise_name:
        q = "SELECT s.title, w.exercise_name, w.set_num, w.weight, w.reps FROM workout_sets w JOIN workout_sessions s ON w.session_id = s.id WHERE s.date = %s ORDER BY w.id ASC" if DATABASE_URL else "SELECT s.title, w.exercise_name, w.set_num, w.weight, w.reps FROM workout_sets w JOIN workout_sessions s ON w.session_id = s.id WHERE s.date = ? ORDER BY w.id ASC"
        cursor.execute(q, (date,))
    elif exercise_name and not date:
        q1 = "SELECT session_id FROM workout_sets WHERE exercise_name = %s ORDER BY id DESC LIMIT 1" if DATABASE_URL else "SELECT session_id FROM workout_sets WHERE exercise_name = ? ORDER BY id DESC LIMIT 1"
        cursor.execute(q1, (exercise_name,))
        res = cursor.fetchone()
        if not res: conn.close(); return {"sets": []}
        q2 = "SELECT s.title, w.exercise_name, w.set_num, w.weight, w.reps FROM workout_sets w JOIN workout_sessions s ON w.session_id = s.id WHERE w.session_id = %s AND w.exercise_name = %s ORDER BY w.set_num ASC" if DATABASE_URL else "SELECT s.title, w.exercise_name, w.set_num, w.weight, w.reps FROM workout_sets w JOIN workout_sessions s ON w.session_id = s.id WHERE w.session_id = ? AND w.exercise_name = ? ORDER BY w.set_num ASC"
        cursor.execute(q2, (res[0], exercise_name))
    else:
        conn.close(); return {"error": "파라미터 누락"}
    rows = cursor.fetchall()
    conn.close()
    return {"sets": [{"title": r[0], "exercise_name": r[1], "set_num": r[2], "weight": r[3], "reps": r[4]} for r in rows]}

# --- 클라우드 서빙용 통합 루트 라우트 추가 ---
@app.get("/", response_class=HTMLResponse)
def read_root():
    with open("index.html", "r", encoding="utf-8") as f:
        return f.read()


# icon 불러오기
@app.get("/static/icon.png")
def get_icon():
    from fastapi.responses import FileResponse
    return FileResponse("icon.png")
