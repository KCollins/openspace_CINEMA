import os
import re
import time
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional, Tuple, Dict
import requests

CTY_DAT_URL = "https://www.country-files.com/cty/cty.dat"
VAIL_SKIMMERS_URL = "https://vailrerbn.com/api/v1/skimmers"
QRZ_XML_URL = "https://xmldata.qrz.com/xml/current/"


def maidenhead_to_latlon(grid: str) -> Optional[Tuple[float, float]]:
    """Converts a 4-, 6-, or 8-character Maidenhead grid locator to (lat, lon)."""
    if not grid:
        return None
    g = grid.strip().upper()
    if len(g) < 4 or not ("A" <= g[0] <= "R" and "A" <= g[1] <= "R"):
        return None
    if not (g[2].isdigit() and g[3].isdigit()):
        return None

    lon = (ord(g[0]) - ord("A")) * 20.0 - 180.0 + int(g[2]) * 2.0
    lat = (ord(g[1]) - ord("A")) * 10.0 - 90.0 + int(g[3]) * 1.0

    if len(g) >= 6 and ("A" <= g[4] <= "X" and "A" <= g[5] <= "X"):
        lon += (ord(g[4]) - ord("A")) * (5.0 / 60.0) + (2.5 / 60.0)
        lat += (ord(g[5]) - ord("A")) * (2.5 / 60.0) + (1.25 / 60.0)
    else:
        lon += 1.0
        lat += 0.5

    return (lat, lon)


def normalize_callsign(call: str) -> str:
    """Strips skimmer nodes (-1, -#) and portable suffixes (/P, /M, /QRP, /B)."""
    c = call.strip().upper()
    c = re.sub(r"-[0-9#]+$", "", c)
    parts = c.split("/")
    if len(parts) == 1:
        return parts[0]
    # Handle portable indicators like W1AW/P vs DXCC prefix overrides like EA8/DL1ABC
    suffixes = {"P", "M", "MM", "AM", "QRP", "B", "R", "A", "KT", "AE", "AG"}
    if parts[-1] in suffixes or parts[-1].isdigit():
        return parts[0]
    return max(parts, key=len)


class CtyPrefixDatabase:
    """Offline fallback mapping DXCC prefixes and exact overrides to (lat, lon)."""

    def __init__(self, cty_path: str):
        self.cty_path = cty_path
        self.exact_calls: Dict[str, Tuple[float, float]] = {}
        self.prefixes: Dict[str, Tuple[float, float]] = {}
        self._ensure_downloaded()
        self._parse()

    def _ensure_downloaded(self) -> None:
        if os.path.exists(self.cty_path):
            return
        print(f"Downloading offline prefix database ({CTY_DAT_URL})...")
        resp = requests.get(CTY_DAT_URL, timeout=30)
        resp.raise_for_status()
        with open(self.cty_path, "wb") as f:
            f.write(resp.content)

    def _parse(self) -> None:
        with open(self.cty_path, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read()

        # Entries end with ';'
        for entry in raw.split(";"):
            entry = entry.strip()
            if not entry or ":" not in entry:
                continue
            fields = [x.strip() for x in entry.split(":")]
            if len(fields) < 9:
                continue
            try:
                lat = float(fields[4])
                # Note: cty.dat stores West longitudes as positive; negate for standard East+
                lon = -float(fields[5])
            except ValueError:
                continue

            primary_pfx = fields[7].lstrip("*").upper()
            self.prefixes[primary_pfx] = (lat, lon)

            alias_blob = fields[8]
            for token in alias_blob.split(","):
                tok = token.strip()
                if not tok:
                    continue
                # Strip zone overrides like (4)[7] or {NA} or ~-5~
                tok = re.sub(r"\([^)]*\)|\[[^\]]*\]|\{[^}]*\}|~[^~]*~", "", tok).upper()
                if tok.startswith("="):
                    self.exact_calls[tok[1:]] = (lat, lon)
                else:
                    self.prefixes[tok] = (lat, lon)

    def lookup(self, callsign: str, pfx_hint: str = "") -> Optional[Tuple[float, float]]:
        call = callsign.strip().upper()
        if call in self.exact_calls:
            return self.exact_calls[call]

        # Try explicit prefix hint from RBN CSV first (accurate for portable ops)
        if pfx_hint:
            p = pfx_hint.strip().upper()
            for length in range(len(p), 0, -1):
                if p[:length] in self.prefixes:
                    return self.prefixes[p[:length]]

        # Longest-prefix match against callsign
        for length in range(min(len(call), 6), 0, -1):
            sub = call[:length]
            if sub in self.prefixes:
                return self.prefixes[sub]
        return None


class QrzXmlClient:
    """Client for QRZ.com XML Callbook API with automatic session renewal."""

    def __init__(self, username: str, password: str, min_interval_sec: float = 0.08):
        self.username = username
        self.password = password
        self.min_interval_sec = min_interval_sec
        self.session_key: Optional[str] = None
        self.last_request_ts = 0.0
        self.disabled = False

    def _throttle(self) -> None:
        elapsed = time.time() - self.last_request_ts
        if elapsed < self.min_interval_sec:
            time.sleep(self.min_interval_sec - elapsed)
        self.last_request_ts = time.time()

    def _login(self) -> bool:
        if not self.username or not self.password or self.disabled:
            return False
        self._throttle()
        try:
            resp = requests.get(
                QRZ_XML_URL,
                params={"username": self.username, "password": self.password, "agent": "rbn_openspace_1.0"},
                timeout=15,
            )
            root = ET.fromstring(resp.text)
            ns = {"q": "http://xmldata.qrz.com"}
            key_el = root.find(".//q:Session/q:Key", ns)
            err_el = root.find(".//q:Session/q:Error", ns)
            if key_el is not None and key_el.text:
                self.session_key = key_el.text.strip()
                return True
            if err_el is not None:
                print(f"[QRZ] Login warning: {err_el.text}")
                self.disabled = True
        except Exception as exc:
            print(f"[QRZ] Login error: {exc}")
        return False

    def lookup(self, callsign: str) -> Optional[Tuple[float, float, str]]:
        if self.disabled or not self.username or not self.password:
            return None
        if not self.session_key and not self._login():
            return None

        for attempt in range(2):
            self._throttle()
            try:
                resp = requests.get(
                    QRZ_XML_URL,
                    params={"s": self.session_key, "callsign": callsign},
                    timeout=15,
                )
                root = ET.fromstring(resp.text)
                ns = {"q": "http://xmldata.qrz.com"}
                err_el = root.find(".//q:Session/q:Error", ns)
                if err_el is not None and err_el.text:
                    err_msg = err_el.text.lower()
                    if ("session" in err_msg or "invalid" in err_msg) and attempt == 0:
                        self.session_key = None
                        if self._login():
                            continue
                    return None

                call_node = root.find(".//q:Callsign", ns)
                if call_node is None:
                    return None

                lat_el = call_node.find("q:lat", ns)
                lon_el = call_node.find("q:lon", ns)
                grid_el = call_node.find("q:grid", ns)
                grid = grid_el.text.strip() if (grid_el is not None and grid_el.text) else ""

                if lat_el is not None and lon_el is not None and lat_el.text and lon_el.text:
                    return (float(lat_el.text), float(lon_el.text), grid)
                if grid:
                    coords = maidenhead_to_latlon(grid)
                    if coords:
                        return (coords[0], coords[1], grid)
                return None
            except Exception:
                return None
        return None


class StationResolver:
    """
    4-tier station coordinate resolver:
      1. Local SQLite cache
      2. Batch RBN skimmer directory (Maidenhead grids)
      3. QRZ XML API (if credentials supplied)
      4. Offline cty.dat DXCC prefix coordinates
    """

    def __init__(
        self,
        data_dir: str,
        qrz_user: str = "",
        qrz_pass: str = "",
        strict_qrz: bool = False,
    ):
        self.db_path = os.path.join(data_dir, "station_cache.db")
        self.strict_qrz = strict_qrz
        self.mem_cache: Dict[str, Optional[Tuple[float, float]]] = {}
        self.cty = CtyPrefixDatabase(os.path.join(data_dir, "cty.dat"))
        self.qrz = QrzXmlClient(qrz_user, qrz_pass)
        self._init_db()
        self._sync_skimmers()

    def _init_db(self) -> None:
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS stations (
                callsign TEXT PRIMARY KEY,
                lat REAL,
                lon REAL,
                grid TEXT,
                source TEXT,
                updated_at TEXT
            )
            """
        )
        self.conn.commit()
        for row in self.conn.execute("SELECT callsign, lat, lon FROM stations"):
            if row[1] is not None and row[2] is not None:
                self.mem_cache[row[0]] = (float(row[1]), float(row[2]))

    def _save_station(self, callsign: str, lat: float, lon: float, grid: str, source: str) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            """
            INSERT OR REPLACE INTO stations (callsign, lat, lon, grid, source, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (callsign, lat, lon, grid, source, now_iso),
        )
        self.conn.commit()
        self.mem_cache[callsign] = (lat, lon)

    def _sync_skimmers(self) -> None:
        """Pre-loads receiving skimmer coordinates from Vail ReRBN /skimmers."""
        try:
            resp = requests.get(VAIL_SKIMMERS_URL, timeout=15)
            if resp.status_code != 200:
                return
            payload = resp.json()
            skimmers = payload if isinstance(payload, list) else payload.get("skimmers", payload.get("data", []))
            added = 0
            for item in skimmers:
                call = (item.get("callsign") or item.get("call") or "").strip().upper()
                grid = (item.get("grid") or item.get("locator") or "").strip()
                lat = item.get("lat") or item.get("latitude")
                lon = item.get("lon") or item.get("longitude")
                if not call or call in self.mem_cache:
                    continue
                if lat is not None and lon is not None:
                    self._save_station(call, float(lat), float(lon), grid, "rbn_skimmer")
                    added += 1
                elif grid:
                    coords = maidenhead_to_latlon(grid)
                    if coords:
                        self._save_station(call, coords[0], coords[1], grid, "rbn_skimmer")
                        added += 1
            if added:
                print(f"Pre-loaded {added} RBN skimmer locations into cache.")
        except Exception:
            pass

    def resolve(self, raw_callsign: str, pfx_hint: str = "") -> Optional[Tuple[float, float]]:
        raw = raw_callsign.strip().upper()
        if not raw:
            return None

        # 1. Exact cache hit (e.g., specific skimmer node like JH7CSU-1)
        if raw in self.mem_cache:
            return self.mem_cache[raw]

        base = normalize_callsign(raw)
        if base in self.mem_cache:
            return self.mem_cache[base]

        # 2. QRZ XML API lookup
        qrz_res = self.qrz.lookup(base)
        if qrz_res is not None:
            lat, lon, grid = qrz_res
            self._save_station(base, lat, lon, grid, "qrz")
            return (lat, lon)

        if self.strict_qrz:
            self.mem_cache[raw] = None
            return None

        # 3. Offline cty.dat DXCC prefix fallback (do not persist to SQLite so future QRZ runs can upgrade it)
        cty_res = self.cty.lookup(base, pfx_hint=pfx_hint)
        if cty_res is not None:
            self.mem_cache[raw] = cty_res
            return cty_res

        self.mem_cache[raw] = None
        return None

    def close(self) -> None:
        self.conn.close()