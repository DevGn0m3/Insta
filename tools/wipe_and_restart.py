"""
Borra TODO (DB + media + thumbnails + logs) para arrancar de cero.
Mismo efecto que el botón "Reset completo" de Salud del Archivo, pero
desde consola, útil si preferís no levantar el servidor primero.

Ejecutar desde la carpeta del proyecto:
  venv\\Scripts\\activate
  python tools/wipe_and_restart.py
"""
import shutil
from pathlib import Path

BASE = Path(__file__).parent.parent
DATA = BASE / "data"

def main():
    targets = [
        DATA / "media",
        DATA / "thumbnails",
        DATA / "db" / "archiver.db",
        DATA / "db" / "archiver.db-wal",
        DATA / "db" / "archiver.db-shm",
        DATA / "settings.json",  # opcional: comentar esta línea si querés
                                   # conservar tu configuración guardada
    ]
    for t in targets:
        if t.is_dir():
            shutil.rmtree(t, ignore_errors=True)
            t.mkdir(parents=True, exist_ok=True)
            print(f"Vaciado: {t}")
        elif t.exists():
            t.unlink()
            print(f"Borrado: {t}")
        else:
            print(f"(no existía) {t}")

    for f in (DATA / "logs").glob("*.log*"):
        f.unlink(missing_ok=True)
        print(f"Borrado log: {f}")

    print("\n✅ Listo. Arrancá el servidor con start.bat y usá '+ Agregar URL'")
    print("   → pestaña 'Importar archivo' para cargar tu .txt con todas las URLs.")

if __name__ == "__main__":
    main()
