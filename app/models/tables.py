from datetime import datetime
from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Integer, String, Text, JSON
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base


class AppUser(Base):
    __tablename__ = "app_user"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    department: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    jobs: Mapped[list["SearchJob"]] = relationship("SearchJob", back_populates="creator")
    exports: Mapped[list["ExportJob"]] = relationship("ExportJob", back_populates="executor")
    reviews: Mapped[list["ReviewNote"]] = relationship("ReviewNote", back_populates="reviewer")


class SearchJob(Base):
    __tablename__ = "search_job"

    job_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("app_user.user_id"))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    note: Mapped[str | None] = mapped_column(Text)

    creator: Mapped["AppUser | None"] = relationship("AppUser", back_populates="jobs")
    query: Mapped["SearchQuery | None"] = relationship("SearchQuery", back_populates="job", uselist=False)
    seed_papers: Mapped[list["SeedPaper"]] = relationship("SeedPaper", back_populates="job")
    candidates: Mapped[list["PaperCandidate"]] = relationship("PaperCandidate", back_populates="job")
    rankings: Mapped[list["RankingResult"]] = relationship("RankingResult", back_populates="job")
    exports: Mapped[list["ExportJob"]] = relationship("ExportJob", back_populates="job")
    api_logs: Mapped[list["ApiLog"]] = relationship("ApiLog", back_populates="job")


class SearchQuery(Base):
    __tablename__ = "search_query"

    query_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(Integer, ForeignKey("search_job.job_id"), nullable=False)
    theme_text: Mapped[str] = mapped_column(Text, nullable=False)
    year_from: Mapped[int | None] = mapped_column(Integer)
    year_to: Mapped[int | None] = mapped_column(Integer)
    include_terms: Mapped[str | None] = mapped_column(Text)
    exclude_terms: Mapped[str | None] = mapped_column(Text)
    subject_tags: Mapped[str | None] = mapped_column(Text)
    max_candidates: Mapped[int] = mapped_column(Integer, default=200)
    use_abstract: Mapped[bool] = mapped_column(Boolean, default=True)

    job: Mapped["SearchJob"] = relationship("SearchJob", back_populates="query")


class SeedPaper(Base):
    __tablename__ = "seed_paper"

    seed_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(Integer, ForeignKey("search_job.job_id"), nullable=False)
    doi: Mapped[str | None] = mapped_column(String(255))
    url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)

    job: Mapped["SearchJob"] = relationship("SearchJob", back_populates="seed_papers")


class Paper(Base):
    __tablename__ = "paper"

    paper_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doi: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    abstract: Mapped[str | None] = mapped_column(Text)
    year: Mapped[int | None] = mapped_column(Integer)
    journal: Mapped[str | None] = mapped_column(Text)
    authors_json: Mapped[dict | None] = mapped_column(JSON)
    source_primary: Mapped[str | None] = mapped_column(String(50))
    openalex_id: Mapped[str | None] = mapped_column(String(100), index=True)
    semantic_scholar_id: Mapped[str | None] = mapped_column(String(100), index=True)
    citation_count: Mapped[int] = mapped_column(Integer, default=0)
    paper_type: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    candidates: Mapped[list["PaperCandidate"]] = relationship("PaperCandidate", back_populates="paper")


class PaperCandidate(Base):
    __tablename__ = "paper_candidate"

    candidate_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(Integer, ForeignKey("search_job.job_id"), nullable=False)
    paper_id: Mapped[int] = mapped_column(Integer, ForeignKey("paper.paper_id"), nullable=False)
    discovery_path: Mapped[str | None] = mapped_column(String(100))
    source_score_raw: Mapped[float | None] = mapped_column(Float)
    is_deduped: Mapped[bool] = mapped_column(Boolean, default=False)
    is_shortlisted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    job: Mapped["SearchJob"] = relationship("SearchJob", back_populates="candidates")
    paper: Mapped["Paper"] = relationship("Paper", back_populates="candidates")
    score: Mapped["ScoreDetail | None"] = relationship("ScoreDetail", back_populates="candidate", uselist=False)
    rankings: Mapped[list["RankingResult"]] = relationship("RankingResult", back_populates="candidate")


class ScoreDetail(Base):
    __tablename__ = "score_detail"

    score_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[int] = mapped_column(Integer, ForeignKey("paper_candidate.candidate_id"), nullable=False, unique=True)
    theme_score: Mapped[float] = mapped_column(Float, default=0.0)
    theme_embed_score: Mapped[float] = mapped_column(Float, default=0.0)
    theme_text_score: Mapped[float] = mapped_column(Float, default=0.0)
    theme_keyword_score: Mapped[float] = mapped_column(Float, default=0.0)
    method_score: Mapped[float] = mapped_column(Float, default=0.0)
    method_rule_score: Mapped[float] = mapped_column(Float, default=0.0)
    method_llm_score: Mapped[float] = mapped_column(Float, default=0.0)
    recency_score: Mapped[float] = mapped_column(Float, default=0.0)
    impact_score: Mapped[float] = mapped_column(Float, default=0.0)
    readability_score: Mapped[float] = mapped_column(Float, default=0.0)
    role_bonus: Mapped[float] = mapped_column(Float, default=0.0)
    total_score: Mapped[float] = mapped_column(Float, default=0.0)
    scoring_version: Mapped[str] = mapped_column(String(20), default="1.0")
    scored_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    candidate: Mapped["PaperCandidate"] = relationship("PaperCandidate", back_populates="score")


class RankingResult(Base):
    __tablename__ = "ranking_result"

    rank_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(Integer, ForeignKey("search_job.job_id"), nullable=False)
    candidate_id: Mapped[int] = mapped_column(Integer, ForeignKey("paper_candidate.candidate_id"), nullable=False)
    final_rank: Mapped[int | None] = mapped_column(Integer)
    decision: Mapped[str] = mapped_column(String(20), default="pending")
    reason_text: Mapped[str | None] = mapped_column(Text)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime)

    job: Mapped["SearchJob"] = relationship("SearchJob", back_populates="rankings")
    candidate: Mapped["PaperCandidate"] = relationship("PaperCandidate", back_populates="rankings")
    notes: Mapped[list["ReviewNote"]] = relationship("ReviewNote", back_populates="ranking")


class ReviewNote(Base):
    __tablename__ = "review_note"

    note_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rank_id: Mapped[int] = mapped_column(Integer, ForeignKey("ranking_result.rank_id"), nullable=False)
    reviewer_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("app_user.user_id"))
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    ranking: Mapped["RankingResult"] = relationship("RankingResult", back_populates="notes")
    reviewer: Mapped["AppUser | None"] = relationship("AppUser", back_populates="reviews")


class ExportJob(Base):
    __tablename__ = "export_job"

    export_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(Integer, ForeignKey("search_job.job_id"), nullable=False)
    format: Mapped[str] = mapped_column(String(10), nullable=False)
    output_path: Mapped[str | None] = mapped_column(Text)
    executed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    executed_by: Mapped[int | None] = mapped_column(Integer, ForeignKey("app_user.user_id"))

    job: Mapped["SearchJob"] = relationship("SearchJob", back_populates="exports")
    executor: Mapped["AppUser | None"] = relationship("AppUser", back_populates="exports")


class ApiLog(Base):
    __tablename__ = "api_log"

    log_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(Integer, ForeignKey("search_job.job_id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    endpoint: Mapped[str | None] = mapped_column(Text)
    request_summary: Mapped[str | None] = mapped_column(Text)
    status_code: Mapped[int | None] = mapped_column(Integer)
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    job: Mapped["SearchJob"] = relationship("SearchJob", back_populates="api_logs")
