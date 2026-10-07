import os
import sqlite3
import datetime
import logging
from typing import Optional, List, Dict, Any
from app.config import settings

logger = logging.getLogger("miliconfig.database")

def get_writable_db_path() -> str:
    """Find the best writable database path with fallback to /tmp."""
    candidates = []
    
    # 1. Path from DATABASE_URL if sqlite
    db_url = settings.DATABASE_URL or ""
    if db_url.startswith("sqlite:///"):
        p = db_url.replace("sqlite:///", "")
        candidates.append(os.path.abspath(p))
    
    # 2. Local directory
    candidates.append(os.path.abspath("miliconfig.db"))
    
    # 3. Data directory
    candidates.append(os.path.abspath("/data/miliconfig.db"))
    candidates.append(os.path.abspath("/home/miliconfig/data/miliconfig.db"))
    
    # 4. Temp directory (always writable in Linux/Docker)
    candidates.append(os.path.abspath("/tmp/miliconfig.db"))

    for path in candidates:
        try:
            parent = os.path.dirname(path)
            if parent and not os.path.exists(parent):
                try:
                    os.makedirs(parent, exist_ok=True)
                except Exception:
                    continue
            test_conn = sqlite3.connect(path, timeout=3.0)
            test_conn.execute("PRAGMA journal_mode = DELETE;")
            test_conn.execute("CREATE TABLE IF NOT EXISTS _health_check (id INT);")
            test_conn.commit()
            test_conn.close()
            logger.info(f"Using SQLite database at: {path}")
            return path
        except Exception as e:
            logger.warning(f"Database path candidate {path} not writable: {e}")
            continue

    return "/tmp/miliconfig.db"

class Database:
    def __init__(self, db_url: Optional[str] = None):
        self.db_url = db_url or settings.DATABASE_URL
        self.sqlite_path = get_writable_db_path()

    def get_connection(self):
        try:
            conn = sqlite3.connect(self.sqlite_path, check_same_thread=False, timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = DELETE;")
            conn.execute("PRAGMA busy_timeout = 10000;")
            conn.execute("PRAGMA foreign_keys = ON;")
            return conn
        except Exception as e:
            logger.error(f"Failed to connect to primary DB {self.sqlite_path}: {e}. Falling back to /tmp/miliconfig.db")
            self.sqlite_path = "/tmp/miliconfig.db"
            conn = sqlite3.connect(self.sqlite_path, check_same_thread=False, timeout=30.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = DELETE;")
            conn.execute("PRAGMA busy_timeout = 10000;")
            return conn

    def init_schema(self):
        try:
            conn = self.get_connection()
            cur = conn.cursor()
            
            # 1. users
            cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                display_name TEXT NOT NULL,
                uuid TEXT UNIQUE NOT NULL,
                subscription_token TEXT UNIQUE NOT NULL,
                status TEXT DEFAULT 'active',
                expires_at TEXT,
                traffic_limit INTEGER DEFAULT 0,
                upload INTEGER DEFAULT 0,
                download INTEGER DEFAULT 0,
                device_limit INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_seen_at TEXT
            );
            """)
            
            # 2. admins
            cur.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT DEFAULT 'admin',
                created_at TEXT NOT NULL
            );
            """)

            # 3. nodes
            cur.execute("""
            CREATE TABLE IF NOT EXISTS nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                protocol TEXT NOT NULL,
                address TEXT NOT NULL,
                port INTEGER NOT NULL,
                uuid TEXT,
                password TEXT,
                path TEXT DEFAULT '/',
                host TEXT,
                sni TEXT,
                alpn TEXT,
                network TEXT DEFAULT 'ws',
                tls INTEGER DEFAULT 1,
                proxyip TEXT,
                region TEXT DEFAULT 'US',
                enabled INTEGER DEFAULT 1
            );
            """)

            # 4. shadowsocks_credentials
            cur.execute("""
            CREATE TABLE IF NOT EXISTS shadowsocks_credentials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                method TEXT NOT NULL,
                password TEXT NOT NULL,
                server TEXT,
                port INTEGER NOT NULL,
                udp INTEGER DEFAULT 1,
                enabled INTEGER DEFAULT 1,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            """)

            # 5. subscriptions
            cur.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token TEXT NOT NULL,
                format TEXT DEFAULT 'base64',
                accessed_at TEXT,
                user_agent TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            """)

            # 6. traffic_usage
            cur.execute("""
            CREATE TABLE IF NOT EXISTS traffic_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                bytes_uploaded INTEGER DEFAULT 0,
                bytes_downloaded INTEGER DEFAULT 0,
                timestamp TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            """)

            # 7. sessions
            cur.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                client_ip TEXT NOT NULL,
                user_agent TEXT,
                protocol TEXT DEFAULT 'vless',
                connected_at TEXT NOT NULL,
                last_activity TEXT NOT NULL,
                is_active INTEGER DEFAULT 1
            );
            """)

            # 8. audit_logs
            cur.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                admin_id INTEGER,
                event TEXT NOT NULL,
                details TEXT,
                ip TEXT
            );
            """)

            # 9. proxy_ips
            cur.execute("""
            CREATE TABLE IF NOT EXISTS proxy_ips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                address TEXT NOT NULL,
                port INTEGER DEFAULT 443,
                region TEXT DEFAULT 'CF',
                is_active INTEGER DEFAULT 1,
                latency_ms REAL DEFAULT 0.0
            );
            """)

            # 10. regions
            cur.execute("""
            CREATE TABLE IF NOT EXISTS regions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                flag TEXT,
                is_active INTEGER DEFAULT 1
            );
            """)

            # 11. settings
            cur.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                description TEXT
            );
            """)

            # 12. routing_rules
            cur.execute("""
            CREATE TABLE IF NOT EXISTS routing_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                domain_pattern TEXT,
                ip_cidr TEXT,
                port INTEGER,
                protocol TEXT,
                source TEXT,
                outbound TEXT NOT NULL DEFAULT 'direct'
            );
            """)

            # 13. dns_profiles
            cur.execute("""
            CREATE TABLE IF NOT EXISTS dns_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                server_url TEXT NOT NULL,
                protocol TEXT DEFAULT 'doh',
                is_default INTEGER DEFAULT 0
            );
            """)

            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"Error initializing schema: {e}", exc_info=True)

db = Database()

def get_db():
    conn = db.get_connection()
    try:
        yield conn
    finally:
        conn.close()
