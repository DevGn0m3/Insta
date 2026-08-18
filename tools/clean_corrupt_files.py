"""
Ejecutar UNA VEZ antes de reiniciar:
  venv\\Scripts\\activate
  python tools/clean_corrupt_files.py
"""
import sqlite3, sys
from pathlib import Path

BASE  = Path(__file__).parent.parent
DB    = BASE/"data"/"db"/"archiver.db"

def is_real_image(p):
    try:
        with open(p,"rb") as f: h=f.read(16)
        if h[:2]==b'\xff\xd8': return True
        if h[:8]==b'\x89PNG\r\n\x1a\n': return True
        if h[:6] in(b'GIF87a',b'GIF89a'): return True
        if h[:4]==b'RIFF' and h[8:12]==b'WEBP': return True
        if h[:2]==b'BM': return True
        if h[4:8]==b'ftyp': return True
        return False
    except: return False

def is_real_video(p):
    try:
        with open(p,"rb") as f: h=f.read(12)
        if h[4:8]==b'ftyp': return True
        if h[4:8] in(b'mdat',b'moov',b'free'): return True
        if h[:4]==b'\x1a\x45\xdf\xa3': return True
        return False
    except: return False

def main():
    if not DB.exists(): print(f"ERROR: DB no encontrada en {DB}"); sys.exit(1)
    conn = sqlite3.connect(DB); conn.row_factory = sqlite3.Row; cur = conn.cursor()
    cur.execute("SELECT id,file_path,file_type FROM media_files WHERE file_type IN ('image','video')")
    rows = cur.fetchall(); deleted_files=0; deleted_db=0; corrupt_posts=set()
    for row in rows:
        path = Path(row["file_path"])
        if not path.exists(): continue
        ok = is_real_image(path) if row["file_type"]=="image" else is_real_video(path)
        if not ok:
            print(f"  CORRUPTO: {path.name} ({path.stat().st_size} bytes)")
            try: path.unlink(); deleted_files+=1
            except Exception as e: print(f"    Error: {e}")
            cur.execute("SELECT post_id FROM media_files WHERE id=?", (row["id"],))
            r=cur.fetchone()
            if r: corrupt_posts.add(r[0])
            cur.execute("DELETE FROM media_files WHERE id=?", (row["id"],))
            deleted_db+=1
    conn.commit()
    print(f"\nArchivos borrados: {deleted_files}")
    print(f"Registros DB eliminados: {deleted_db}")
    print(f"Posts afectados: {len(corrupt_posts)}")
    for pid in corrupt_posts:
        cur.execute("SELECT COUNT(*) FROM media_files WHERE post_id=?", (pid,))
        if cur.fetchone()[0]==0:
            cur.execute("SELECT original_url FROM posts WHERE id=?", (pid,))
            r=cur.fetchone(); url=r[0] if r else "?"
            cur.execute("DELETE FROM post_tags WHERE post_id=?", (pid,))
            cur.execute("DELETE FROM posts WHERE id=?", (pid,))
            print(f"  Post borrado: {url[:80]}")
    conn.commit(); conn.close()
    print("\n✅ Limpieza completada. Reiniciá el servidor.")

if __name__=="__main__": main()
