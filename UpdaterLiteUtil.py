# -*- coding: utf-8 -*-
r"""
atualizar_versao_fix_liquibase.py   (rodar no WINDOWS - python 3)

GUI para atualizar a base Sysmo contornando o erro 403 do dbchangelog-latest.xsd.

Causa: o liquibase embarcado (3.5) resolve OFFLINE apenas as versoes de XSD que
empacota (dbchangelog-1.0..3.5 + dbchangelog-ext). O schemaLocation dos changelogs
aponta para 'dbchangelog-latest.xsd', que NAO esta na allowlist offline -> o parser
busca na rede -> liquibase.org devolve 403 -> validacao falha -> update aborta.

Solucao: copiar o build.zip da versao do share, reescrever nos changelogs
'dbchangelog-latest.xsd' -> 'dbchangelog-<maior_versao_empacotada>.xsd' (ex 3.5,
que resolve offline), rezipar no pacote do updater e chamar o updater-lite SEM
argumento de versao (assim ele usa o build.zip patcheado do pacote em vez de
re-baixar do share). O fix vai DENTRO do build.zip, entao a extracao ja sai correta.

Uso: dois cliques no .bat, ou:  python atualizar_versao_fix_liquibase.py
"""
import io
import os
import re
import shutil
import subprocess
import threading
import time
import zipfile
import queue

import tkinter as tk
from tkinter import ttk, messagebox

SHARE_TPL = r"\\192.168.3.5\Versoes S1\build\integracao-continua\pacotes\{branch}\{major}.{minor}\{patch}"
PACOTE = r"C:\sysmo-updater\upload\pacote"
KEEP = {"SysmoUpdaterS1.jar", "UpdaterMicroservices.exe"}
UPDATER = r"C:\SysmoVs\updater-lite.exe"
LOG_DIR = r"C:\sysmo-updater\logs"
PSQL = r"C:\Progra~1\PostgreSQL\17\bin\psql.exe"
INI = r"C:\SysmoVs\dbxconnections.ini"

XSD_DIR_IN_JAR = "liquibase/parser/core/xml/"
MARK = b"dbchangelog-latest.xsd"
XSD_RE = re.compile(r"dbchangelog-(\d+)\.(\d+)\.xsd")
TAG_RE = re.compile(r"<[^>]+>")

# liquibase 4.x instalado: fonte dos XSD permissivos (aceitam atributos novos
# como dataType em createSequence). O liquibase 3.5 do build so empacota ate 3.5
# (estrito) e nao empacota 'latest' -> alem do 403, valida a menos.
LIQ4_JARS = [
    r"C:\SysmoVs\liquibase\bin\internal\lib\liquibase-core.jar",
    r"C:\SysmoVs\liquibase\bin\liquibase.jar",
]


def load_permissive_xsds():
    """Retorna {basename: bytes} de dbchangelog-latest.xsd e dbchangelog-ext.xsd
    de um liquibase 4.x instalado, p/ enriquecer o jar 3.5 do build."""
    want = ("dbchangelog-latest.xsd", "dbchangelog-ext.xsd")
    for jar in LIQ4_JARS:
        if not os.path.isfile(jar):
            continue
        try:
            with zipfile.ZipFile(jar) as z:
                names = {n.split("/")[-1]: n for n in z.namelist()
                         if n.endswith(".xsd")}
                if "dbchangelog-latest.xsd" in names:
                    out = {}
                    for b in want:
                        if b in names:
                            out[b] = z.read(names[b])
                    return out, jar
        except zipfile.BadZipFile:
            continue
    return None, None

# criar processos sem abrir janela de console (Windows)
CREATE_NO_WINDOW = 0x08000000


# ----------------------------- logica de negocio -----------------------------

def find_liquibase_in_zip(zip_src):
    """Localiza dinamicamente o liquibase*.jar dentro do build.zip (o layout muda
    entre versoes/branches). Retorna:
      (jar_name, has_latest, newest_target)
    - jar_name: caminho do jar dentro do zip, ou None se nao achou.
    - has_latest: True se o jar ja empacota dbchangelog-latest.xsd (4.x -> resolve
      offline, NAO precisa de fix).
    - newest_target: 'dbchangelog-<maior>.xsd' empacotado (alvo do rewrite), ou None.
    """
    for info in zip_src.infolist():
        name = info.filename
        if not name.lower().endswith(".jar"):
            continue
        low = name.lower()
        if "liquibase" not in low.split("/")[-1]:
            continue
        try:
            with zipfile.ZipFile(io.BytesIO(zip_src.read(name))) as jz:
                # procura os dbchangelog*.xsd em qualquer path dentro do jar
                xsds = [n for n in jz.namelist()
                        if n.split("/")[-1].startswith("dbchangelog")
                        and n.endswith(".xsd")]
                if not xsds:
                    continue
                has_latest = any(n.endswith("dbchangelog-latest.xsd") for n in xsds)
                best = None
                for n in xsds:
                    m = XSD_RE.search(n)
                    if m:
                        v = (int(m.group(1)), int(m.group(2)))
                        if best is None or v > best[0]:
                            best = (v, "dbchangelog-%d.%d.xsd" % v)
                newest = best[1] if best else None
                return name, has_latest, newest
        except zipfile.BadZipFile:
            continue
    return None, False, None


def clean_pacote():
    if not os.path.isdir(PACOTE):
        os.makedirs(PACOTE)
        return
    for name in os.listdir(PACOTE):
        if name in KEEP:
            continue
        p = os.path.join(PACOTE, name)
        if os.path.isdir(p):
            shutil.rmtree(p, ignore_errors=True)
        else:
            try:
                os.remove(p)
            except OSError:
                pass


def _rebuild_jar_with_xsds(jar_bytes, target_xsd, perm_xsds):
    """Reescreve o liquibase.jar em memoria: troca o conteudo do XSD alvo
    (dbchangelog-3.5.xsd) pelo 'latest' permissivo do 4.x e o dbchangelog-ext.xsd
    pelo ext do 4.x. Assim o resolver offline (que so aceita nomes ate 3.5) passa a
    servir um schema que aceita atributos novos (dataType em createSequence etc)."""
    # mapeia por BASENAME (o path do xsd dentro do jar varia); o target (ex 3.5)
    # recebe o 'latest' permissivo, e o ext recebe o ext permissivo.
    by_base = {}
    if perm_xsds.get("dbchangelog-latest.xsd"):
        by_base[target_xsd] = perm_xsds["dbchangelog-latest.xsd"]
    if perm_xsds.get("dbchangelog-ext.xsd"):
        by_base["dbchangelog-ext.xsd"] = perm_xsds["dbchangelog-ext.xsd"]
    if not by_base:
        return jar_bytes, 0
    src = zipfile.ZipFile(io.BytesIO(jar_bytes))
    out = io.BytesIO()
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            data = by_base.get(info.filename.split("/")[-1])
            if data is not None:
                n += 1
            else:
                data = src.read(info.filename)
            zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            zi.compress_type = info.compress_type
            zi.external_attr = info.external_attr
            dst.writestr(zi, data)
    return out.getvalue(), n


def copy_and_patch(zip_src_path, zip_dst_path, jar_name, target_xsd, perm_xsds,
                   do_fix, progress, cancel):
    target = target_xsd.encode() if target_xsd else b""
    patched = 0
    jar_repl = 0
    with zipfile.ZipFile(zip_src_path) as src, \
         zipfile.ZipFile(zip_dst_path, "w", allowZip64=True) as dst:
        infos = src.infolist()
        # progresso pelo tamanho COMPACTADO (~ tamanho do build.zip, mais intuitivo)
        total = sum(i.compress_size for i in infos) or 1
        done = 0
        last = 0.0
        for info in infos:
            if cancel.is_set():
                raise _Cancelled()
            data = src.read(info.filename)
            if do_fix and info.filename.lower().endswith(".xml") and MARK in data:
                # detecta changelog pelo conteudo (prefixo do schema varia entre builds)
                data = data.replace(MARK, target)
                patched += 1
            elif do_fix and info.filename == jar_name and perm_xsds and target_xsd:
                data, jar_repl = _rebuild_jar_with_xsds(data, target_xsd, perm_xsds)
            zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            zi.compress_type = info.compress_type
            zi.external_attr = info.external_attr
            zi.internal_attr = info.internal_attr
            zi.create_system = info.create_system
            dst.writestr(zi, data)
            done += info.compress_size
            now = time.time()
            if now - last > 0.2:
                progress(done, total, patched)
                last = now
        progress(total, total, patched)
    return patched, jar_repl


def _latest_log():
    if not os.path.isdir(LOG_DIR):
        return None
    logs = [os.path.join(LOG_DIR, f) for f in os.listdir(LOG_DIR)
            if f.lower().endswith(".log")]
    return max(logs, key=os.path.getmtime) if logs else None


def _updater_running():
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq updater-lite.exe", "/NH"],
            stderr=subprocess.DEVNULL, text=True, errors="ignore",
            creationflags=CREATE_NO_WINDOW)
        return "updater-lite.exe" in out
    except Exception:
        return False


def _kill_updater():
    subprocess.call(["taskkill", "/IM", "updater-lite.exe", "/F"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=CREATE_NO_WINDOW)


def read_ini():
    host = base = None
    try:
        with open(INI, "r", encoding="latin-1", errors="ignore") as f:
            for ln in f:
                s = ln.strip()
                low = s.lower()
                if low.startswith("hostname="):
                    host = s.split("=", 1)[1].split(":")[0].strip()
                elif low.startswith("database=") and "/" not in s:
                    base = s.split("=", 1)[1].strip()
    except OSError:
        pass
    return host, base


def query_version():
    host, base = read_ini()
    if not host or not base:
        return None
    try:
        out = subprocess.check_output(
            [PSQL, "-U", "sysdba", "-h", host, "-p", "5432", "-d", base,
             "-t", "-c", "select ver from sgrsis01;"],
            stderr=subprocess.DEVNULL, text=True, errors="ignore",
            creationflags=CREATE_NO_WINDOW)
        return out.strip()
    except Exception:
        return None


class _Cancelled(Exception):
    pass


# --------------------------------- worker ------------------------------------

def worker(version, branch, msgq, cancel):
    def log(t):
        msgq.put(("log", t))

    try:
        parts = version.split(".")
        if len(parts) < 3:
            log("ERRO: versao invalida (use MAJOR.MINOR.PATCH, ex 2.80.03)")
            msgq.put(("done", False))
            return
        major, minor, patch = parts[0], parts[1], parts[2]
        share = SHARE_TPL.format(branch=branch, major=major, minor=minor, patch=patch)
        src_zip = os.path.join(share, "build.zip")
        src_ver = os.path.join(share, "build.ver")
        dst_zip = os.path.join(PACOTE, "build.zip")
        dst_ver = os.path.join(PACOTE, "build.ver")

        log("Versao : %s   Branch: %s" % (version, branch))
        log("Share  : %s" % share)

        if not os.path.isfile(src_zip):
            log("ERRO: build.zip nao encontrado: %s" % src_zip)
            msgq.put(("done", False)); return
        if not os.path.isfile(src_ver):
            log("ERRO: build.ver nao encontrado: %s" % src_ver)
            msgq.put(("done", False)); return

        jar_name = None
        target_xsd = ""
        perm_xsds = {}
        do_fix = False
        log("Analisando o liquibase do build.zip (fix XSD so se necessario)...")
        with zipfile.ZipFile(src_zip) as z:
            jar_name, has_latest, newest = find_liquibase_in_zip(z)
        if jar_name is None:
            log("  liquibase.jar nao encontrado no zip -> copiando sem patch.")
        elif has_latest:
            log("  %s ja empacota dbchangelog-latest.xsd (resolve offline)." % jar_name)
            log("  -> fix DESNECESSARIO nesta versao; copiando sem patch.")
        else:
            do_fix = True
            target_xsd = newest
            log("  jar: %s | maior XSD empacotado: %s" % (jar_name, target_xsd))
            log("  -> fix NECESSARIO (nao tem dbchangelog-latest offline).")
            log("Carregando XSD permissivo de um liquibase 4.x instalado...")
            perm_xsds, perm_src = load_permissive_xsds()
            if perm_xsds:
                log("  -> %s (de %s)" % (", ".join(sorted(perm_xsds)), perm_src))
            else:
                log("  AVISO: liquibase 4.x nao encontrado; o XSD %s (estrito) pode "
                    "rejeitar atributos novos (ex: dataType em createSequence)." % target_xsd)

        log("Limpando pacote (preserva %s)..." % ", ".join(sorted(KEEP)))
        clean_pacote()

        log("Copiando build.ver...")
        shutil.copyfile(src_ver, dst_ver)

        log("Copiando%s build.zip..." % (" + patchando" if do_fix else ""))

        def prog(done, total, patched):
            msgq.put(("prog", done, total, patched))

        n, jar_repl = copy_and_patch(src_zip, dst_zip, jar_name, target_xsd, perm_xsds,
                                     do_fix, prog, cancel)
        if do_fix:
            log("  -> %d changelog(s) reescritos (dbchangelog-latest.xsd -> %s)"
                % (n, target_xsd))
            log("  -> %d XSD(s) do liquibase.jar trocados pelo schema permissivo" % jar_repl)
            if n == 0:
                log("  AVISO: nenhum changelog continha 'dbchangelog-latest.xsd'.")

        if cancel.is_set():
            raise _Cancelled()

        # o build.zip ja esta fechado/completo aqui (o 'with' do zip encerrou).
        sz = os.path.getsize(dst_zip) / (1024.0 * 1024.0)
        log("build.zip finalizado e fechado: %.0f MB (pronto p/ o updater ler)" % sz)

        log("Encerrando instancia anterior do updater-lite...")
        _kill_updater()
        log("Disparando updater-lite (sem versao -> usa o build.zip patcheado)...")
        subprocess.Popen([UPDATER], close_fds=True)

        # acompanha o log do updater ao vivo
        log("--- acompanhando o updater (log ao vivo) ---")
        logf = None
        t0 = time.time()
        while logf is None and time.time() - t0 < 30 and not cancel.is_set():
            logf = _latest_log()
            if logf is None:
                time.sleep(1)
        pos = os.path.getsize(logf) if logf and os.path.isfile(logf) else 0
        idle = 0
        while not cancel.is_set():
            running = _updater_running()
            try:
                size = os.path.getsize(logf) if logf else 0
                if size > pos:
                    with open(logf, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(pos)
                        chunk = f.read()
                        pos = f.tell()
                    for line in chunk.splitlines():
                        line = TAG_RE.sub("", line).rstrip()
                        if line:
                            log(line)
                    idle = 0
            except OSError:
                pass
            if not running:
                idle += 1
                if idle >= 2:
                    break
            time.sleep(1)

        if cancel.is_set():
            _kill_updater()
            raise _Cancelled()

        log("--- updater encerrou ---")
        ver = query_version()
        if ver:
            log("Versao da base agora: %s" % ver)
            ok = ver.startswith(".".join([major, minor, patch]))
            log("RESULTADO: %s" % ("ATUALIZADA ✔" if ok else "NAO atualizou (ver acima)"))
            msgq.put(("done", ok))
        else:
            log("Nao consegui consultar a versao da base (verifique manualmente).")
            msgq.put(("done", True))

    except _Cancelled:
        log("*** CANCELADO pelo usuario ***")
        try:
            if os.path.isfile(os.path.join(PACOTE, "build.zip")):
                os.remove(os.path.join(PACOTE, "build.zip"))
        except OSError:
            pass
        msgq.put(("done", False))
    except Exception as e:
        log("ERRO: %s" % e)
        msgq.put(("done", False))


# ---------------------------------- GUI --------------------------------------

class App:
    def __init__(self, root):
        self.root = root
        self.msgq = queue.Queue()
        self.cancel = threading.Event()
        self.thread = None

        root.title("Updater Lite Util")
        root.geometry("820x480")

        top = ttk.Frame(root, padding=8)
        top.pack(fill="x")
        ttk.Label(top, text="Branch:").grid(row=0, column=0, sticky="w")
        self.e_branch = ttk.Combobox(top, width=12, state="readonly",
                                     values=["Develop", "Release", "Master"])
        self.e_branch.set("Develop")
        self.e_branch.grid(row=0, column=1, padx=(4, 16))
        ttk.Label(top, text="Versão:").grid(row=0, column=2, sticky="w")
        self.e_ver = ttk.Entry(top, width=14)
        self.e_ver.grid(row=0, column=3, padx=(4, 16))
        self.btn_start = ttk.Button(top, text="Iniciar", command=self.on_start)
        self.btn_start.grid(row=0, column=4, padx=4)
        self.btn_cancel = ttk.Button(top, text="Cancelar", command=self.on_cancel,
                                     state="disabled")
        self.btn_cancel.grid(row=0, column=5, padx=4)

        pf = ttk.Frame(root, padding=(8, 0))
        pf.pack(fill="x")
        self.pb = ttk.Progressbar(pf, mode="determinate", maximum=100)
        self.pb.pack(fill="x", side="left", expand=True)
        self.lbl = ttk.Label(pf, text="", width=34)
        self.lbl.pack(side="left", padx=8)

        self.txt = tk.Text(root, wrap="none", height=22)
        self.txt.pack(fill="both", expand=True, padx=8, pady=8)
        self.txt.configure(state="disabled", bg="#101010", fg="#d0d0d0")

        self.e_ver.focus_set()
        self.root.after(100, self.pump)

    def append(self, t):
        self.txt.configure(state="normal")
        self.txt.insert("end", t + "\n")
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def on_start(self):
        if self.thread and self.thread.is_alive():
            return
        ver = self.e_ver.get().strip()
        branch = self.e_branch.get().strip()
        if not ver or not branch:
            messagebox.showwarning(
                "Campos obrigatorios",
                "Informe a Versao (ex: 2.80.03) e selecione a Branch.")
            return
        if len(ver.split(".")) < 3:
            messagebox.showwarning(
                "Versao invalida",
                "Use o formato MAJOR.MINOR.PATCH (ex: 2.80.03).")
            return
        self.cancel.clear()
        self.btn_start.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        self.pb.configure(value=0)
        self.lbl.configure(text="iniciando...")
        ver = self.e_ver.get().strip()
        # combobox mostra capitalizado; o dir do share e minusculo
        branch = (self.e_branch.get().strip() or "develop").lower()
        self.thread = threading.Thread(
            target=worker, args=(ver, branch, self.msgq, self.cancel), daemon=True)
        self.thread.start()

    def on_cancel(self):
        self.cancel.set()
        self.lbl.configure(text="cancelando...")
        self.btn_cancel.configure(state="disabled")

    def pump(self):
        try:
            while True:
                m = self.msgq.get_nowait()
                if m[0] == "log":
                    self.append(m[1])
                elif m[0] == "prog":
                    done, total, patched = m[1], m[2], m[3]
                    frac = (done / total * 100) if total else 100
                    self.pb.configure(value=frac)
                    mb = 1024.0 * 1024.0
                    self.lbl.configure(
                        text="%5.1f%%  %.0f/%.0f MB  %d patch"
                             % (frac, done / mb, total / mb, patched))
                elif m[0] == "done":
                    self.lbl.configure(text="Concluído" if m[1] else "parou")
                    self.btn_start.configure(state="normal")
                    self.btn_cancel.configure(state="disabled")
        except queue.Empty:
            pass
        self.root.after(100, self.pump)


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
