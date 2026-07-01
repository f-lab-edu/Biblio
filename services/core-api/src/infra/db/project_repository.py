from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Select, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.admin_ops import Project
from src.models.video import Video


@dataclass(slots=True)
class ProjectWithVideoCount:
    project: Project
    video_count: int


class ProjectRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, project: Project) -> Project:
        self.session.add(project)
        await self.session.flush() # 세션에 쌓인 변경을 DB로 보냄(INSERT INTO project  ...)
        await self.session.refresh(project) # DB에 저장된 최신 값을 다시 읽어서 project 객체로 => DB가 자동으로 채운 값을 Python 객체( ProjectResponse)에도 넣기 위해
        return project

    async def get_for_user(self, project_id: UUID, requester_user_id: UUID) -> Project | None:
        result = await self.session.execute(
            select(Project).where(
                Project.id == project_id,
                Project.user_id == requester_user_id,
            )
        )
        return result.scalar_one_or_none() 

    # 유저의 프로젝트 목록 반환
    async def list_for_user(self, requester_user_id: UUID) -> list[ProjectWithVideoCount]:
        statement = self._list_statement(requester_user_id) # 프로젝트 목록 + video_count 집계 쿼리 생성
        result = await self.session.execute(statement) # 생성한 쿼리를 DB에서 실행
        return [
            ProjectWithVideoCount(project=project, video_count=video_count) # row를 서비스가 쓰기 좋은 형태로 변환
            for project, video_count in result.all()  # result.all() => 실행 결과 row 전체 조회: (Project, video_count)
        ]

    async def mark_deleting(self, project: Project) -> Project:
        project.lifecycle_state = "DELETING"
        await self.session.flush()
        return project

    async def update_title(self, project: Project, title: str) -> Project:
        project.title = title
        await self.session.flush()
        await self.session.refresh(project)
        return project

    async def count_active_videos(self, project_id: UUID) -> int:
        result = await self.session.execute(
            select(func.count(Video.id)).where(
                Video.project_id == project_id,
                Video.status != "DELETING",
            )
        )
        return result.scalar_one()

    # 유저의 프로젝트 목록을 가져오면서, 각 프로젝트마다 삭제 중이 아닌 영상 개수를 같이 집계
    @staticmethod
    def _list_statement(requester_user_id: UUID) -> Select[tuple[Project, int]]:
        return (
            select(Project, func.count(Video.id).label("video_count")) # 영상이 없어는 project도 join 해야 하므로 outer join 사용
            .outerjoin(
                Video,
                and_(
                    Video.project_id == Project.id,
                    Video.status != "DELETING",
                ),
            )
            .where(
                Project.user_id == requester_user_id,
                Project.lifecycle_state != "DELETING",
            )
            .group_by(Project.id)
            .order_by(Project.created_at.desc(), Project.id.desc())
        )
