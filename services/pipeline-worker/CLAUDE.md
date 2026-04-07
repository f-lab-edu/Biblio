## 테스트 실행

- 테스트는 WSL Ubuntu 터미널에서 Poetry 환경으로 실행한다. anaconda Python 직접 사용 금지.
- 실행 명령: `cd /mnt/c/Users/ASUS/project/Biblio/services/pipeline-worker && poetry run pytest`
- 특정 파일만 실행: `poetry run pytest tests/unit/test_process_video.py -v`
