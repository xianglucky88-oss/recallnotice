"""Real sqlite3 store: content-hash primary key, INSERT-OR-IGNORE dedup, watermark."""
import json, sqlite3
from .config import DB_PATH

SCHEMA_VERSION = 2

class Store:
    def __init__(self, con=None):
        self.con = con
        if con is not None:
            self._init()

    def connect(self):
        con = sqlite3.connect(DB_PATH)
        self.__init__(con)
        return con

    def _init(self):
        self.con.execute(
            "CREATE TABLE IF NOT EXISTS records("
            "content_hash TEXT PRIMARY KEY, id TEXT, ts TEXT, payload TEXT NOT NULL)")
        self.con.execute(
            "CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)")
        self.con.execute(
            "CREATE TABLE IF NOT EXISTS verdicts("
            "merge_key TEXT PRIMARY KEY, state TEXT NOT NULL, chosen_value TEXT,"
            " basis TEXT, candidates TEXT NOT NULL, support INTEGER,"
            " incomplete INTEGER)")
        self.con.commit()

    @staticmethod
    def hash(record):
        blob = json.dumps(record, sort_keys=True, ensure_ascii=False)
        import hashlib
        return hashlib.sha256(blob.encode()).hexdigest()[:24]

    def upsert(self, record):
        cur = self.con.execute(
            "INSERT OR IGNORE INTO records(content_hash,id,ts,payload) VALUES(?,?,?,?)",
            (self.hash(record), str(record.get("id", "")),
             str(record.get("ts", "")), json.dumps(record, ensure_ascii=False)))
        self.con.commit()
        return cur.rowcount > 0

    def delete(self, content_hash):
        self.con.execute("DELETE FROM records WHERE content_hash=?", (content_hash,))
        self.con.commit()

    def count(self):
        return self.con.execute("SELECT COUNT(*) FROM records").fetchone()[0]

    def all_records(self):
        return [json.loads(p) for (_h, p) in
                self.con.execute("SELECT content_hash,payload FROM records")]

    def all_rows(self):
        return [(h, json.loads(p)) for (h, p) in
                self.con.execute("SELECT content_hash,payload FROM records")]

    def get(self, content_hash):
        row = self.con.execute(
            "SELECT payload FROM records WHERE content_hash=?", (content_hash,)).fetchone()
        return json.loads(row[0]) if row else None

    def watermark(self):
        row = self.con.execute("SELECT v FROM meta WHERE k='watermark'").fetchone()
        return row[0] if row else None

    def set_watermark(self, value):
        self.con.execute(
            "INSERT INTO meta(k,v) VALUES('watermark',?) "
            "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(value),))
        self.con.commit()

    def schema_version(self):
        row = self.con.execute(
            "SELECT v FROM meta WHERE k='schema_version'").fetchone()
        return int(row[0]) if row else 0

    def set_schema_version(self, value):
        self.con.execute(
            "INSERT INTO meta(k,v) VALUES('schema_version',?) "
            "ON CONFLICT(k) DO UPDATE SET v=excluded.v", (str(value),))
        self.con.commit()

    def replace_verdicts(self, verdicts):
        """Atomically replace the adjudication table with *verdicts*."""
        with self.con:
            self.con.execute("DELETE FROM verdicts")
            self.con.executemany(
                "INSERT INTO verdicts(merge_key,state,chosen_value,basis,"
                "candidates,support,incomplete) VALUES(?,?,?,?,?,?,?)",
                [(v["merge_key"], v["state"], v["chosen_value"], v["basis"],
                  json.dumps(v["candidates"], ensure_ascii=False),
                  v["support"], v["incomplete"]) for v in verdicts])

    def verdicts(self):
        out = []
        for row in self.con.execute(
                "SELECT merge_key,state,chosen_value,basis,candidates,support,"
                "incomplete FROM verdicts ORDER BY merge_key"):
            out.append({"merge_key": row[0], "state": row[1],
                        "chosen_value": row[2], "basis": row[3],
                        "candidates": json.loads(row[4]),
                        "support": row[5], "incomplete": row[6]})
        return out

    def reset(self):
        self.con.execute("DELETE FROM records")
        self.con.execute("DELETE FROM meta")
        self.con.execute("DELETE FROM verdicts")
        self.con.commit()
