"""
Seed PostgreSQL with all TransitFlow mock data from train-mock-data/.

Usage:
    python skeleton/seed_postgres.py

Run AFTER docker-compose up -d.
You must first design and create your tables in databases/relational/schema.sql.
Safe to re-run: implement your inserts with ON CONFLICT DO NOTHING.
"""

import json
import os
import sys

import psycopg2
from psycopg2.extras import execute_values
from argon2 import PasswordHasher

# ── resolve paths ────────────────────────────────────────────────────────────
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR    = os.path.join(PROJECT_DIR, "train-mock-data")

sys.path.insert(0, PROJECT_DIR)
from skeleton import config as cfg


def load(filename):
    with open(os.path.join(DATA_DIR, filename), encoding="utf-8") as f:
        return json.load(f)


def connect():
    return psycopg2.connect(
        host=cfg.PG_HOST,
        port=cfg.PG_PORT,
        dbname=cfg.PG_DB,
        user=cfg.PG_USER,
        password=cfg.PG_PASSWORD,
    )


def insert_many(cur, table, columns, rows):
    """Bulk insert with ON CONFLICT DO NOTHING. Returns row count inserted."""
    if not rows:
        return 0
    sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES %s "
        f"ON CONFLICT DO NOTHING"
    )
    execute_values(cur, sql, rows)
    return cur.rowcount


# ── seeders ──────────────────────────────────────────────────────────────────

def seed_metro_stations(cur):
    """
    將捷運站基本資料寫入 metro_stations。

    設計決策：
    - `lines` 欄位不在此處處理，改由 seed_metro_station_lines() 寫入獨立的
      junction table。原因：一個站可能橫跨多條線，若存成陣列則無法走 B-tree
      index，正規化後才能以 WHERE line = ? 有效查詢。
    - `adjacent_stations` 屬於圖關係（邊、距離、方向），存在 Neo4j 管理，
      不重複放入關聯式資料庫，避免雙重維護造成不一致。
    - `interchange_metro_lines` 為冗餘欄位：is_interchange_metro 布林旗標
      已足夠判斷是否為轉乘站，實際線別可從 metro_station_lines JOIN 取得，
      多存一次反而增加資料同步的負擔。
    - interchange_national_rail_station_id 以 .get() 取值，因為大多數捷運站
      無對應國鐵站，允許 NULL 比用空字串更語意清晰且符合 SQL 慣例。
    """
    data = load("metro_stations.json")
    rows = []
    for station in data:
        rows.append((
            station["station_id"],
            station["name"],
            station["is_interchange_metro"],
            station["is_interchange_national_rail"],
            station.get("interchange_national_rail_station_id"),  # nullable：非所有站都有對應國鐵站
        ))

    n = insert_many(cur, "metro_stations",
                    ["station_id", "name", "is_interchange_metro",
                     "is_interchange_national_rail",
                     "interchange_national_rail_station_id"],
                    rows)
    print(f"  metro_stations: {n} rows")


def seed_metro_station_lines(cur):
    """
    將捷運站與路線的對應關係寫入 metro_station_lines（junction table）。

    設計決策：
    - 刻意將線別拆出來成獨立表，而非在 metro_stations 用陣列欄位儲存，
      目的是讓 WHERE line = 'M1' 能走 index，並支援未來新增線別時
      只需 INSERT 新列，不需 UPDATE 原站資料（符合 Open/Closed 原則）。
    - 資料來源與 seed_metro_stations() 共用同一個 JSON 檔（metro_stations.json），
      但刻意拆成兩個函式，確保每個函式只負責一張表，方便單獨重跑除錯。
    """
    data = load("metro_stations.json")
    rows = []
    for station in data:
        for line in station["lines"]:
            rows.append((
                station["station_id"],
                line,
            ))

    n = insert_many(cur, "metro_station_lines",
                    ["station_id", "line"],
                    rows)
    print(f"  metro_station_lines: {n} rows")


def seed_national_rail_stations(cur):
    data = load("national_rail_stations.json")
    # TODO: 尚未實作 — 等待 schema 確認後補齊
    raise NotImplementedError("seed_national_rail_stations: schema 尚未定案，請完成 schema.sql 後實作")


def seed_metro_schedules(cur):
    data = load("metro_schedules.json")
    # TODO: 尚未實作 — 需配合 metro_schedule_stops junction table 一起寫
    raise NotImplementedError("seed_metro_schedules: 須與 seed_metro_schedule_stops() 一起實作")


def seed_national_rail_schedules(cur):
    data = load("national_rail_schedules.json")
    # TODO: 尚未實作 — 需配合 national_rail_schedule_stops junction table 一起寫
    raise NotImplementedError("seed_national_rail_schedules: 須與 seed_national_rail_schedule_stops() 一起實作")


def seed_seat_layouts(cur):
    data = load("national_rail_seat_layouts.json")
    # TODO: 尚未實作
    raise NotImplementedError("seed_seat_layouts: 尚未實作")


def seed_users(cur):
    """
    將使用者基本資料寫入 users 表。密碼與安全問答不在此處處理，
    由 seed_user_credentials() 單獨負責，遵循最小權限原則：
    查詢使用者基本資料的 query 不應接觸到任何憑證欄位。

    設計決策：
    - JSON 來源的 `full_name` 是單一字串，但 schema 刻意拆成 first_name /
      last_name，方便前端分別顯示姓氏、依姓排序，以及未來多語系格式化。
      此處以第一個空格為分隔點做 split，符合英文姓名的常見格式。
    - phone 以 .get() 取值，因為並非所有使用者都有提供手機號碼，
      允許 NULL 比空字串更符合「未填寫」的語意。
    - 密碼欄位（password）完全不接觸，確保明文密碼不會出現在
      users 表或任何非憑證相關的程式路徑中。
    """
    data = load("registered_users.json")
    rows = []
    for user in data:
        # JSON 的 full_name 拆成 first / last，最多切一刀避免中間名被截斷
        parts = user["full_name"].split(" ", 1)
        first_name = parts[0]
        last_name = parts[1] if len(parts) > 1 else ""

        rows.append((
            user["user_id"],
            first_name,
            last_name,
            user["email"],
            user.get("phone"),       # nullable：並非所有用戶都填寫電話
            user["date_of_birth"],
            user["registered_at"],
            user["is_active"],
        ))

    n = insert_many(cur, "users",
                    ["user_id", "first_name", "last_name", "email",
                        "phone", "date_of_birth", "registered_at", "is_active"],
                    rows)
    print(f"  users: {n} rows")


def seed_user_credentials(cur):
    """
    將使用者憑證（密碼雜湊、安全問答雜湊）寫入 user_credentials。

    設計決策：
    - 使用 argon2id 演算法（由 PasswordHasher 預設提供），符合 OWASP 建議的
      密碼儲存標準。argon2 的輸出字串已內嵌 salt、演算法版本與參數，
      因此 schema 不需額外的 salt 欄位，簡化了表結構。
    - JSON 中的 password 為明文（開發用模擬資料），在此處雜湊後才寫入資料庫，
      確保資料庫內永遠不存在明文密碼。
    - secret_answer 同樣以 argon2 雜湊儲存，且比對時區分大小寫，
      這是系統刻意的設計：安全問答作為帳號恢復機制，要求使用者輸入與
      註冊時完全一致的答案，降低暴力猜測的成功率。
    - 此函式使用逐筆 cur.execute() 而非 execute_values() 批次插入，
      原因是 argon2 雜湊計算必須在 Python 端逐筆進行，無法向量化；
      若強行批次組裝 rows list，反而降低可讀性且無效能優勢。
    """
    data = load("registered_users.json")
    ph = PasswordHasher()

    for user in data:
        # 每筆都重新計算雜湊：argon2 每次呼叫產生不同 salt，確保相同密碼
        # 不會產生相同的 hash，防止 rainbow table 攻擊
        password_hash = ph.hash(user["password"])
        secret_answer_hash = ph.hash(user["secret_answer"])

        cur.execute(
            """
            INSERT INTO user_credentials
                (user_id, password_hash, secret_question, secret_answer_hash)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (
                user["user_id"],
                password_hash,
                user["secret_question"],
                secret_answer_hash,
            )
        )

    print(f"  user_credentials: {len(data)} rows")


def seed_national_rail_bookings(cur):
    data = load("bookings.json")
    # TODO: 尚未實作
    raise NotImplementedError("seed_national_rail_bookings: 尚未實作")


def seed_metro_travels(cur):
    data = load("metro_travel_history.json")
    # TODO: 尚未實作
    raise NotImplementedError("seed_metro_travels: 尚未實作")


def seed_payments(cur):
    data = load("payments.json")
    # TODO: 尚未實作
    raise NotImplementedError("seed_payments: 尚未實作")


def seed_feedback(cur):
    data = load("feedback.json")
    # TODO: 尚未實作
    raise NotImplementedError("seed_feedback: 尚未實作")


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    print("Connecting to PostgreSQL...")
    conn = connect()
    conn.autocommit = False
    cur = conn.cursor()

    try:
        print("Seeding tables (dependency order):")
        seed_metro_stations(cur)
        seed_metro_station_lines(cur)
        seed_national_rail_stations(cur)
        seed_metro_schedules(cur)
        seed_national_rail_schedules(cur)
        seed_seat_layouts(cur)
        seed_users(cur)
        seed_user_credentials(cur)
        seed_national_rail_bookings(cur)
        seed_metro_travels(cur)
        seed_payments(cur)
        seed_feedback(cur)
        conn.commit()
        print("\nAll done. Database seeded successfully.")
    except Exception as e:
        conn.rollback()
        print(f"\nError: {e}")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
