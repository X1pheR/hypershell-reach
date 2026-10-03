import sqlite3

def run_payload(root,run_id):
    with sqlite3.connect(root / "runs.sqlite3") as connection:
        return connection.execute("SELECT payload FROM runs WHERE id=?",(run_id,)).fetchone()[0]

def replace_run_payload(root,run_id,payload):
    with sqlite3.connect(root / "runs.sqlite3") as connection:
        connection.execute("UPDATE runs SET payload=? WHERE id=?",(payload,run_id))
