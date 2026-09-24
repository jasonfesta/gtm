"""Nonexclusive local audience/role tags with retained assignment history."""

from crm.cli import DEFAULT_DB, connect


class Tags:
    def __init__(self, db=DEFAULT_DB):
        self.db = db

    def list(self, person_id=None):
        with connect(self.db) as conn:
            if person_id is None:
                return [dict(r) for r in conn.execute("SELECT * FROM crm_tags ORDER BY tag_id")]
            self._person(conn, person_id)
            return [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM current_person_tags WHERE person_id=? ORDER BY tag_id",
                    (person_id,),
                )
            ]

    @staticmethod
    def _person(conn, person_id):
        if not conn.execute("SELECT 1 FROM people WHERE person_id=?", (person_id,)).fetchone():
            raise ValueError("unknown person_id")

    def set(self, person_id, tag_id, action, evidence, reviewer):
        if action not in ("add", "remove") or not evidence.strip() or not reviewer.strip():
            raise ValueError("action, supporting evidence/reason and reviewer are required")
        with connect(self.db) as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._person(conn, person_id)
            if not conn.execute("SELECT 1 FROM crm_tags WHERE tag_id=?", (tag_id,)).fetchone():
                raise ValueError("unknown tag_id")
            previous = conn.execute(
                "SELECT action FROM person_tag_events WHERE person_id=? AND tag_id=? "
                "ORDER BY event_id DESC LIMIT 1",
                (person_id, tag_id),
            ).fetchone()
            if (previous and previous[0] == action) or (not previous and action == "remove"):
                return {"changed": False}
            cursor = conn.execute(
                "INSERT INTO person_tag_events(person_id,tag_id,action,evidence,reviewer) "
                "VALUES(?,?,?,?,?)",
                (person_id, tag_id, action, evidence, reviewer),
            )
            return {"changed": True, "event_id": cursor.lastrowid}
