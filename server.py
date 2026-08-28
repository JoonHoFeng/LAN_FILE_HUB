#!/usr/bin/env python3
"""邻里盘：Windows SMB 共享目录的多人内网文件门户。"""

import argparse
import json
import mimetypes
import os
import re
import socket
import sqlite3
import subprocess
import threading
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
DATABASE = DATA_DIR / "lan_file_hub.db"
MAX_FILES_PER_SHARE = 1_000
ADMIN_TOKEN = os.environ.get("LAN_FILE_HUB_ADMIN_TOKEN", "")
DB_LOCK = threading.Lock()
HOST_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,252}$")
SHARE_PATTERN = re.compile(r"^[^\\/:*?\"<>|]{1,80}$")


def database():
    DATA_DIR.mkdir(exist_ok=True)
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    connection.execute("""
        CREATE TABLE IF NOT EXISTS shares (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          folder_path TEXT NOT NULL,
          host TEXT NOT NULL DEFAULT '',
          share_name TEXT NOT NULL DEFAULT '',
          username TEXT NOT NULL DEFAULT '',
          description TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL
        )
    """)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(shares)")}
    for column in ("host", "share_name", "username"):
        if column not in columns:
            connection.execute(f"ALTER TABLE shares ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
    return connection


def as_json(handler, payload, status=HTTPStatus.OK):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def error(handler, message, status=HTTPStatus.BAD_REQUEST):
    as_json(handler, {"error": message}, status)


def smb_path(host, share_name):
    return f"\\\\{host}\\{share_name}"


def is_local_host(host):
    """判断 SMB 目标是否就是运行本服务的 Windows 机器。"""
    normalized = host.lower()
    names = {"localhost", "127.0.0.1", socket.gethostname().lower(), os.environ.get("COMPUTERNAME", "").lower()}
    if normalized in names:
        return True
    try:
        addresses = set(socket.gethostbyname_ex(socket.gethostname())[2])
        addresses.update(info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
        return host in addresses
    except socket.gaierror:
        return False


def resolve_folder(folder_path):
    folder = Path(folder_path).expanduser().resolve()
    if not folder.is_dir():
        raise ValueError("无法访问该共享文件夹。请检查 IP、共享名、网络和账号权限。")
    return folder


def validate_share(payload, needs_password):
    host = str(payload.get("host", "")).strip()
    share_name = str(payload.get("shareName", "")).strip()
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))
    description = ""
    if not HOST_PATTERN.fullmatch(host):
        raise ValueError("请输入有效的 IPv4 地址或设备主机名。")
    if not SHARE_PATTERN.fullmatch(share_name):
        raise ValueError("共享文件夹名称不能包含路径分隔符或 Windows 保留字符。")
    if not username or len(username) > 160:
        raise ValueError("请检查用户名长度。")
    if "\\" in username or "/" in username:
        raise ValueError("SMB 用户名只填写 Windows 用户名；系统会自动使用“共享服务器 IP\\用户名”连接。")
    if needs_password and not password:
        raise ValueError("首次添加共享文件夹时必须填写密码。")
    return host, share_name, username, password, description


def save_windows_credential(host, username, password):
    if os.name != "nt":
        raise ValueError("“IP + 用户名 + 密码”直连 SMB 仅支持 Windows 服务器。Linux 请先挂载 SMB 目录后再接入。")
    if not password:
        return
    try:
        result = subprocess.run(
            ["cmdkey", f"/add:{host}", f"/user:{username}", f"/pass:{password}"],
            capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("无法写入 Windows 凭据管理器。") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).replace("\r", " ").replace("\n", " ").strip()[:180]
        raise ValueError(f"无法保存 Windows SMB 凭据。{detail or '请检查 B 机器上的账户名和密码。'}")


def connect_share(host, share_name, username, password):
    try:
        save_windows_credential(host, f"{host}\\{username}", password)
    except ValueError:
        # 访问本机共享时，服务通常已经以目标 Windows 账户运行；此时无需另存凭据。
        if not is_local_host(host):
            raise
    return resolve_folder(smb_path(host, share_name))


def format_size(size):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024


def scan_files(folder):
    """仅读取共享根目录的一级文件和文件夹，不递归扫描子目录。"""
    entries = []
    try:
        for item in folder.iterdir():
            if item.name.startswith(".") or item.is_symlink():
                continue
            try:
                stat = item.stat()
                if item.is_file():
                    entries.append({"path": item.name, "name": item.name, "kind": "file", "size": format_size(stat.st_size), "bytes": stat.st_size, "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()})
                elif item.is_dir():
                    entries.append({"path": item.name, "name": item.name, "kind": "directory", "size": "文件夹", "bytes": 0, "modified": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()})
                if len(entries) >= MAX_FILES_PER_SHARE:
                    return sorted(entries, key=lambda entry: (entry["kind"] != "directory", entry["name"].lower())), True
            except OSError:
                continue
    except OSError:
        return [], False
    return sorted(entries, key=lambda entry: (entry["kind"] != "directory", entry["name"].lower())), False


def create_directory_archive(source):
    """将一级目录打包为 ZIP，保留整个目录结构并忽略符号链接。"""
    with tempfile.NamedTemporaryFile(prefix="lan-file-hub-", suffix=".zip", delete=False) as temporary:
        archive_path = Path(temporary.name)
    try:
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            archive_root = Path(source.name)
            for base, dirs, names in os.walk(source, followlinks=False):
                dirs[:] = [name for name in dirs if not (Path(base) / name).is_symlink()]
                base_path = Path(base)
                relative_base = base_path.relative_to(source)
                if not dirs and not names:
                    archive.writestr(str(archive_root / relative_base).replace("\\", "/") + "/", "")
                for name in names:
                    item = base_path / name
                    if item.is_symlink() or not item.is_file():
                        continue
                    archive.write(item, (archive_root / item.relative_to(source)).as_posix())
        return archive_path
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise


def read_share(row):
    try:
        files, truncated = scan_files(resolve_folder(row["folder_path"]))
        available = True
    except (ValueError, OSError):
        files, truncated, available = [], False, False
    return {"id": row["id"], "name": row["name"], "host": row["host"], "shareName": row["share_name"], "username": row["username"], "networkPath": row["folder_path"], "description": row["description"], "files": files, "available": available, "truncated": truncated}


class AppHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, fmt, *args):
        print(f"[{self.log_date_time_string()}] {self.address_string()} {fmt % args}")

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def read_body(self):
        try:
            return json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode("utf-8"))
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("请求数据格式不正确。")

    def is_admin(self):
        return bool(ADMIN_TOKEN) and self.headers.get("X-Admin-Token", "") == ADMIN_TOKEN

    def require_admin(self):
        if not self.is_admin():
            error(self, "需要管理员口令才能修改共享目录。", HTTPStatus.UNAUTHORIZED)
            return False
        return True

    def list_shares(self):
        with DB_LOCK, database() as connection:
            rows = connection.execute("SELECT * FROM shares ORDER BY created_at DESC").fetchall()
        as_json(self, {"shares": [read_share(row) for row in rows]})

    def find_share(self, share_id):
        with DB_LOCK, database() as connection:
            return connection.execute("SELECT * FROM shares WHERE id = ?", (share_id,)).fetchone()

    def create_share(self):
        if not self.require_admin():
            return
        try:
            payload = self.read_body(); host, share_name, username, password, description = validate_share(payload, True); folder = connect_share(host, share_name, username, password); share_id = uuid.uuid4().hex
            with DB_LOCK, database() as connection:
                connection.execute("INSERT INTO shares (id, name, folder_path, host, share_name, username, description, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (share_id, share_name, str(folder), host, share_name, username, description, datetime.now(timezone.utc).isoformat()))
            as_json(self, {"id": share_id}, HTTPStatus.CREATED)
        except ValueError as exc:
            error(self, str(exc))

    def update_share(self, share_id):
        if not self.require_admin():
            return
        existing = self.find_share(share_id)
        if not existing:
            return error(self, "未找到该共享目录。", HTTPStatus.NOT_FOUND)
        try:
            payload = self.read_body(); host, share_name, username, password, description = validate_share(payload, False)
            if (host != existing["host"] or username != existing["username"]) and not password:
                raise ValueError("修改 IP 或用户名时，请同时填写对应密码。")
            folder = connect_share(host, share_name, username, password)
            with DB_LOCK, database() as connection:
                connection.execute("UPDATE shares SET name = ?, folder_path = ?, host = ?, share_name = ?, username = ?, description = ? WHERE id = ?", (share_name, str(folder), host, share_name, username, description, share_id))
            as_json(self, {"ok": True})
        except ValueError as exc:
            error(self, str(exc))

    def delete_share(self, share_id):
        if not self.require_admin():
            return
        with DB_LOCK, database() as connection:
            result = connection.execute("DELETE FROM shares WHERE id = ?", (share_id,))
        if not result.rowcount:
            return error(self, "未找到该共享目录。", HTTPStatus.NOT_FOUND)
        as_json(self, {"ok": True})

    def download_item(self, share_id, raw_path):
        row = self.find_share(share_id)
        if not row:
            return error(self, "未找到该共享目录。", HTTPStatus.NOT_FOUND)
        try:
            folder = resolve_folder(row["folder_path"]); relative_path = Path(unquote(raw_path))
            if relative_path.is_absolute() or ".." in relative_path.parts or len(relative_path.parts) != 1:
                raise ValueError
            target = (folder / relative_path).resolve(); target.relative_to(folder)
            if target.is_symlink() or not (target.is_file() or target.is_dir()):
                raise ValueError
        except (ValueError, OSError):
            return error(self, "文件不存在或无法访问。", HTTPStatus.NOT_FOUND)

        archive_path = None
        try:
            if target.is_dir():
                archive_path = create_directory_archive(target)
                source, download_name, content_type = archive_path, f"{target.name}.zip", "application/zip"
            else:
                source, download_name, content_type = target, target.name, mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            size = source.stat().st_size
            self.send_response(HTTPStatus.OK); self.send_header("Content-Type", content_type); self.send_header("Content-Length", str(size)); self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(download_name)}"); self.send_header("X-Content-Type-Options", "nosniff"); self.end_headers()
            with source.open("rb") as file:
                while block := file.read(1024 * 1024): self.wfile.write(block)
        except (OSError, BrokenPipeError):
            return
        finally:
            if archive_path:
                archive_path.unlink(missing_ok=True)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/health": return as_json(self, {"ok": True})
        if parsed.path == "/api/shares": return self.list_shares()
        if parsed.path.startswith("/api/files/") and parsed.path.endswith("/download"):
            return self.download_item(parsed.path.split("/")[3], parse_qs(parsed.query).get("path", [""])[0])
        static_files = {"/": "/index.html", "/index.html": "/index.html", "/styles.css": "/styles.css", "/app.js": "/app.js", "/assets/wechat-donation.jpg": "/assets/wechat-donation.jpg"}
        if parsed.path in static_files:
            self.path = static_files[parsed.path]
            return super().do_GET()
        return error(self, "资源不存在。", HTTPStatus.NOT_FOUND)

    def do_HEAD(self):
        self.send_error(HTTPStatus.METHOD_NOT_ALLOWED, "不支持 HEAD 请求")

    def do_POST(self):
        if urlparse(self.path).path == "/api/shares": return self.create_share()
        return error(self, "接口不存在。", HTTPStatus.NOT_FOUND)

    def do_PUT(self):
        parts = urlparse(self.path).path.split("/")
        if len(parts) == 4 and parts[:3] == ["", "api", "shares"]: return self.update_share(parts[3])
        return error(self, "接口不存在。", HTTPStatus.NOT_FOUND)

    def do_DELETE(self):
        parts = urlparse(self.path).path.split("/")
        if len(parts) == 4 and parts[:3] == ["", "api", "shares"]: return self.delete_share(parts[3])
        return error(self, "接口不存在。", HTTPStatus.NOT_FOUND)


def main():
    parser = argparse.ArgumentParser(description="运行邻里盘内网文件门户")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址，默认允许局域网访问")
    parser.add_argument("--port", default=8080, type=int, help="监听端口，默认 8080")
    args = parser.parse_args()
    if not ADMIN_TOKEN: print("警告：未设置 LAN_FILE_HUB_ADMIN_TOKEN，管理接口将保持锁定。")
    if os.name != "nt": print("提示：Linux 服务器不能直接保存 SMB 账号密码；请先挂载 SMB 目录后使用。")
    server = ThreadingHTTPServer((args.host, args.port), AppHandler)
    print(f"邻里盘已启动：http://{args.host}:{args.port}\n按 Ctrl+C 停止服务。")
    try: server.serve_forever()
    except KeyboardInterrupt: print("\n服务已停止。")
    finally: server.server_close()


if __name__ == "__main__": main()
