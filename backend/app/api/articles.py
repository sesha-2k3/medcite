"""GET /v1/articles/{pmid}: a stored article with its passages, for the source panel."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request

from app.api.schemas import ArticleResponse, PassageOut
from app.auth.deps import current_user
from app.auth.users import User
from app.db.queries.articles import ArticleRepository
from app.errors import NotFoundError
from app.retrieval.evidence import grade_study

router = APIRouter(prefix="/v1", tags=["articles"])
PUBMED_URL = "https://pubmed.ncbi.nlm.nih.gov/{pmid}/"


@router.get("/articles/{pmid}", response_model=ArticleResponse)
async def get_article(
    pmid: Annotated[int, Path(gt=0)],
    request: Request,
    user: Annotated[User, Depends(current_user)],
) -> ArticleResponse:
    articles: ArticleRepository = request.app.state.articles
    article = await articles.get(pmid)
    if article is None:
        raise NotFoundError(f"PMID {pmid} is not in the local index")
    grade = grade_study(article.study_meta, publication_status=article.publication_status)
    return ArticleResponse(
        pmid=article.pmid,
        title=article.title,
        journal=article.journal,
        pub_date=article.pub_date,
        doi=article.doi,
        pub_types=list(article.pub_types),
        publication_status=article.publication_status,
        is_retracted=article.is_retracted,
        design=grade.design,
        badge=grade.badge,
        study_meta=article.study_meta,
        pubmed_url=PUBMED_URL.format(pmid=article.pmid),
        passages=[
            PassageOut(
                chunk_id=p.chunk_id, section=p.section, chunk_index=p.chunk_index, content=p.content
            )
            for p in article.passages
        ],
    )
