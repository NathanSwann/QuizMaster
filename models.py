from sqlalchemy import (
    Column,
    Integer,
    String,
    ForeignKey,
    Boolean,
    DateTime,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()


class Quiz(Base):
    __tablename__ = "quizzes"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String)

    questions = relationship("Question", back_populates="quiz", cascade="all, delete")


class Question(Base):
    __tablename__ = "questions"
    id = Column(Integer, primary_key=True, index=True)
    quiz_id = Column(Integer, ForeignKey("quizzes.id"))
    round = Column(String)
    question_type = Column(String)
    question = Column(String)
    options = Column(String)  # Stored as | joined string
    correct_options = Column(String)
    quiz = relationship("Quiz", back_populates="questions")


class LiveSession(Base):
    __tablename__ = "live_sessions"
    id = Column(Integer, primary_key=True)
    quiz_id = Column(Integer, ForeignKey("quizzes.id"))
    current_question_index = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    round_break = Column(Boolean, default=False)

    quiz = relationship("Quiz")


class Participant(Base):
    __tablename__ = "participants"
    id = Column(Integer, primary_key=True)
    name = Column(String)
    session_id = Column(Integer, ForeignKey("live_sessions.id"))


class Answer(Base):
    __tablename__ = "answers"
    id = Column(Integer, primary_key=True)
    participant_id = Column(Integer, ForeignKey("participants.id"))
    question_id = Column(Integer, ForeignKey("questions.id"))
    answer = Column(String)
    submission_time = Column(DateTime)
    correct_override = Column(Boolean, default=False)
    disputed = Column(Boolean, default=False)
    checked = Column(Boolean, default=False)
