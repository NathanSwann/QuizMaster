"""
How do we actually score a question??
"""

from models import *
import polars as pl
from sqlalchemy import func


def mark_correct(row) -> float:
    if row[5] == "NUMBER":
        return 0
    if row[5] == "TEXT":
        answer = "".join(ch for ch in row[6][0].lower() if ch.isalnum())
        options = ["".join(ch for ch in opt.lower() if ch.isalnum()) for opt in row[4]]
        return 1.0 if answer in options else 0.0
    if row[5] == "ORDER":
        answer = row[6]
        coptions = row[4]
        return sum([1.0 if x == y else 0.0 for x, y in zip(answer, coptions)]) / len(
            coptions
        )
    if row[5] == "MC":
        answer = row[6]
        coptions = row[4]
        options = row[3]
        cmask = [1.0 if x in coptions else 0 for x in options]
        amask = [1.0 if x in answer else 0 for x in options]
        return max(
            sum([1.0 if x == y else -1.0 for x, y in zip(cmask, amask)]) / len(options),
            0.0,
        )
    return 0.0


def score_session(session_id, db):
    """
    Get question wise score sheet
    """

    answers = (
        db.query(
            Answer.id.label("answer_id"),
            Answer.participant_id,
            Answer.question_id,
            Participant.name,
            Question.options,
            Question.question_type,
            Question.correct_options,
            Answer.answer,
            Answer.submission_time,
            Answer.correct_override,
        )
        .join(Participant)
        .join(Question)
        .filter(Participant.session_id == session_id)
        .all()
    )
    data = [
        {
            "answer_id": row.answer_id,
            "participant_id": row.participant_id,
            "question_id": row.question_id,
            "options": row.options.split("|"),
            "correct_options": row.correct_options.split("|"),
            "question_type": row.question_type,
            "answer": row.answer.split("|"),
            "submission_time": row.submission_time,
            "correct_override": row.correct_override,
            "participant_name": row.name,
        }
        for row in answers
    ]
    if len(data) == 0:
        return None
    df = pl.DataFrame(
        data,
        schema={
            "answer_id": pl.Int64,
            "participant_id": pl.Int64,
            "question_id": pl.Int64,
            "options": pl.List(pl.String),
            "correct_options": pl.List(pl.String),
            "question_type": pl.String,
            "answer": pl.List(pl.String),
            "submission_time": pl.Datetime,
            "correct_override": pl.Boolean,
            "participant_name": pl.String,
        },
    )
    df = df.with_columns(df.map_rows(mark_correct, return_dtype=pl.datatypes.Float64))

    numeric_correct = pl.Series(
        df.filter(pl.col("question_type") == "NUMBER")
        .select(
            pl.col("answer").list.first().cast(float),
            pl.col("correct_options").list.first().cast(float),
            pl.col("question_id"),
            pl.col("answer_id"),
        )
        .with_columns(
            (pl.col("correct_options") - pl.col("answer")).abs().alias("diff")
        )
        .filter(pl.col("diff") == pl.col("diff").min().over("question_id"))
        .select("answer_id")
    ).to_list()

    df = df.with_columns(
        pl.when(pl.col("answer_id").is_in(numeric_correct))
        .then(pl.col("map") + 1.0)
        .otherwise(pl.col("map")),
    )
    df = df.with_columns(
        pl.when(
            (pl.col("map") == 1.0)
            & (
                pl.col("submission_time")
                == pl.col("submission_time").max().over("question_id")
            )
        )
        .then(1.0)
        .otherwise(0.0)
        .alias("first_points"),
    )
    return df.select(
        pl.col("question_id"),
        pl.col("participant_id"),
        pl.col("participant_name"),
        pl.col("map").alias("points"),
        pl.col("first_points"),
        (pl.col("map") + pl.col("first_points")).alias("score"),
    )


def get_player_scores(session_id, db):
    df = score_session(session_id, db)
    if df == None:
        return []
    scores = df.group_by("participant_id").agg(
        pl.col("score").sum(),
        pl.col("first_points").sum(),
    )
    answered = pl.DataFrame(
        [
            {"participant_id": x.participant_id, "answered": "yes"}
            for x in db.query(Answer)
            .join(Participant)
            .filter(
                Answer.question_id
                == (
                    db.query(func.max(Answer.question_id))
                    .join(Participant)
                    .filter(Participant.session_id == session_id)
                    .scalar_subquery()
                )
            )
            .all()
        ]
    )
    return (
        pl.DataFrame(
            [
                {"participant_id": x.id, "participant_name": x.name}
                for x in db.query(Participant)
                .filter(Participant.session_id == session_id)
                .all()
            ]
        )
        .join(scores, on="participant_id", how="left")
        .join(answered, on="participant_id", how="left")
        .with_columns(
            pl.col("answered").fill_null("no"),
            pl.col("score").fill_null(0),
            pl.col("first_points").fill_null(0),
        )
        .sort(by="score", descending=True)
        .to_dicts()
    )
