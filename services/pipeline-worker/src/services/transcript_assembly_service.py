from dataclasses import dataclass
from uuid import UUID

from src.infra.ai.google_stt_adapter import (
    TranscriptSegmentDTO,
    TranscriptWordDTO,
    drain_segments,
)
from src.services.chunking_service import (
    ChunkDraft,
    ChunkingService,
    SentenceFragment,
)
from src.services.transcript_merge_service import AudioPart, TranscriptMergeService
from src.services.transcription_artifact import TranscriptionArtifact


@dataclass(frozen=True, slots=True)
class AssemblyPart:
    """조립이 참조하는 audio part 정보.

    start_ms, end_ms는 실제 STT 결과가 아니라 normalization이 계획한 경계다.
    part끼리 겹치는 구간을 계산할 때 이 값을 쓴다.
    """

    pipeline_run_id: UUID
    audio_part_id: UUID
    part_index: int
    start_ms: int
    end_ms: int
    audio_gcs_path: str
    stt_model_version: str
    status: str
    result_ref: str | None

    def as_audio_part(self) -> AudioPart:
        return AudioPart(
            index=self.part_index,
            start_ms=self.start_ms,
            end_ms=self.end_ms,
            storage_path=self.audio_gcs_path,
        )


@dataclass(slots=True)
class AssemblyProgress:
    """advance() 한 번의 결과.

    segments와 chunks는 저장하러 나가는 산출물이고,
    나머지는 다음 advance() 호출에 그대로 되먹이는 이월 상태다.
    입력과 출력에 같은 이름이 있는 이유가 이것이다.
    """

    segments: list[TranscriptSegmentDTO]
    chunks: list[ChunkDraft]
    pending_words: list[TranscriptWordDTO]
    chunk_buffer: list[SentenceFragment]
    next_part_index: int
    next_chunk_index: int
    completed: bool


class TranscriptAssemblyService:
    """완료된 part의 STT 결과를 문장과 검색 청크로 바꾼다.

    DB도 Queue도 건드리지 않는 동기 계산이다. 조립 상태를 객체가 들고 있지 않고
    인자로 받아 새 상태를 돌려주므로, 프로세스가 죽어도 저장된 상태에서 이어갈 수 있다.
    저장과 발행은 호출하는 쪽(infra/db/transcript_assembly.py)이 맡는다.
    """

    def __init__(
        self,
        *,
        merge_service: TranscriptMergeService,
        chunking_service: ChunkingService,
    ) -> None:
        self._merge_service = merge_service
        self._chunking_service = chunking_service

    @property
    def chunking_version(self) -> str:
        return self._chunking_service.chunking_version

    def advance(
        self,
        *,
        all_parts: list[AssemblyPart],
        artifacts: list[TranscriptionArtifact],
        duration_ms: int,
        next_part_index: int,
        next_chunk_index: int,
        pending_words: list[TranscriptWordDTO],
        chunk_buffer: list[SentenceFragment],
        final_flush: bool,
    ) -> AssemblyProgress:
        """중복 제거 → 문장 확정 → 청크 조립을 한 번에 수행한다.

        artifacts에는 next_part_index부터 끊기지 않고 이어진 part만 담겨 들어온다.
        중간이 비어 있으면 호출하는 쪽이 애초에 이 함수를 부르지 않는다.
        final_flush는 마지막 part까지 반영했다는 뜻이며, 이때만 남은 단어와
        상한 미달 청크까지 전부 확정한다.
        """
        # 겹침 계산에 앞뒤 part 경계가 필요해서 index로 바로 찾을 수 있게 바꾼다.
        parts_by_index = {part.part_index: part for part in all_parts}
        owned_words: list[TranscriptWordDTO] = []
        for artifact in artifacts:
            part = parts_by_index[artifact.part_index]
            self._validate_artifact(part, artifact)
            # part 경계는 5초씩 겹쳐 있어 같은 발화가 양쪽 STT 결과에 모두 들어 있다.
            # 겹침 구간 중앙을 기준으로 단어를 한쪽 part에만 귀속시켜 중복을 걷어내고,
            # part 기준 상대 시각을 영상 전체 시각으로 바꾼다.
            # 기준이 시간으로 고정돼 있어 part 도착 순서가 결과를 바꾸지 않는다.
            owned_words.extend(
                self._merge_service.owned_words_for_part(
                    part=part.as_audio_part(),
                    relative_words=artifact.words,
                    duration_ms=duration_ms,
                    previous_part=self._neighbor(parts_by_index, artifact.part_index - 1),
                    next_part=self._neighbor(parts_by_index, artifact.part_index + 1),
                )
            )

        # 지난 호출이 남긴 pending_words를 앞에 붙인 뒤 문장 경계를 찾는다.
        # 구두점이나 100단어 상한에서 문장을 닫고, 닫지 못한 단어가 새 pending_words가 된다.
        # 문장을 먼저 확정해야 청크가 문장 중간에서 잘리지 않는다.
        drained = drain_segments(
            owned_words,
            pending_words=pending_words,
            flush=final_flush,
        )
        # 확정된 문장을 이전 chunk_buffer에 이어 쌓고 상한을 넘길 때만 청크를 만든다.
        # 상한에 못 미친 문장은 buffer에 남아 다음 part를 기다린다.
        # pending_words와 chunk_buffer는 서로 다른 층의 잔여물이라 따로 관리한다.
        chunking = self._chunking_service.append_segments(
            drained.segments,
            buffer=chunk_buffer,
            next_chunk_index=next_chunk_index,
            flush=final_flush,
        )
        return AssemblyProgress(
            segments=drained.segments,
            chunks=chunking.chunks,
            pending_words=drained.pending_words,
            chunk_buffer=chunking.buffer,
            # 이어진 구간만 들어오므로 처리한 개수만큼 그대로 전진시킬 수 있다.
            next_part_index=next_part_index + len(artifacts),
            next_chunk_index=chunking.next_chunk_index,
            completed=final_flush,
        )

    @staticmethod
    def _neighbor(
        parts_by_index: dict[int, AssemblyPart],
        part_index: int,
    ) -> AudioPart | None:
        # 첫 part의 앞과 마지막 part의 뒤에는 이웃이 없다. 그 바깥에는 겹침도 없다.
        part = parts_by_index.get(part_index)
        return part.as_audio_part() if part is not None else None

    @staticmethod
    def _validate_artifact(
        part: AssemblyPart,
        artifact: TranscriptionArtifact,
    ) -> None:
        # 저장된 STT 결과가 지금 계획된 part와 같은 것인지 확인한다.
        # 설정이 바뀌어 part 경계나 모델 버전이 달라졌는데 옛 결과를 그대로 쓰면
        # 어긋난 transcript가 조용히 만들어지므로, 넘기지 않고 실패시킨다.
        if not artifact.matches(
            pipeline_run_id=part.pipeline_run_id,
            audio_part_id=part.audio_part_id,
            part_index=part.part_index,
            start_ms=part.start_ms,
            end_ms=part.end_ms,
            stt_model_version=part.stt_model_version,
        ):
            raise ValueError("Transcription artifact identity mismatch during assembly")
