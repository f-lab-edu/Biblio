# 2026-08-24 Chirp 3 word offset failure

- Worker image: `pipeline-worker:70dedf8`
- Cloud Run revision: `pipeline-worker-00083-tlq`
- Fixture SHA-256: `3356d8882b38b240f49d401c74938877015326e63bd9ab4f9d90bcc57920f145`
- Trace ID: `a6bb101f-22d4-456a-9497-f7fa040b6e5c`
- Video ID: `af2d7adf-b694-4094-8581-036bba9ad2f5`
- Pipeline run ID: `fa872160-df4f-4497-97da-85f142c05b85`
- Audio part: index `0`, `0~900000ms`
- Failure: word index `709`, `raw_start_ms=326640`, `raw_end_ms=163200`, `previous_end_ms=326640`, `next_start_ms=163200`, `reason=overlapping_neighbors`
- Outcome: part 0 and the video failed. Part 1 STT returned 1,906 words and 202 segments, but its result was discarded because the run was already inactive.

`worker-logs.json` contains the structured logs collected for the failure window.

The complete raw Google STT response is not included because the current parser raises before persisting a transcription artifact. Therefore this incident preserves the exact logged failure values, but not `next.end` or the timestamps of later words.
