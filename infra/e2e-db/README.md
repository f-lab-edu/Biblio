# E2E DB

`core-api`, `pipeline-worker`, `search-service`가 함께 사용할 로컬 E2E용 Postgres 이미지입니다.

베이스는 `pgvector/pgvector:pg16`이고, `pgmq`는 공식 SQL-only 설치 방식으로 주입합니다.

## 빌드

```bash
docker build -t biblio-e2e-db infra/e2e-db
```

## 실행

```bash
docker run -d --name biblio-e2e-db \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=app \
  -p 55433:5432 \
  biblio-e2e-db
```

## 확인

```bash
docker exec -it biblio-e2e-db psql -U postgres -d app
```

```sql
SELECT extname FROM pg_extension WHERE extname = 'vector';
SELECT pgmq.create('test_queue');
SELECT pgmq.send('test_queue', '{"hello":"world"}'::jsonb);
SELECT * FROM pgmq.read('test_queue', 30, 1);
```

## 참고

- `pgmq`는 extension 설치가 아니라 SQL-only 설치입니다.
- 따라서 `CREATE EXTENSION pgmq;`가 아니라 `pgmq.*` 함수가 존재하는지로 확인합니다.
- `/docker-entrypoint-initdb.d`는 DB 데이터 디렉터리가 비어 있을 때만 실행됩니다.
