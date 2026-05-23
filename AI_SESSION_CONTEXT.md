# AI Session Context — TransitFlow

**How to use this file:**
At the start of every AI coding session, paste the full contents of this file as your first message to your AI assistant. This gives the AI the context it needs to produce code that fits your codebase and is consistent with your teammates' work.

**Who maintains this file:**
Whoever makes a schema change or architectural decision updates this file in the same commit. Treat it like a team contract.

---

## Project Overview

TransitFlow is a Python-based AI chat assistant for a fictional transit operator. It queries three databases — PostgreSQL (relational + vector), Neo4j (graph) — and uses an LLM to answer user questions. Our task as students is to design the database schema and implement the query functions in `databases/relational/queries.py` and `databases/graph/queries.py`.

## Tech Stack

- Language: Python 3.11+
- Relational DB: PostgreSQL via `psycopg2` with `RealDictCursor`
- Graph DB: Neo4j via the `neo4j` Python driver
- Vector search: `pgvector` extension (already implemented — do not modify)
- Web UI: Gradio
- LLM: Google Gemini or local Ollama (configured via `.env`)

## Coding Conventions

- **Naming:** `snake_case` for all Python names and SQL identifiers
- **Docstrings:** All functions must have a docstring with `Args:` and `Returns:` sections
- **Return types:** Use type hints. Read-only functions return `list[dict]` or `Optional[dict]`
- **Empty results:** Return `[]` or `None` (as documented), never raise an exception for "not found"
- **SQL:** Use `%s` placeholders for all user inputs — never string-format into SQL
- **Relational pattern:** Use `_connect()` helper + `psycopg2.extras.RealDictCursor`:
  ```python
  with _connect() as conn:
      with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
          cur.execute("SELECT ...", (param,))
          return [dict(row) for row in cur.fetchall()]
  ```
- **Graph pattern:** Use `_driver()` helper + session:
  ```python
  with _driver() as driver:
      with driver.session() as session:
          result = session.run("MATCH ...", station_id=station_id)
          return [dict(record) for record in result]
  ```

## Agreed Relational Schema

<!-- ============================================================
  FILL THIS IN after your team completes the schema design workshop.
  Paste your final CREATE TABLE statements here.
  ============================================================ -->

```sql
-- TODO: paste your final schema.sql contents here after team review
```

## Agreed Graph Schema

<!-- ============================================================
  FILL THIS IN after your team agrees on Neo4j node labels and
  relationship types.
  ============================================================ -->

```
Node labels:
- TODO

Relationship types:
- TODO

Key properties:
- TODO
```

## Function Signatures We Are Implementing

These are fixed contracts. AI-generated code must match these signatures exactly.

### Relational (`databases/relational/queries.py`)

```python
# Read-only
def query_national_rail_availability(origin_id: str, destination_id: str, travel_date: Optional[str] = None) -> list[dict]: ...
def query_national_rail_fare(schedule_id: str, fare_class: str, stops_travelled: int) -> Optional[dict]: ...
def query_metro_schedules(origin_id: str, destination_id: str) -> list[dict]: ...
def query_metro_fare(schedule_id: str, stops_travelled: int) -> Optional[dict]: ...
def query_available_seats(schedule_id: str, travel_date: str, fare_class: str) -> list[dict]: ...
def query_user_profile(user_email: str) -> Optional[dict]: ...
def query_user_bookings(user_email: str) -> dict: ...  # returns {"national_rail": [...], "metro": [...]}
def query_payment_info(booking_id: str) -> Optional[dict]: ...

# Write operations
def execute_booking(user_id, schedule_id, origin_station_id, destination_station_id, travel_date, fare_class, seat_id, ticket_type="single") -> tuple[bool, dict | str]: ...
def execute_cancellation(booking_id: str, user_id: str) -> tuple[bool, dict | str]: ...

# Auth
def register_user(email, first_name, surname, year_of_birth, password, secret_question, secret_answer) -> tuple[bool, str]: ...
def login_user(email: str, password: str) -> Optional[dict]: ...
def get_user_secret_question(email: str) -> Optional[str]: ...
def verify_secret_answer(email: str, answer: str) -> bool: ...
def update_password(email: str, new_password: str) -> bool: ...
```

### Graph (`databases/graph/queries.py`)

```python
def query_shortest_route(origin_id: str, destination_id: str, network: str = "auto") -> dict: ...
def query_cheapest_route(origin_id: str, destination_id: str, network: str = "auto", fare_class: str = "standard") -> dict: ...
def query_alternative_routes(origin_id, destination_id, avoid_station_id, network="auto", max_routes=3) -> list[list[dict]]: ...
def query_interchange_path(origin_id: str, destination_id: str) -> dict: ...
def query_delay_ripple(delayed_station_id: str, hops: int = 2) -> list[dict]: ...
def query_station_connections(station_id: str) -> list[dict]: ...
```

## Team Decisions Log

<!-- Add entries as you make decisions. Format: "Decision: X. Why: Y." -->

- [ ] Schema design: TODO — add your table/column decisions here
- [ ] Graph schema: TODO — add your node label and relationship type decisions here
- [ ] (example) Metro schedule stop ordering: using `jsonb_array_elements` approach — easier to debug than containment operators

## Prompts That Worked

<!-- Share prompts that produced good output so teammates can reuse them. -->

### Schema design prompt that worked:
```
TODO — add a prompt here after your schema design workshop
```

### Query implementation prompt that worked:
```
TODO — add after implementing your first function
```

—————資料庫專題要注意—————

# TransitFlow 專案開發與資料庫設計規範

## 📌 一、核心設計與商業邏輯 (Business Logic & Core Design)
- **商業規則**：務必徹底確認並遵循專案的 Business Rules。
- **刪除機制**：必須利用軟刪除 (Soft Delete) 處理，嚴禁直接從資料庫實體抹除資料。
- **權限控管**：使用者在「未登入」狀態下，絕對無法撈取任何訂票紀錄。
- **主鍵設計 (PK)**：由團隊自行決定使用 **UUID v7**（建議以 `binary(16)` 儲存以節省空間）或 **Auto Increments** (自動遞增)。
- **驗收標準**：重點在於檢視「回傳內容」是否正確。若因 AI 助理能力不足導致錯誤可忽視。

## 🔒 二、資安與密碼防護 (Security & Credential Management)
- **演算法限制**：嚴禁明碼儲存。Hash 演算法禁止使用 MD5、SHA 系列，強烈建議使用 **argon2id**。
- **獨立資料表**：密碼嚴禁存放在 `user` 資料表中，必須抽離至獨立資料表（如 `user_credentials`）。
  - **欄位規範**：包含 `c_id` (Surrogate Key / 代理鍵)、`u_id` (Foreign Key / 外鍵)、hash、salt。
  - **權限控管**：必須對此表設定嚴格的存取權限，亦可考慮獨立存放在另一個 Schema。
- **Salt 生成**：可用 **CSPRNG** 生成，並於資料庫中設定妥當的字元長度。
- **驗證位置**：Hash 比對與驗證可在 Web Server 端或資料庫端執行。
- **帳號救援**：必須實作設定「秘密問題 (Secret Question)」與答案的比對機制。

## 📁 三、專案檔案配合與實作細節 (Implementation & Configurations)
- **架構優先 (Schema-First)**：先設計好資料表。務必確保 `schema.sql`、`seed_postgres.py` 與 `registered_users.json` 三者的欄位與邏輯完美配合。
- **向量資料庫 (Vector DB)**：`seed_vectors.py` 已經實作完畢，開發時無需修改（目前架構為一個文件對應一個向量）。
- **逾時設定 (Timeout)**：系統預設為 300 秒，如需修改可至 `skeleton/config.py` 中調整。
- **開放空間**：`feedback.json` 目前尚未有具體的實作規範，由團隊自行決定如何應用與發揮。
