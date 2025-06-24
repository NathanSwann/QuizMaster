from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from enum import Enum
from typing import List
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
import datetime
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
import scoring
import polars as pl

from models import Base, Quiz, Question, Answer, LiveSession, Participant

# --- Database Setup ---
DATABASE_URL = "sqlite:///./quizzes.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class QuestionTypeEnum(str, Enum):
    text = "TEXT"
    multiple_choice = "MC"
    order = "ORDER"
    number = "NUMBER"


# --- Pydantic Schemas ---
class QuestionCreate(BaseModel):
    question: str
    round: str
    question_type: QuestionTypeEnum
    options: List[str]
    correct_options: List[str]


class QuizCreate(BaseModel):
    title: str


class AnswerSubmission(BaseModel):
    user_id: int
    answer: str


# --- FastAPI App ---
app = FastAPI()

Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- API Routes ---
@app.post("/quiz")
def create_quiz(quiz: QuizCreate, db: Session = Depends(get_db)):
    db_quiz = Quiz(title=quiz.title)
    db.add(db_quiz)
    db.commit()
    db.refresh(db_quiz)
    return {"quiz_id": db_quiz.id, "message": "Quiz created."}


@app.get("/quiz")
def list_quizes(db: Session = Depends(get_db)):
    return {"quizes": {x.id: {"title": x.title} for x in db.query(Quiz).all()}}


@app.post("/quiz/{quiz_id}/question")
def add_question(quiz_id: int, question: QuestionCreate, db: Session = Depends(get_db)):
    db_quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
    if not db_quiz:
        raise HTTPException(status_code=404, detail="Quiz not found")

    question_record = Question(
        quiz_id=quiz_id,
        question=question.question,
        question_type=question.question_type,
        round=question.round,
        options="|".join(question.options),  # simple serialization
        correct_options="|".join(question.correct_options),
    )
    db.add(question_record)
    db.commit()
    return {"message": "Question added."}


@app.get("/quiz/{quiz_id}")
def get_quiz(quiz_id: int, db: Session = Depends(get_db)):
    db_quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
    if not db_quiz:
        raise HTTPException(status_code=404, detail="Quiz not found")

    questions = [
        {
            "question": q.question,
            "options": q.options.split("|"),
            "question_type": q.question_type,
            "round": q.round,
        }
        for q in db_quiz.questions
    ]
    return {"title": db_quiz.title, "questions": questions}


@app.post("/quiz/{quiz_id}/question/{question_id}/submit")
def submit_answers(
    quiz_id: int,
    question_id: int,
    submission: AnswerSubmission,
    db: Session = Depends(get_db),
):
    db_quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
    if not db_quiz:
        raise HTTPException(status_code=404, detail="Quiz not found")
    q = (
        db.query(Answer)
        .filter(
            (Answer.participant_id == submission.participant_id) & Answer.question_id
            == question_id
        )
        .first()
    )
    if q:
        raise HTTPException(status_code=400, detail="Question already submitted")

    answer = Answer(
        participant_id=submission.participant_id,
        question_id=question_id,
        answer=submission.answer,
        submission_time=datetime.datetime.now(),
    )
    db.add(answer)
    db.commit()
    return {}


@app.get("/quiz/{quiz_id}/result")
def get_result(quiz_id: int, db: Session = Depends(get_db)):
    db_quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
    if not db_quiz:
        raise HTTPException(status_code=404, detail="Quiz not found")
    if not db_quiz.submitted:
        raise HTTPException(status_code=400, detail="Quiz not submitted yet")
    return {"score": db_quiz.score, "total": len(db_quiz.questions)}


@app.post("/quiz/{quiz_id}/live/start")
def start_live_session(quiz_id: int, db: Session = Depends(get_db)):
    quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
    if not quiz:
        raise HTTPException(status_code=404, detail="Quiz not found")

    session = LiveSession(quiz_id=quiz_id)
    db.add(session)
    db.commit()
    db.refresh(session)
    return {"session_id": session.id}


class JoinRequest(BaseModel):
    name: str


@app.post("/live/{session_id}/join")
def join_session(session_id: int, join: JoinRequest, db: Session = Depends(get_db)):
    session = (
        db.query(LiveSession)
        .filter(LiveSession.id == session_id, LiveSession.is_active)
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Live session not found or ended")

    participant = Participant(name=join.name, session_id=session_id)
    db.add(participant)
    db.commit()
    db.refresh(participant)
    return {"participant_id": participant.id}


@app.get("/user/{user_id}")
def get_user_info(user_id: int, db: Session = Depends(get_db)):
    session = db.query(Participant).filter(Participant.id == user_id).first()
    return session


@app.post("/live/{session_id}/next")
def advance_question(session_id: int, db: Session = Depends(get_db)):
    session = (
        db.query(LiveSession)
        .filter(LiveSession.id == session_id, LiveSession.is_active == True)
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Live session not active")

    quiz = session.quiz
    if session.current_question_index + 1 >= len(quiz.questions):
        session.is_active = False
    else:
        session.current_question_index += 1
    session.round_break = False

    db.commit()
    return {
        "current_question_index": session.current_question_index,
        "active": session.is_active,
    }


@app.post("/live/{session_id}/round_break")
def round_break(session_id: int, db: Session = Depends(get_db)):
    session = (
        db.query(LiveSession)
        .filter(LiveSession.id == session_id, LiveSession.is_active == True)
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Live session not active")
    session.round_break = True
    db.commit()
    return {"msg": "paused"}


@app.get("/live/{session_id}/question")
def get_current_question(session_id: int, db: Session = Depends(get_db)):
    session = db.query(LiveSession).filter(LiveSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Live session not found")

    quiz = session.quiz
    if not session.is_active:
        return {"message": "Quiz finished"}

    question = quiz.questions[session.current_question_index]
    return {
        "question_id": question.id,
        "question": question.question,
        "options": question.options.split("|"),
        "question_type": question.question_type,
        "question_index": session.current_question_index,
        "on_round_break": session.round_break,
        "round": question.round,
    }


@app.get("/live/{session_id}/quiz")
def get_session(session_id: int, db: Session = Depends(get_db)):
    session = db.query(LiveSession).filter(LiveSession.id == session_id).first()

    questions = [
        {
            "question": q.question,
            "options": q.options.split("|"),
            "question_type": q.question_type,
            "round": q.round,
        }
        for q in session.quiz.questions
    ]
    return {"title": session.quiz.title, "questions": questions}


class AnswerInput(BaseModel):
    participant_id: int
    answer: str


@app.post("/live/{session_id}/answer")
def submit_answer(session_id: int, answer: AnswerInput, db: Session = Depends(get_db)):
    session = db.query(LiveSession).filter(LiveSession.id == session_id).first()
    quiz = session.quiz
    question = quiz.questions[session.current_question_index]

    answer = Answer(
        participant_id=answer.participant_id,
        question_id=question.id,
        answer=answer.answer,
        submission_time=datetime.datetime.now(),
    )
    db.add(answer)
    db.commit()

    return {"message": "Answer recorded"}


class AnswerRequest(BaseModel):
    participant_id: int


@app.get("/live/{session_id}/my_answers/{participant_id}")
def my_answers(session_id: int, participant_id: int, db: Session = Depends(get_db)):
    scores = scoring.score_session(session_id, db)
    if scores is None:
        return {}
    scores.filter((pl.col("participant_id") == participant_id) & (pl.col("score") < 1))
    answer_info = pl.DataFrame(
        db.query(
            Answer.id.label("answer_id"), Answer.disputed, Answer.checked, Answer.answer
        )
        .filter(Answer.participant_id == participant_id)
        .all()
    )
    question_info = pl.DataFrame(
        db.query(
            Question.id.label("question_id"), Question.question.label("question_text")
        ).all()
    )
    return (
        scores.join(answer_info, how="left", on="answer_id")
        .join(question_info, how="left", on="question_id")
        .to_dicts()
    )


@app.post("/live/dispute/{answer_id}")
def dispute(answer_id: int, db: Session = Depends(get_db)):

    answer = db.query(Answer).filter(Answer.id == answer_id).first()
    if not answer:
        raise HTTPException(status_code=404, detail="Live session not active")
    answer.disputed = True
    db.commit()
    return {"msg": "disputed"}


@app.get("/live/{session_id}/results")
def get_results(session_id: int, db: Session = Depends(get_db)):
    res = scoring.get_player_scores(session_id, db)
    return {"results": res}


static_path = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_path), name="static")


@app.get("/", response_class=HTMLResponse)
def root():
    return (static_path / "participant.html").read_text()


@app.get("/runner", response_class=HTMLResponse)
def runner():
    return (static_path / "runner.html").read_text()


@app.get("/results", response_class=HTMLResponse)
def results():
    return (static_path / "results.html").read_text()


@app.get("/makequiz", response_class=HTMLResponse)
def make_quiz_show():
    return (static_path / "make_quiz.html").read_text()


@app.get("/review_answers", response_class=HTMLResponse)
def review_answers():
    return (static_path / "review_answers.html").read_text()


@app.get("/manage_tokens", response_class=HTMLResponse)
def manage_tokens():
    return (static_path / "delete_tokens.html").read_text()
